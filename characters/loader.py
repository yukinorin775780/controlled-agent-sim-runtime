"""
Character Loader Module
Loads character data from YAML files and Jinja2 templates.
"""

import copy
import os
import re
import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from typing import Dict, Any, Optional, List
from core import inventory


def _evaluate_condition(condition: str, value: int) -> bool:
    """
    解析 condition 字符串（如 ">= 80", "< 40"）并用 value 进行数学比较。
    支持 >=, <=, >, <, ==。
    """
    condition = condition.strip()
    match = re.match(r"(>=|<=|>|<|==)\s*(-?\d+)", condition)
    if not match:
        return False
    op, threshold = match.group(1), int(match.group(2))
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == "==":
        return value == threshold
    return False


def _resolve_dynamic_states(
    attributes: Dict[str, Any],
    **kwargs
) -> Dict[str, Dict[str, Any]]:
    """
    预处理 dynamic_states：用 kwargs 覆盖 current_value，计算激活的规则描述。
    返回处理后的 dynamic_states，每个状态包含 name, current_value, active_rule_description。
    """
    raw_states = attributes.get("dynamic_states") or {}
    base_stats = attributes.get("base_stats") or {}

    def _get_value(state_id: str) -> int:
        v = kwargs.get(state_id)
        if v is not None:
            return int(v)
        if state_id == "affection":
            v = kwargs.get("relationship_score")
            if v is not None:
                return int(v)
        v = base_stats.get(state_id, 0)
        return int(v) if v is not None else 0

    resolved = {}
    for state_id, state_def in raw_states.items():
        if not isinstance(state_def, dict):
            continue
        current_value = _get_value(state_id)

        rules = state_def.get("rules") or []
        active_rule_description = ""
        for rule in rules:
            cond = rule.get("condition", "")
            if _evaluate_condition(cond, current_value):
                active_rule_description = rule.get("description", "")
                break

        resolved[state_id] = {
            "name": state_def.get("name", state_id),
            "current_value": current_value,
            "active_rule_description": active_rule_description,
        }
    return resolved


