"""
Mechanics 节点：技能检定与掷骰。
"""

from core.graph.graph_state import GameState
from core.systems import mechanics


def mechanics_node(state: GameState) -> dict:
    """
    根据意图执行技能检定（PERSUASION/DECEPTION/STEALTH/INSIGHT 等）。

    调用 mechanics.execute_skill_check：仅合并 journal_events 与 latest_roll；
    不修改 entities / affection（情感由 DM 与 LLM 决定）。
    """
    intent = state.get("intent", "chat")
    is_probing_secret = state.get("is_probing_secret", False)
    if intent in ["chat", "CHAT", "command_done", "pending", "gift_given", "item_used"] and not is_probing_secret:
        return {}

    print(f"⚙️ Mechanics Node: Processing {intent} (is_probing_secret={is_probing_secret})...")
    normalized_intent = str(intent).strip().upper()
    intent_context = state.get("intent_context") if isinstance(state, dict) else {}
    if isinstance(intent_context, dict) and intent_context.get("gatekeeper_boss_resolution_context"):
        result = mechanics.execute_gatekeeper_boss_resolution_action(state)
    elif normalized_intent == "ATTACK":
        result = mechanics.execute_attack_action(state)
    elif normalized_intent == "SHOVE":
        result = mechanics.execute_shove_action(state)
    elif normalized_intent == "LOOT":
        result = mechanics.execute_loot_action(state)
    elif normalized_intent == "CAST_SPELL":
        result = mechanics.execute_cast_spell_action(state)
    elif normalized_intent in ("USE_ITEM", "CONSUME"):
        result = mechanics.execute_use_item(state)
    elif normalized_intent == "SHORT_REST":
        result = mechanics.execute_short_rest_action(state)
    elif normalized_intent == "LONG_REST":
        result = mechanics.execute_long_rest_action(state)
    elif normalized_intent == "STEALTH":
        result = mechanics.execute_stealth_action(state)
    elif normalized_intent == "EQUIP":
        result = mechanics.execute_equip_action(state)
    elif normalized_intent == "UNEQUIP":
        result = mechanics.execute_unequip_action(state)
    elif normalized_intent in ("MOVE", "APPROACH"):
        result = mechanics.execute_move_action(state)
    elif normalized_intent == "TRIGGER_TRAP":
        result = mechanics.execute_trigger_trap_action(state)
    elif normalized_intent == "INTERACT":
        intent_context = state.get("intent_context") if isinstance(state, dict) else {}
        source = ""
        target = ""
        if isinstance(intent_context, dict):
            source = str(intent_context.get("source") or "").strip().lower()
            target = str(intent_context.get("action_target") or "").strip().lower()
        if source == "trap_trigger" or target == "gas_trap_1":
            result = mechanics.execute_trigger_trap_action(state)
        else:
            result = mechanics.execute_interact_action(state)
    elif normalized_intent == "DISARM":
        result = mechanics.execute_disarm_action(state)
    elif normalized_intent == "UNLOCK":
        result = mechanics.execute_unlock_action(state)
    elif normalized_intent in ("END_TURN", "PASS_TURN", "WAIT_TURN"):
        result = mechanics.execute_end_turn_action(state)
    else:
        result = mechanics.execute_skill_check(state)

    advanced_result = mechanics.advance_combat_after_action(state, result)
    if isinstance(advanced_result, dict):
        result = advanced_result

    # Ensure enemy blocks fully resolve before returning to the frontend.
    while True:
        entities = result.get("entities") if isinstance(result.get("entities"), dict) else state.get("entities") or {}
        combat_active = bool(result.get("combat_active", state.get("combat_active", False)))
        initiative_order = list(result.get("initiative_order") or state.get("initiative_order") or [])
        current_turn_index = result.get("current_turn_index", state.get("current_turn_index", 0))
        if not combat_active or not initiative_order:
            break
        side = mechanics._active_block_side(
            state={**state, **result},
            entities=entities,
            initiative_order=initiative_order,
            current_turn_index=current_turn_index,
        )
        if side != "hostile":
            break
        advanced_result = mechanics.advance_combat_after_action(state, result)
        if not isinstance(advanced_result, dict) or advanced_result == result:
            break
        result = advanced_result

    journal_events = list(result.get("journal_events", []))
    if journal_events:
        deduped: list[str] = []
        for line in journal_events:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        journal_events = deduped

    out: dict = {"journal_events": journal_events}
    if "raw_roll_data" in result:
        out["latest_roll"] = result["raw_roll_data"]
    if "entities" in result:
        out["entities"] = result["entities"]
    if "player_inventory" in result:
        out["player_inventory"] = result["player_inventory"]
    if "environment_objects" in result:
        out["environment_objects"] = result["environment_objects"]
    if "flags" in result:
        out["flags"] = result["flags"]
    if "combat_active" in result:
        out["combat_active"] = result["combat_active"]
    if "combat_phase" in result:
        out["combat_phase"] = result["combat_phase"]
    if "initiative_order" in result:
        out["initiative_order"] = result["initiative_order"]
    if "current_turn_index" in result:
        out["current_turn_index"] = result["current_turn_index"]
    if "turn_resources" in result:
        out["turn_resources"] = result["turn_resources"]
    if "recent_barks" in result:
        out["recent_barks"] = result["recent_barks"]
    if "map_data" in result:
        out["map_data"] = result["map_data"]
    if "pending_events" in result:
        out["pending_events"] = result["pending_events"]
    if "demo_cleared" in result:
        out["demo_cleared"] = bool(result["demo_cleared"])
    return out
