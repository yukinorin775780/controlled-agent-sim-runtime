"""
Spell registry for mechanics and intent parsing (YAML-driven).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import yaml


SPELL_DB: Dict[str, Dict[str, Any]] = {}
_SPELL_ALIASES: Dict[str, str] = {}
_LOADED = False


def _default_spell_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "spells.yaml")
    )


def _normalize_lookup_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _rebuild_aliases() -> None:
    _SPELL_ALIASES.clear()
    for spell_id, data in SPELL_DB.items():
        canonical_id = _normalize_lookup_key(spell_id)
        if not canonical_id:
            continue
        _SPELL_ALIASES[canonical_id] = canonical_id
        spell_name = _normalize_lookup_key(data.get("name"))
        if spell_name:
            _SPELL_ALIASES[spell_name] = canonical_id
        for alias in data.get("aliases", []) or []:
            normalized_alias = _normalize_lookup_key(alias)
            if normalized_alias:
                _SPELL_ALIASES[normalized_alias] = canonical_id


def load_spells(filepath: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Load spells from YAML and cache in SPELL_DB.
    """
    global _LOADED
    if _LOADED and not force_reload:
        return SPELL_DB

    target_path = filepath or _default_spell_path()
    if not os.path.exists(target_path):
        SPELL_DB.clear()
        _SPELL_ALIASES.clear()
        _LOADED = True
        return SPELL_DB

    with open(target_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    SPELL_DB.clear()
    if isinstance(raw_data, dict):
        for raw_id, raw_spell in raw_data.items():
            spell_id = _normalize_lookup_key(raw_id)
            if not spell_id or not isinstance(raw_spell, dict):
                continue
            spell_data = dict(raw_spell)
            spell_data.setdefault("name", str(raw_id))
            spell_data.setdefault("level", 0)
            spell_data.setdefault("target", "single")
            spell_data.setdefault("damage", "1d4")
            spell_data.setdefault("saving_throw", "DEX")
            spell_data.setdefault("damage_type", "force")
            if "range" not in spell_data:
                spell_data["range"] = 1
            SPELL_DB[spell_id] = spell_data

    _rebuild_aliases()
    _LOADED = True
    return SPELL_DB


def resolve_spell_id(spell_ref: Any) -> str:
    load_spells()
    normalized_ref = _normalize_lookup_key(spell_ref)
    if not normalized_ref:
        return ""
    return _SPELL_ALIASES.get(normalized_ref, "")


def get_spell_data(spell_id: Any) -> Dict[str, Any]:
    load_spells()
    resolved = resolve_spell_id(spell_id)
    if not resolved:
        return {}
    return dict(SPELL_DB.get(resolved, {}))