def _resolve_active_story_rules(
    attributes: Dict[str, Any],
    current_flags: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    根据 YAML 中的 story_rules 与当前剧情 flags，收集所有激活的规则描述文本。
    current_flags 中某键为真（truthy）时，追加对应 description 到列表。
    """
    if not current_flags:
        current_flags = {}
    story_rules = attributes.get("story_rules") or {}
    active: List[str] = []
    for rule_key, rule_def in story_rules.items():
        if not isinstance(rule_def, dict):
            continue
        if current_flags.get(rule_key):
            desc = (rule_def.get("description") or "").strip()
            if desc:
                active.append(desc)
    return active


_ABILITY_LOWER = ("str", "dex", "con", "int", "wis", "cha")


def _ensure_ability_scores(attrs: Dict[str, Any]) -> None:
    """补全 persona_template.j2 所需的 ability_scores（兼容仅写在 base_stats 下的简版 YAML）。"""
    scores: Dict[str, int] = {}
    raw = attrs.get("ability_scores")
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                scores[str(k).upper()] = int(v)
            except (TypeError, ValueError):
                continue
    base = attrs.get("base_stats") or {}
    if isinstance(base, dict):
        for k in _ABILITY_LOWER:
            uk = k.upper()
            if uk not in scores and k in base:
                try:
                    scores[uk] = int(base[k])
                except (TypeError, ValueError):
                    pass
    for uk in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        scores.setdefault(uk, 10)
    attrs["ability_scores"] = scores


def _ensure_dialogue_style(attrs: Dict[str, Any]) -> None:
    """补全 dialogue_style.tone / common_phrases（兼容 personality.speech_style 列表写法）。"""
    ds = attrs.get("dialogue_style")
    if isinstance(ds, dict) and ds.get("tone") is not None:
        out = dict(ds)
        cp = out.get("common_phrases")
        if not isinstance(cp, list):
            out["common_phrases"] = [] if cp is None else [str(cp)]
        out.setdefault("tone", "In character.")
        attrs["dialogue_style"] = out
        return

    tone = "In character, stay true to your personality."
    phrases: List[str] = []
    pers = attrs.get("personality") or {}
    ss = pers.get("speech_style")
    if isinstance(ss, list):
        for item in ss:
            if isinstance(item, dict):
                t = item.get("Tone") or item.get("tone")
                p = item.get("Phrases") or item.get("phrases")
                if t is not None:
                    tone = str(t).strip()
                if p is not None:
                    phrases.append(str(p).replace("\n", " ").strip())
            elif isinstance(item, str):
                s = item.strip()
                low = s.lower()
                if low.startswith("tone:"):
                    tone = s.split(":", 1)[1].strip()
                elif low.startswith("phrases:"):
                    phrases.append(s.split(":", 1)[1].strip())
                elif s:
                    phrases.append(s)
    if not phrases:
        phrases = ["—"]
    attrs["dialogue_style"] = {"tone": tone, "common_phrases": phrases}


def normalize_character_attributes_for_template(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    深拷贝 YAML 数据并补全 persona_template.j2 强依赖字段，避免简版角色卡渲染崩溃。
    """
    out = copy.deepcopy(raw)
    _ensure_ability_scores(out)
    _ensure_dialogue_style(out)
    out.setdefault("race", "")
    return out


class CharacterLoader:
    """
    Loads character attributes from YAML files and renders prompts using Jinja2 templates.
    
    Uses relative paths based on the module's location to find character files.
    """
    
    def __init__(self):
        """Initialize the CharacterLoader with the characters directory path."""
        # Get the directory where this module is located
        self.characters_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Initialize Jinja2 environment with the characters directory as template path
        self.jinja_env = Environment(
            loader=FileSystemLoader(self.characters_dir),
            trim_blocks=True,
            lstrip_blocks=True
        )
    
    def load_character(self, name: str) -> Dict[str, Any]:
        """
        Load character data from YAML file and return attributes dictionary.
        
        Args:
            name: Character name (e.g., "analyst")
                The method will look for {name}.yaml in the characters directory.
        
        Returns:
            dict: Character attributes loaded from YAML file
        
        Raises:
            FileNotFoundError: If the YAML file doesn't exist
            yaml.YAMLError: If the YAML file is malformed
        """
        # Construct path to YAML file
        yaml_filename = f"{name}.yaml"
        yaml_path = os.path.join(self.characters_dir, yaml_filename)
        
        # Check if file exists
        if not os.path.exists(yaml_path):
            raise FileNotFoundError(
                f"Character file not found: {yaml_path}\n"
                f"Expected file: {yaml_filename} in {self.characters_dir}"
            )
        
        # Load YAML file
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                attributes = yaml.safe_load(f)
            
            if attributes is None:
                raise ValueError(f"YAML file is empty or contains no data: {yaml_path}")
            
            return attributes
            
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Error parsing YAML file {yaml_path}: {e}")
    
    def load_template(self, name: str, template_path: Optional[str] = None) -> Any:
        """
        Load Jinja2 template for the character.
        
        Args:
            name: Character name (e.g., "analyst")
                The method will look for {name}_persona_template.j2 or persona_template.j2 in the characters directory.
            template_path: Optional explicit template path from YAML (e.g., "prompts/hostile_npc_template.j2").
                If provided, this is tried first before the default fallback chain.
        
        Returns:
            jinja2.Template: Loaded Jinja2 template
        
        Raises:
            TemplateNotFound: If the template file doesn't exist
        """
        # Build candidate list: explicit template_path first, then standard fallbacks
        template_names = []
        if template_path and isinstance(template_path, str) and template_path.strip():
            template_names.append(template_path.strip())
        template_names.append(f"{name}_persona_template.j2")
        template_names.append("persona_template.j2")
        
        for template_name in template_names:
            try:
                template = self.jinja_env.get_template(template_name)
                return template
            except TemplateNotFound:
                continue
        
        # If no template found, raise error
        raise TemplateNotFound(
            f"Template not found for character '{name}'. "
            f"Tried: {', '.join(template_names)}"
        )
    
    def render_prompt(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        Render the prompt template with character attributes.
        
        This method loads the character data, calculates necessary values,
        and renders the Jinja2 template.
        
        Args:
            name: Character name (e.g., "analyst")
            attributes: Optional character attributes dictionary.
                If None, will load from YAML file.
            **kwargs: Additional variables to pass to the template
                (e.g., wis_mod, int_mod, cha_mod, relationship_score, etc.)
        
        Returns:
            str: Rendered prompt string
        """
        # Load attributes if not provided
        if attributes is None:
            attributes = self.load_character(name)
        
        # Load template — respect template_path from YAML if present
        template_path = attributes.get("template_path") if isinstance(attributes, dict) else None
        template = self.load_template(name, template_path=template_path)
        
        # 预处理 dynamic_states：计算激活的规则描述
        dynamic_states = _resolve_dynamic_states(attributes, **kwargs)

        # 剧情标识：story_rules + current_flags（优先 current_flags，否则使用传入的 flags）
        current_flags = kwargs.get("current_flags")
        if current_flags is None:
            current_flags = kwargs.get("flags") or {}
        active_story_rules = _resolve_active_story_rules(attributes, current_flags)
        
        # Prepare template variables
        template_vars = {
            "attributes": attributes,
            "dynamic_states": dynamic_states,
            "active_story_rules": active_story_rules,
            **kwargs,
        }
        # 与 has_healing_potion 同义，避免旧模板误用未定义的 healing_potion（恒为假）
        _hp = bool(template_vars.get("has_healing_potion", False))
        template_vars["has_healing_potion"] = _hp
        template_vars["healing_potion"] = _hp
        
        # Render template
        return template.render(**template_vars)
    
    def get_characters_dir(self) -> str:
        """
        Get the characters directory path.
        
        Returns:
            str: Absolute path to the characters directory
        """
        return self.characters_dir
    
    @staticmethod
    def get_relationship_status(relationship_score: int) -> str:
        """
        Get the relationship status name based on the relationship score.
        
        This is a utility function that converts a numeric relationship score
        into a human-readable status string.
        
        Args:
            relationship_score: The relationship score (range: -100 to 100)
        
        Returns:
            str: The relationship status name with Chinese translation
        
        Examples:
            >>> CharacterLoader.get_relationship_status(85)
            'Devoted (恋人/至死不渝)'
            >>> CharacterLoader.get_relationship_status(0)
            'Neutral (中立)'
            >>> CharacterLoader.get_relationship_status(-60)
            'Hostile (敌对)'
        """
        if relationship_score >= 81:
            return "Devoted (恋人/至死不渝)"
        elif relationship_score >= 41:
            return "Trusting (信赖)"
        elif relationship_score >= 11:
            return "Friendly (友好)"
        elif relationship_score >= -9:
            return "Neutral (中立)"
        elif relationship_score >= -49:
            return "Negative (反感)"
        else:  # relationship_score <= -50
            return "Hostile (敌对)"

# =========================================================================
# 新增：封装一个 Character 对象，让 main.py 调用更简单
# =========================================================================

class Character:
    """
    Represents a loaded character instance.
    Holds the data and provides methods to render prompts.
    """
    def __init__(self, name: str, data: Dict[str, Any], loader: CharacterLoader, quests: Optional[List[Any]] = None):
        self.name = name
        self.data = data
        self.loader = loader
        self.quests = quests if quests is not None else []
        self.inventory = inventory.Inventory()
        
    def render_prompt(
        self,
        relationship_score: int,
        flags: Optional[dict] = None,
        summary: str = "",
        journal_entries: Optional[List[str]] = None,
        inventory_items: Optional[List[str]] = None,
        has_healing_potion: bool = False,
        time_of_day: str = "晨曦 (Morning)",
        hp: int = 20,
        active_buffs: Optional[List[dict]] = None,
        protocol_confidence: Optional[int] = None,
        memory_awakening: Optional[int] = None,
        affection: Optional[int] = None,
    ) -> str:
        """
        Render the system prompt for this character based on current relationship, flags, summary,
        journal entries, and inventory items.
        
        Args:
            relationship_score: Current relationship score with the player
            flags: Dictionary of persistent world-state flags (defaults to empty dict)
            summary: Story summary for context (defaults to empty string)
            journal_entries: Recent journal entries for the AI to remember (defaults to [])
            inventory_items: List of item names the character is holding (defaults to [])
            has_healing_potion: Whether the character has at least one healing_potion (for reality constraints)
        """
        # 注入 relationship，并补全 ability_scores / dialogue_style 等模板必需字段（兼容简版 YAML）
        current_attributes = normalize_character_attributes_for_template(self.data)
        current_attributes["relationship"] = relationship_score

        # Flatten nested 'attributes' subkey for hostile NPC YAMLs (e.g., gatekeeper.yaml)
        # that nest personality/secret_objective under an 'attributes:' key.
        nested_attrs = current_attributes.get("attributes")
        if isinstance(nested_attrs, dict):
            for key in ("personality", "secret_objective", "ability_scores"):
                if key in nested_attrs and key not in current_attributes:
                    current_attributes[key] = nested_attrs[key]

        # Ensure flags is a dict (default to empty)
        if flags is None:
            flags = {}
        if journal_entries is None:
            journal_entries = []
        if inventory_items is None:
            inventory_items = []
        if active_buffs is None:
            active_buffs = []

        return self.loader.render_prompt(
            name=self.name,
            attributes=current_attributes,
            flags=flags,
            summary=summary,
            journal_entries=journal_entries,
            inventory_items=inventory_items,
            has_healing_potion=has_healing_potion,
            time_of_day=time_of_day,
            hp=hp,
            active_buffs=active_buffs,
            protocol_confidence=protocol_confidence,
            memory_awakening=memory_awakening,
            affection=affection,
        )

# =========================================================================
# 新增：对外暴露的快捷函数 (main.py 只需要 import 这个)
# =========================================================================

def load_character(name: str) -> Character:
    """
    Factory function to load a character and return a Character object.
    
    Args:
        name: The name of the character (e.g., "analyst")
    
    Returns:
        Character: An initialized character object
    """
    loader = CharacterLoader()
    data = loader.load_character(name)
    quests_data = data.get('quests', [])
    character = Character(name, data, loader, quests=quests_data)
    
    # Load inventory items（兼容纯字符串与 {id, count} 字典格式）
    inv_data = data.get('inventory', [])
    for item in inv_data:
        if isinstance(item, str):
            character.inventory.add(item, 1)
        elif isinstance(item, dict) and item.get('id'):
            character.inventory.add(item['id'], item.get('count', 1))
    
    return character