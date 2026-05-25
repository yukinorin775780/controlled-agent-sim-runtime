"""
UI Renderer Module (View Layer)
Handles all Rich/UI rendering - no game logic
"""

import asyncio
import random
from typing import Optional
from rich.align import Align
from rich.console import Console, Group
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.theme import Theme
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich.box import HEAVY, ROUNDED
from core.dice import CheckResult
from core.inventory import Inventory
from core.systems.quest import QuestManager

# 战术面板 NPC 条/边框颜色：按 entity id 稳定映射，不绑定具体角色名
_DASHBOARD_NPC_COLORS = (
    "magenta",
    "red",
    "green",
    "blue",
    "yellow",
    "bright_magenta",
    "cyan",
    "bright_green",
    "bright_blue",
    "bright_cyan",
)


def _dashboard_color_for_entity(ent_id: str) -> str:
    key = (ent_id or "npc").strip().lower()
    if not key:
        return "cyan"
    idx = sum((i + 1) * ord(c) for i, c in enumerate(key)) % len(_DASHBOARD_NPC_COLORS)
    return _DASHBOARD_NPC_COLORS[idx]


class GameRenderer:
    """Handles all UI rendering using Rich library"""
    
    def __init__(self):
        """Initialize the renderer with custom Controlled Agent theme"""
        agent_theme = Theme({
            "info": "dim cyan",
            "warning": "yellow",
            "error": "bold red",
            "success": "bold green",
            "failure": "bold red",
            "critical": "bold yellow reverse blink",
            "npc": "bold purple",
            "player": "bold white",
            "dm": "italic grey50",
            "stat": "bold blue",
            "item": "bold magenta",
        })
        self.console = Console(theme=agent_theme)
    
    def clear_screen(self):
        """Clear the console screen"""
        self.console.clear()
    
    def show_title(self, title_text: str):
        """Display a styled title rule"""
        self.console.print(Rule(f"[bold purple]{title_text}[/bold purple]", style="bold purple"))
        self.console.print()
    
    def _format_inv_display(self, inv) -> str:
        """格式化背包显示，支持 Dict[str,int] 或 list of {id, count}，使用物品数据库中文名及类型颜色"""
        if not inv:
            return "[dim]空无一物[/dim]"
        try:
            from core.systems.inventory import get_registry
            registry = get_registry()
            if isinstance(inv, dict):
                items = [(k, v) for k, v in inv.items() if v > 0]
            else:
                items = [(x.get("id", ""), x.get("count", 0)) for x in inv if x.get("count", 0) > 0]
            lines = []
            for item_id, count in items:
                item_name = registry.get_name(item_id)
                item_data = registry.get(item_id)
                item_type = item_data.get("type", "unknown")
                color = "magenta" if item_type == "quest" else "red" if item_type == "consumable" else "cyan"
                lines.append(f"• [{color}]{item_name}[/{color}]: {count}")
            return "\n".join(lines) if lines else "[dim]空无一物[/dim]"
        except Exception:
            if isinstance(inv, dict):
                return "\n".join([f"• {k}: {v}" for k, v in inv.items() if v > 0]) or "[dim]空无一物[/dim]"
            return "\n".join([f"• {x.get('id', '')}: {x.get('count', 0)}" for x in inv if x.get("count", 0) > 0]) or "[dim]空无一物[/dim]"

    def show_dashboard(self, state: dict):
        """动态渲染多角色战术面板"""
        turn = state.get("turn_count", 0)
        time_str = state.get("time_of_day", "晨曦 (Morning)")
        entities = state.get("entities", {})
        player_data = entities.get("player", {})
        player_hp = player_data.get("hp", 20)
        player_max_hp = player_data.get("max_hp", 20)
        player_inv = player_data.get("inventory") or state.get("player_inventory", {})

        # 解析任务状态：合并所有在场 NPC YAML 中的 quests（无硬编码单一角色）
        flags = state.get("flags", {})
        from characters.loader import load_character

        merged_quests: list = []
        for eid in entities:
            if eid == "player":
                continue
            try:
                char_data = load_character(eid)
                merged_quests.extend(char_data.quests or [])
            except (FileNotFoundError, OSError, ValueError, TypeError):
                continue
        active_quests = QuestManager.check_quests(merged_quests, flags)

        self.print("───────────────────────────────────────────────────── 📊 战术状态面板 ──────────────────────────────────────────────────────")
        self.print(f"[bold cyan]🌍 时间: {time_str} | ⏳ 回合: {turn}[/bold cyan]")

        # 主角 HP 行
        player_hearts_top = "❤️" * max(0, player_hp // 2) + "🤍" * max(0, (player_max_hp - player_hp) // 2)
        self.print(f"[cyan]Player{'':<8}[/cyan] | [bold red]HP: {player_hp}/{player_max_hp} {player_hearts_top}[/bold red] | [bold yellow]Buffs: 无[/bold yellow]")

        for ent_id, ent_data in entities.items():
            if ent_id == "player":
                continue
            hp = ent_data.get("hp", 20)
            max_hp = ent_data.get("max_hp", 20)
            buffs = ent_data.get("active_buffs", [])
            buff_str = ", ".join([f"{b['id']}({b['duration']}t)" for b in buffs]) if buffs else "无"
            hp_bar = "❤️" * max(0, hp // 2) + "🤍" * max(0, (max_hp - hp) // 2)
            name_color = _dashboard_color_for_entity(ent_id)
            self.print(f"[{name_color}]{ent_id.capitalize():<12}[/{name_color}] | [bold red]HP: {hp}/{max_hp} {hp_bar}[/bold red] | [bold yellow]Buffs: {buff_str}[/bold yellow]")

        panels = []
        # 你的背包：仅物品列表，HP 已在顶部全局状态栏显示
        player_inv_text = self._format_inv_display(player_inv)
        panels.append(
            Panel(
                player_inv_text,
                title="🎒 你的背包",
                width=22,
                border_style="cyan",
                box=ROUNDED,
            )
        )

        for ent_id, ent_data in entities.items():
            if ent_id == "player":
                continue
            aff = ent_data.get("affection", 0)
            n_inv = ent_data.get("inventory", {})
            n_inv_str = self._format_inv_display(n_inv)
            content = f"[pink]❤️ 好感度: {aff} / 100[/pink]\n[dim]─[/dim]\n{n_inv_str}"
            name_color = _dashboard_color_for_entity(ent_id)
            panels.append(Panel(content, title=f"📦 {ent_id.capitalize()}", width=22, border_style=name_color))

        self.console.print(Columns(panels))

        # 任务UI构建（显示在背包面板下方）
        quest_text = ""
        if active_quests:
            for q in active_quests:
                status_color = "green" if q["status"] == "COMPLETED" else "yellow"
                quest_text += f"[{status_color}]• {q['title']}[/{status_color}]\n[dim]  {q['stage_description']}[/dim]\n"
        else:
            quest_text = "[dim]暂无活跃任务...[/dim]"

        quest_panel = Panel(quest_text, title="📜 [bold yellow]任务日志[/bold yellow]", border_style="yellow")
        self.console.print(quest_panel)

        # --- 【新增】渲染环境与物体 ---
        loc = state.get("current_location")
        env_objs = state.get("environment_objects") or {}
        if loc:
            self.console.print(f"[bold cyan]📍 当前位置:[/bold cyan] {loc}")
            if env_objs:
                obj_strs = []
                for obj_id, obj_data in env_objs.items():
                    if not isinstance(obj_data, dict):
                        continue
                    name = obj_data.get("name", obj_id)
                    status = obj_data.get("status", "unknown")
                    if status == "locked":
                        status_str = f"[red]({status})[/red] 🔒"
                    elif status == "opened":
                        status_str = f"[green]({status})[/green] 🔓"
                    elif status == "destroyed":
                        status_str = f"[dim]({status})[/dim] 💥"
                    else:
                        status_str = f"({status})"
                    obj_strs.append(f"{name} {status_str}")

                self.console.print(f"[bold yellow]🔍 场景交互物:[/bold yellow] {' | '.join(obj_strs)}")
            else:
                self.console.print("[dim]🔍 场景中没有特别引人注目的物品。[/dim]")
            self.console.print("─" * 120, style="dim")

        self.print("")

    def show_dashboard_legacy(self, player_name: str, npc_name: str, relationship: int, npc_state: dict, active_quests: Optional[list] = None, player_inventory: Optional[Inventory] = None, npc_inventory: Optional[Inventory] = None, journal: Optional[list] = None) -> Group:
        """
        Render the dashboard panels showing game status, quest journal, and recent events.
        
        Args:
            player_name: Player's name
            npc_name: NPC's name
            relationship: Current relationship score
            npc_state: NPC state dict with 'status' and 'duration'
            active_quests: List of active quest objects (optional)
            player_inventory: Player's inventory object (optional)
            npc_inventory: NPC's inventory object (optional)
            journal: List of journal entry strings for recent events (optional)
        
        Returns:
            Group: A Group containing the status panel, quest panel, and journal panel
        """
        # Panel 1: Status Panel
        dashboard_table = Table.grid(padding=(0, 2))
        dashboard_table.add_column(style="stat")
        dashboard_table.add_column(style="npc")
        dashboard_table.add_column(style="stat")
        dashboard_table.add_column(style="warning")
        
        state_name = npc_state.get("status", "NORMAL")
        state_duration = npc_state.get("duration", 0)
        state_display = f"{state_name}"
        if state_duration > 0:
            state_display += f" ({state_duration} turns)"
        
        # Player inventory display
        player_inv_text = "🎒 Inventory: Empty"
        if player_inventory:
            player_inv_text = f"🎒 Inventory: {player_inventory.list_items()}"
        
        # NPC inventory display
        npc_inv_text = ""
        if npc_inventory:
            npc_inv_text = f"🎒 Equipped: {npc_inventory.list_items()}"
        
        dashboard_table.add_row(
            f"Player: [player]{player_name}[/player]",
            f"NPC: [npc]{npc_name}[/npc]",
            f"Relationship: [stat]{relationship}/100[/stat]",
            f"State: [warning]{state_display}[/warning]"
        )
        
        # Add inventory rows
        dashboard_table.add_row(
            player_inv_text,
            npc_inv_text,
            "",
            ""
        )
        
        status_panel = Panel(dashboard_table, title="[bold]Game Status[/bold]", border_style="blue")
        
        # Panel 2: Quest Panel
        if active_quests:
            quest_content = []
            for quest in active_quests:
                quest_title = quest.get("title", "Unknown Quest")
                stage_desc = quest.get("stage_description", "")
                quest_status = quest.get("status", "ACTIVE")
                
                if quest_status == "COMPLETED":
                    # Completed quests: Green checkmark, dimmed text
                    quest_line = f"✅ [bold green]{quest_title}[/bold green]: [dim]{stage_desc}[/dim]"
                else:
                    # Active quests: Fire icon, bright gold text
                    quest_line = f"🔥 [bold gold1]{quest_title}[/bold gold1]: [gold1]{stage_desc}[/gold1]"
                
                quest_content.append(quest_line)
            
            quest_text = "\n".join(quest_content)
        else:
            quest_text = "[dim]No active quests.[/dim]"
        
        quest_panel = Panel(
            quest_text,
            title="📓 QUEST JOURNAL",
            title_align="left",
            border_style="gold1",
            box=HEAVY,
            expand=True
        )
        
        # Panel 3: Recent Journal Events (data from journal.get_recent_entries(3))
        if journal and len(journal) > 0:
            recent = list(journal)
            recent.reverse()  # Newest first for display
            journal_text = "\n".join(f"• {e}" for e in recent)
        else:
            journal_text = "[dim]No major events yet.[/dim]"
        
        journal_panel = Panel(
            journal_text,
            title="📜 Recent Journal Events",
            title_align="left",
            border_style="dim",
            expand=True
        )
        
        return Group(status_panel, quest_panel, journal_panel)
    
    def input_prompt(self, prompt_text: str = "[player]You > [/player]") -> str:
        """
        Get user input with styled prompt.
        
        Args:
            prompt_text: The prompt text to display
        
        Returns:
            str: User input string
        """
        return self.console.input(prompt_text).strip()
    
    def create_spinner(self, text: str, spinner: str = "dots"):
        """
        Create a status spinner context manager.
        
        Args:
            text: Text to display in spinner
            spinner: Spinner style (default: "dots")
        
        Returns:
            Context manager for console.status
        """
        return self.console.status(text, spinner=spinner)
    
    def print_inner_thought(self, thought: str):
        """Display character's inner monologue in dim/italic style."""
        self.console.print(f"[dim italic]💭 *Inner Thought:* {thought}[/dim italic]")
        self.console.print()

    def print_dm_narration(self, text: str):
        """Display DM narration in a styled panel (Amelia Tyler style)."""
        if not text:
            return
        self.console.print(
            Panel(
                text,
                title="[bold yellow]🎙️ Simulation Director[/bold yellow]",
                border_style="yellow",
                width=80,
            )
        )
        self.console.print()

    def print_npc_response(self, name: str, text: str, subtitle: str = ""):
        """
        Display NPC dialogue in a styled panel.
        
        Args:
            name: NPC name
            text: Dialogue text
            subtitle: Optional subtitle (e.g., "Looking at you warily")
        """
        title = f"[npc]{name}[/npc]"
        if subtitle:
            title += f" ({subtitle})"
        
        self.console.print(Panel(
            text,
            title=title,
            style="npc",
            width=80
        ))
        self.console.print()

    async def print_npc_response_stream(self, name: str, text: str, subtitle: str = "", char_delay: float = 0.02):
        """
        异步流式打字机效果：逐字显示 NPC 对话。
        使用 rich.live.Live 实现动态渲染，Panel 样式与 print_npc_response 保持一致。
        
        Args:
            name: NPC name
            text: Dialogue text
            subtitle: Optional subtitle (e.g., "Looking at you warily")
            char_delay: 每个字符的延迟秒数（默认 0.02）
        """
        title = f"[npc]{name}[/npc]"
        if subtitle:
            title += f" ({subtitle})"
        
        displayed = ""
        with Live(console=self.console, refresh_per_second=30) as live:
            for char in text:
                displayed += char
                live.update(Panel(
                    displayed,
                    title=title,
                    style="npc",
                    width=80
                ))
                await asyncio.sleep(char_delay)
        
        self.console.print()
    
    def print_dm_analysis(self, action: str, dc: int):
        """
        Display DM intent analysis result.
        
        Args:
            action: Action type (e.g., "PERSUASION")
            dc: Difficulty class
        """
        self.console.print(f"[dm]🎲 判定意图: [item]{action}[/item] (DC [stat]{dc}[/stat])[/dm]")
    
    def print_roll_result(self, result_dict: dict):
        """
        Display dice roll result with appropriate styling.
        
        Args:
            result_dict: Result dictionary from roll_d20
        """
        result_type = result_dict['result_type']
        
        # Determine result style
        if result_type == CheckResult.CRITICAL_SUCCESS:
            res_style = "critical"
        elif result_type == CheckResult.CRITICAL_FAILURE:
            res_style = "critical"
        elif result_type == CheckResult.SUCCESS:
            res_style = "success"
        else:
            res_style = "failure"
        
        # Print result with styled output
        self.console.print(f"   └─ [{res_style}]{result_dict['log_str']}[/{res_style}]")
        self.console.print()
    
    def print_system_info(self, text: str):
        """Display system information message"""
        self.console.print(f"[info]{text}[/info]")
    
    def print_warning(self, text: str):
        """Display warning message"""
        self.console.print(f"[warning]{text}[/warning]")
    
    def print_error(self, text: str):
        """Display error message"""
        self.console.print(f"[error]{text}[/error]")
    
    def print_state_effect(self, status: str, duration: int, effect_desc: str):
        """
        Display NPC state effect message.
        
        Args:
            status: State status (e.g., "SILENT", "VULNERABLE")
            duration: Remaining duration
            effect_desc: Description of the effect
        """
        if status == "SILENT":
            self.console.print(f"[warning]❄️ 状态: 拒绝交流 (剩余 {duration} 回合)[/warning]")
        elif status == "VULNERABLE":
            self.console.print(f"[warning]✨ 状态: 心防失守 (剩余 {duration} 回合) -> 自动成功！[/warning]")
        else:
            self.console.print(f"[info]💫 状态恢复: {status}[/info]")
            self.console.print()
    
    def print_advantage_alert(self, action_type: str, roll_type: str):
        """
        Display advantage/disadvantage alert.
        
        Args:
            action_type: Action type (e.g., "PERSUASION")
            roll_type: Roll type ('advantage' or 'disadvantage')
        """
        if roll_type == 'advantage':
            self.console.print(f"[warning]🌟 High relationship grants ADVANTAGE on [item]{action_type}[/item]![/warning]")
        elif roll_type == 'disadvantage':
            self.console.print("[warning]💀 Low relationship imposes DISADVANTAGE![/warning]")
    
    def print_situational_bonus(self, bonus: int, reason: str):
        """
        Display situational bonus message.
        
        Args:
            bonus: Bonus amount
            reason: Reason for the bonus
        """
        self.console.print(f"[warning]💍 Situational Bonus: +[stat]{bonus}[/stat] ([item]{reason}[/item])[/warning]")
    
    def print_relationship_change(self, change: int, current: int):
        """
        Display relationship score change.
        
        Args:
            change: Change amount (can be negative)
            current: Current relationship score
        """
        change_str = f"+{change}" if change > 0 else str(change)
        self.console.print(f"[info]💕 关系值变化: [stat]{change_str}[/stat] (当前: [stat]{current}/100[/stat])[/info]")
    
    def print_auto_success(self, action_type: str):
        """Display auto-success message (VULNERABLE state)"""
        self.console.print(f"[success]🎯 Auto-Success: [item]{action_type}[/item] -> [critical]CRITICAL SUCCESS[/critical][/success]")
        self.console.print()

    async def show_dice_roll_animation(self, intent: str, dc: int, modifier: int, roll_data: dict):
        """异步呈现跑团掷骰子的悬念动画"""
        final_roll = roll_data.get("raw_roll", 10)
        total = roll_data.get("total", 10)
        is_success = roll_data.get("is_success", False)
        result_type = roll_data.get("result_type")

        if is_success:
            color = "bold green"
            res_text = "成功 (SUCCESS)"
        else:
            color = "bold red"
            res_text = "失败 (FAILURE)"

        if "CRITICAL" in str(result_type):
            res_text = f"大{res_text}!"
            color = "bold yellow reverse blink"

        self.console.print()
        with Live(console=self.console, refresh_per_second=20) as live:
            # 1. 悬念滚动效果 (Rolling)
            for _ in range(15):
                fake_roll = random.randint(1, 20)
                panel = Panel(
                    Align.center(f"[bold cyan]🎲 正在进行 {intent} 检定...[/bold cyan]\n\n[bold white]D20 掷出: {fake_roll}[/bold white]\n\n[dim]目标 DC: {dc} | 修正值: {modifier:+d}[/dim]"),
                    title="[bold yellow]命运之骰[/bold yellow]",
                    border_style="yellow",
                    width=50
                )
                live.update(panel)
                await asyncio.sleep(0.05)

            # 2. 定格最终裸骰数字 (Reveal)
            panel = Panel(
                Align.center(f"[bold cyan]🎲 {intent} 检定[/bold cyan]\n\n[bold white]D20 掷出: [/bold white][{color}]{final_roll}[/{color}]\n\n[dim]目标 DC: {dc} | 修正值: {modifier:+d}[/dim]"),
                title="[bold yellow]命运之骰[/bold yellow]",
                border_style="yellow",
                width=50
            )
            live.update(panel)
            await asyncio.sleep(0.6)

            # 3. 加上修正值，显示最终判定结果 (Result)
            panel = Panel(
                Align.center(f"[bold cyan]🎲 {intent} 检定[/bold cyan]\n\n[bold white]最终结果: {final_roll} {modifier:+d} = [/bold white][{color}]{total}[/{color}]\n\n[{color}]{res_text}[/{color}]"),
                title="[bold yellow]检定结算[/bold yellow]",
                border_style="green" if is_success else "red",
                width=50
            )
            live.update(panel)
            await asyncio.sleep(0.8)

    def print_action_effect(self, message: str):
        """Display NPC action effect (e.g. using an item)."""
        self.console.print(f"[info]🧪 {message}[/info]")

    def print_critical_state_change(self, result_type: CheckResult, new_status: str, duration: int):
        """
        Display critical roll state change message.
        
        Args:
            result_type: CheckResult enum value
            new_status: New NPC status
            duration: Duration of the new state
        """
        if result_type == CheckResult.CRITICAL_SUCCESS:
            self.console.print(f"[critical]🔥 CRITICAL! She is now VULNERABLE for {duration} turns![/critical]")
        elif result_type == CheckResult.CRITICAL_FAILURE:
            self.console.print(f"[critical]❄️ CRITICAL FAIL! She is now SILENT for {duration} turns![/critical]")
    
    def print_rule(self, text: str, style: str = "info"):
        """Display a horizontal rule"""
        self.console.print(Rule(text, style=style))
        self.console.print()
    
    def print(self, *args, **kwargs):
        """Direct print passthrough to console"""
        self.console.print(*args, **kwargs)
