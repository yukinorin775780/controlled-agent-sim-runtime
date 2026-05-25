"""
基础战斗系统保护性测试。
锁定初始怪物注入、攻击检定、扣血与死亡状态更新。
"""

from unittest.mock import patch

from core.graph.nodes.mechanics import mechanics_node
from core.systems import mechanics
from core.systems.inventory import init_registry
from core.systems.world_init import get_initial_world_state

init_registry("config/items.yaml")

INITIATIVE_PLAYER_FIRST = [10, 12, 9, 8, 20]
PLAYER_HIT_ENEMY_COUNTER = INITIATIVE_PLAYER_FIRST + [14, 4, 15, 3]


def _deactivate_additional_enemies(state):
    for enemy_id in ("drone_sentinel", "drone_support"):
        enemy = state.get("entities", {}).get(enemy_id)
        if isinstance(enemy, dict):
            enemy["status"] = "dead"
            enemy["hp"] = 0
            enemy["loot_generated"] = True


def test_initial_world_state_includes_drone_sparring_target():
    state = get_initial_world_state()

    player = state["entities"]["player"]
    drone = state["entities"]["drone_1"]
    iron_chest = state["environment_objects"]["iron_chest"]

    assert player["x"] == 4
    assert player["y"] == 9
    assert player["equipment"] == {"main_hand": None, "ranged": None, "armor": None}
    assert player["status_effects"] == []
    assert player["speed"] == 30
    assert player["ability_scores"]["DEX"] == 10

    assert drone == {
        "name": "训练无人机",
        "faction": "hostile",
        "ability_scores": {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        "speed": 30,
        "hp": 7,
        "max_hp": 7,
        "ac": 15,
        "status": "alive",
        "inventory": {
            "gold_coin": 5,
            "scimitar": 1,
        },
        "equipment": {"main_hand": "scimitar", "ranged": None, "armor": None},
        "position": "camp_center",
        "x": 4,
        "y": 3,
        "active_buffs": [],
        "status_effects": [],
        "affection": 0,
    }
    assert iron_chest["x"] == 6
    assert iron_chest["y"] == 2
    assert state["map_data"]["id"] == "training_range"
    assert state["map_data"]["width"] == 15
    assert state["map_data"]["height"] == 15
    assert [5, 5] in state["map_data"]["blocked_movement_tiles"]
    assert state["combat_active"] is False
    assert state["initiative_order"] == []
    assert state["current_turn_index"] == 0
    assert state["turn_resources"] == {}


def test_initial_world_state_includes_archer_and_shaman_enemies():
    state = get_initial_world_state()

    archer = state["entities"]["drone_sentinel"]
    shaman = state["entities"]["drone_support"]

    assert archer["faction"] == "hostile"
    assert archer["equipment"]["ranged"] == "shortbow"
    assert archer["hp"] == 5
    assert archer["status"] == "alive"

    assert shaman["faction"] == "hostile"
    assert shaman["enemy_type"] == "shaman"
    assert shaman["spell_slots"]["level_1"] == 1
    assert "healing_word" in str(shaman.get("spells", {})).lower()


def test_initial_world_state_includes_powder_barrels():
    state = get_initial_world_state()
    barrel_1 = state["entities"]["powder_barrel_1"]
    barrel_2 = state["entities"]["powder_barrel_2"]

    assert barrel_1["entity_type"] == "powder_barrel"
    assert barrel_1["hp"] == 10
    assert barrel_1["status"] == "alive"
    assert barrel_2["entity_type"] == "powder_barrel"
    assert [7, 3] in state["map_data"]["blocked_movement_tiles"]
    assert [8, 3] in state["map_data"]["blocked_movement_tiles"]


def test_initial_world_state_includes_hidden_trap_and_locked_chest():
    state = get_initial_world_state()

    trap = state["entities"]["trap_tripwire_1"]
    locked_chest = state["environment_objects"]["locked_chest"]

    assert trap["entity_type"] == "trap"
    assert trap["is_hidden"] is True
    assert trap["detect_dc"] == 13
    assert trap["disarm_dc"] == 15
    assert trap["damage"] == "2d6"
    assert trap["trigger_radius"] == 0

    assert locked_chest["is_locked"] is True
    assert locked_chest["unlock_dc"] == 14
    assert locked_chest["status"] == "locked"


def test_character_yaml_equipment_is_mapped_to_equipment_slots():
    state = get_initial_world_state()

    analyst = state["entities"]["analyst"]
    scout = state["entities"]["scout"]

    assert analyst["equipment"]["main_hand"] == "mace"
    assert analyst["equipment"]["armor"] == "scale_mail"
    assert scout["equipment"]["main_hand"] == "rusty_dagger"
    assert scout["equipment"]["ranged"] == "shortbow"


@patch("core.systems.mechanics.random.randint", side_effect=PLAYER_HIT_ENEMY_COUNTER)
def test_attack_mechanics_hit_and_apply_damage(mock_randint):
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    drone = result["entities"]["drone_1"]
    attack_log = result["journal_events"][0]
    latest_roll = result["latest_roll"]

    assert drone["hp"] == 3
    assert drone["status"] == "alive"
    assert result["entities"]["player"]["hp"] == 20
    assert "玩家 使用 徒手打击 对 训练无人机 发起攻击" in attack_log
    assert "命中检定: 14(+4) = 18 vs AC 15，命中" in attack_log
    assert "造成 4 点伤害" in attack_log
    assert "伤害骰: 1d4[掷出 4]" in attack_log
    assert latest_roll["intent"] == "ATTACK"
    assert latest_roll["target"] == "drone_1"
    assert latest_roll["weapon"] == "unarmed"
    assert latest_roll["damage"]["formula"] == "1d4"
    assert latest_roll["damage"]["total"] == 4
    assert latest_roll["result"]["is_success"] is True
    assert latest_roll["result"]["total"] == 18
    assert result["combat_active"] is True
    assert result["initiative_order"] == ["player", "drone_1", "scout", "analyst", "tactician"]
    assert result["current_turn_index"] == 0
    assert "⚔️ 战斗开始！先攻顺序：" in "\n".join(result["journal_events"])
    assert "训练无人机 使用 弯刀 对 玩家 发起攻击" not in "\n".join(result["journal_events"])
    assert result["turn_resources"]["player"]["action"] == 0


@patch("core.systems.mechanics.random.randint", side_effect=PLAYER_HIT_ENEMY_COUNTER)
def test_attack_ignores_llm_weapon_hint_when_not_equipped(mock_randint):
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state["player_inventory"] = {"scimitar": 1}
    state["entities"]["player"]["equipment"] = {"main_hand": None, "ranged": None, "armor": None}
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
                "weapon": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["hp"] == 3
    assert result["player_inventory"] if "player_inventory" in result else state["player_inventory"] == {"scimitar": 1}
    assert result["latest_roll"]["weapon"] == "unarmed"
    assert result["latest_roll"]["weapon_name"] == "徒手打击"
    assert result["latest_roll"]["damage"]["formula"] == "1d4"
    assert result["latest_roll"]["damage"]["rolls"] == [4]
    assert result["latest_roll"]["damage"]["modifier"] == 0
    assert result["latest_roll"]["damage"]["total"] == 4


@patch("core.systems.mechanics.random.randint", side_effect=INITIATIVE_PLAYER_FIRST + [18, 4])
def test_attack_mechanics_marks_defender_dead_on_zero_hp(mock_randint):
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state["entities"]["drone_1"]["hp"] = 4
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    drone = result["entities"]["drone_1"]

    assert drone["hp"] == 0
    assert drone["status"] == "dead"
    assert "训练无人机 倒下了" in "\n".join(result["journal_events"])
    assert result["combat_active"] is False
    assert result["initiative_order"] == []
    assert "敌对单位已经被肃清" in result["journal_events"][-1]


@patch(
    "core.systems.mechanics._generate_bark_for_event",
    return_value={
        "entity": "player",
        "entity_name": "玩家",
        "event_type": "CRITICAL_HIT",
        "target": "训练无人机",
        "text": "漂亮一击",
    },
)
@patch("core.systems.mechanics.random.randint", side_effect=[20, 2])
def test_execute_combat_attack_generates_bark_on_critical_hit(mock_randint, mock_generate_bark):
    attacker = {
        "id": "player",
        "name": "玩家",
        "ability_scores": {"STR": 10, "DEX": 10},
        "equipment": {"main_hand": None, "ranged": None, "armor": None},
        "x": 4,
        "y": 4,
    }
    defender = {
        "id": "drone_1",
        "name": "训练无人机",
        "ac": 10,
        "hp": 20,
        "max_hp": 20,
        "status": "alive",
        "x": 4,
        "y": 3,
    }

    result = mechanics.execute_combat_attack(attacker=attacker, defender=defender)

    assert len(result["recent_barks"]) == 1
    assert result["recent_barks"][0]["event_type"] == "CRITICAL_HIT"
    assert '💬 [台词] 玩家: "漂亮一击"' in "\n".join(result["journal_events"])
    assert defender["hp"] == 18
    assert defender["status"] == "alive"
    assert mock_generate_bark.called


@patch(
    "core.systems.mechanics._generate_bark_for_event",
    return_value={
        "entity": "player",
        "entity_name": "玩家",
        "event_type": "CRITICAL_MISS",
        "target": "训练无人机",
        "text": "失手了",
    },
)
@patch("core.systems.mechanics.random.randint", side_effect=[1])
def test_execute_combat_attack_generates_bark_on_critical_miss(mock_randint, mock_generate_bark):
    attacker = {
        "id": "player",
        "name": "玩家",
        "ability_scores": {"STR": 10, "DEX": 10},
        "equipment": {"main_hand": None, "ranged": None, "armor": None},
        "x": 4,
        "y": 4,
    }
    defender = {
        "id": "drone_1",
        "name": "训练无人机",
        "ac": 15,
        "hp": 7,
        "max_hp": 7,
        "status": "alive",
        "x": 4,
        "y": 3,
    }

    result = mechanics.execute_combat_attack(attacker=attacker, defender=defender)

    assert len(result["recent_barks"]) == 1
    assert result["recent_barks"][0]["event_type"] == "CRITICAL_MISS"
    assert '💬 [台词] 玩家: "失手了"' in "\n".join(result["journal_events"])
    assert defender["hp"] == 7
    assert defender["status"] == "alive"
    assert mock_generate_bark.called


def test_equip_action_moves_weapon_from_inventory_to_equipment():
    state = get_initial_world_state()
    state["player_inventory"] = {"scimitar": 1, "healing_potion": 2}
    state.update(
        {
            "intent": "EQUIP",
            "intent_context": {
                "action_actor": "player",
                "item_id": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {"healing_potion": 2}
    assert result["entities"]["player"]["equipment"] == {"main_hand": "scimitar", "ranged": None, "armor": None}
    assert "玩家 装备了 弯刀" in result["journal_events"][0]
    assert result["latest_roll"]["intent"] == "EQUIP"
    assert result["latest_roll"]["result"]["is_success"] is True


def test_unequip_action_moves_weapon_back_to_inventory():
    state = get_initial_world_state()
    state["player_inventory"] = {}
    state["entities"]["player"]["equipment"] = {"main_hand": "scimitar", "ranged": None, "armor": None}
    state.update(
        {
            "intent": "UNEQUIP",
            "intent_context": {
                "action_actor": "player",
                "item_id": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {"scimitar": 1}
    assert result["entities"]["player"]["equipment"] == {"main_hand": None, "ranged": None, "armor": None}
    assert "玩家 卸下了 弯刀" in result["journal_events"][0]


def test_equip_action_consumes_action_in_combat():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["combat_phase"] = "IN_COMBAT"
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["player_inventory"] = {"scimitar": 1}
    state.update(
        {
            "intent": "EQUIP",
            "intent_context": {
                "action_actor": "player",
                "item_id": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["equipment"]["main_hand"] == "scimitar"
    assert result["player_inventory"] == {}
    assert result["turn_resources"]["player"]["action"] == 0
    assert result["turn_resources"]["player"]["bonus_action"] == 1


def test_equip_action_rejects_when_no_action_in_combat():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["combat_phase"] = "IN_COMBAT"
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 0, "bonus_action": 1, "movement": 6}}
    state["player_inventory"] = {"scimitar": 1}
    state.update(
        {
            "intent": "EQUIP",
            "intent_context": {
                "action_actor": "player",
                "item_id": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "动作资源不足" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["result_type"] == "NO_ACTION"
    assert result["entities"]["player"]["equipment"]["main_hand"] is None
    assert result["player_inventory"] == {"scimitar": 1}


def test_unequip_action_consumes_action_in_combat():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["combat_phase"] = "IN_COMBAT"
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["player_inventory"] = {}
    state["entities"]["player"]["equipment"] = {"main_hand": "scimitar", "ranged": None, "armor": None}
    state.update(
        {
            "intent": "UNEQUIP",
            "intent_context": {
                "action_actor": "player",
                "item_id": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["equipment"]["main_hand"] is None
    assert result["player_inventory"] == {"scimitar": 1}
    assert result["turn_resources"]["player"]["action"] == 0


def test_stealth_enters_hidden_in_out_of_combat():
    state = get_initial_world_state()
    state.update(
        {
            "intent": "STEALTH",
            "intent_context": {
                "action_actor": "scout",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    effects = result["entities"]["scout"]["status_effects"]
    assert any(effect.get("type") == "hidden" for effect in effects)
    assert result.get("combat_active", False) is False
    assert "进入潜行状态" in "\n".join(result["journal_events"])


def test_stealth_move_then_ambush_applies_surprised_and_skips_enemy_turn():
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)

    state.update(
        {
            "intent": "STEALTH",
            "intent_context": {"action_actor": "scout"},
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )
    stealth_result = mechanics_node(state)
    assert any(effect.get("type") == "hidden" for effect in stealth_result["entities"]["scout"]["status_effects"])

    move_state = {
        **state,
        **stealth_result,
        "intent": "MOVE",
        "intent_context": {
            "action_actor": "scout",
            "action_target": "drone_1",
        },
    }
    with patch("core.systems.mechanics.random.randint", return_value=18):
        move_result = mechanics_node(move_state)

    assert move_result.get("combat_active", False) is False
    assert "潜行对抗" in "\n".join(move_result["journal_events"])
    assert any(effect.get("type") == "hidden" for effect in move_result["entities"]["scout"]["status_effects"])

    attack_state = {
        **move_state,
        **move_result,
        "intent": "ATTACK",
        "intent_context": {
            "action_actor": "scout",
            "action_target": "drone_1",
        },
        "user_input": "侦察员攻击训练无人机",
    }
    with (
        patch("core.systems.mechanics.random.randint", side_effect=[8, 7, 18, 6, 5, 4, 16]),
        patch("core.systems.mechanics.parse_dice_string", return_value=3),
    ):
        attack_result = mechanics_node(attack_state)

    assert attack_result.get("combat_active", False) is True
    assert attack_result["latest_roll"]["result"]["advantage"] is True
    assert len(attack_result["latest_roll"]["result"]["rolls"]) == 2
    assert not any(effect.get("type") == "hidden" for effect in attack_result["entities"]["scout"]["status_effects"])
    assert any(effect.get("type") == "surprised" for effect in attack_result["entities"]["drone_1"]["status_effects"])
    assert "潜袭" in "\n".join(attack_result["journal_events"])

    enemy_turn_result = mechanics.execute_enemy_turn(
        "drone_1",
        {
            **attack_state,
            **attack_result,
        },
    )
    drone_resources = enemy_turn_result["turn_resources"]["drone_1"]
    assert drone_resources["action"] == 0
    assert drone_resources["movement"] == 0
    assert not any(
        effect.get("type") == "surprised"
        for effect in enemy_turn_result["entities"]["drone_1"]["status_effects"]
    )
    assert "受惊" in "\n".join(enemy_turn_result["journal_events"])


@patch("core.systems.mechanics.random.randint", side_effect=PLAYER_HIT_ENEMY_COUNTER)
def test_equipped_weapon_attack_uses_weapon_damage_and_range_auto_approach(mock_randint):
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state["player_inventory"] = {}
    state["entities"]["player"]["equipment"] = {"main_hand": "scimitar", "ranged": None, "armor": None}
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    drone = result["entities"]["drone_1"]
    player = result["entities"]["player"]
    assert drone["hp"] == 3
    assert player["x"] == 4
    assert player["y"] == 4
    assert player["position"] == "靠近 训练无人机"
    assert "造成 4 点伤害" in result["journal_events"][0]
    assert "使用 弯刀 对 训练无人机 发起攻击" in result["journal_events"][0]
    assert "伤害骰: 1d6[掷出 4]" in result["journal_events"][0]
    assert "[战术走位]" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["weapon"] == "scimitar"
    assert result["latest_roll"]["weapon_name"] == "弯刀"
    assert result["latest_roll"]["damage"]["formula"] == "1d6"
    assert result["latest_roll"]["damage"]["total"] == 4


def test_turn_lock_rejects_out_of_turn_action():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["scout", "player", "drone_1"]
    state["current_turn_index"] = 0
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "tactician",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "动作无效" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["result_type"] == "TURN_LOCKED"
    assert result["entities"]["drone_1"]["hp"] == 7
    assert result["combat_active"] is True
    assert result["initiative_order"] == ["scout", "player", "drone_1"]
    assert result["current_turn_index"] == 0
    assert "敌方回合" not in "\n".join(result["journal_events"])


@patch("core.systems.mechanics.random.randint", side_effect=[15, 3])
def test_end_turn_advances_to_enemy_then_waits_for_player_side(mock_randint):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["tactician", "drone_1", "player"]
    state["current_turn_index"] = 0
    state["entities"]["drone_1"]["x"] = 4
    state["entities"]["drone_1"]["y"] = 8
    state["entities"]["player"]["x"] = 4
    state["entities"]["player"]["y"] = 9
    for companion_id in ("scout", "analyst"):
        state["entities"][companion_id]["status"] = "dead"
        state["entities"][companion_id]["hp"] = 0
    state.update(
        {
            "intent": "END_TURN",
            "intent_context": {
                "action_actor": "tactician",
                "action_target": "party",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "宣布我方结束回合" in "\n".join(result["journal_events"])
    assert "训练无人机 使用 弯刀 对 玩家 发起攻击" in "\n".join(result["journal_events"])
    assert result["entities"]["player"]["hp"] == 17
    assert result["combat_active"] is True
    assert result["initiative_order"] == ["tactician", "drone_1", "player"]
    assert result["current_turn_index"] == 2


@patch("core.systems.mechanics.random.randint", side_effect=[14, 4])
def test_shared_turn_block_allows_party_members_to_act_interleaved(mock_randint):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["scout", "player", "drone_1"]
    state["current_turn_index"] = 0
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["hp"] == 3
    assert result["combat_active"] is True
    assert result["current_turn_index"] == 0
    assert result["turn_resources"]["player"]["action"] == 0


def test_attack_rejects_when_action_resource_spent():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 0, "bonus_action": 1, "movement": 6}}
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "动作资源不足" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["result_type"] == "NO_ACTION"


def test_ranged_attack_rejects_when_line_of_sight_blocked():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["entities"]["player"]["equipment"] = {"main_hand": None, "ranged": "shortbow", "armor": None}
    state["entities"]["drone_1"]["x"] = 5
    state["entities"]["drone_1"]["y"] = 4
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["hp"] == 7
    assert "视线范围内" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["result"]["result_type"] == "NO_LOS"
    assert result["turn_resources"]["player"]["action"] == 1


@patch(
    "core.systems.mechanics._generate_bark_for_event",
    return_value={
        "entity": "player",
        "entity_name": "玩家",
        "event_type": "ENVIRONMENTAL_SHOVE",
        "target": "训练无人机",
        "text": "请你吃火",
    },
)
@patch("core.systems.mechanics.parse_dice_string", return_value=3)
@patch("core.systems.mechanics.random.randint", side_effect=[12, 8])
def test_shove_success_pushes_target_into_campfire_and_applies_fire_damage(
    mock_randint,
    mock_parse_dice,
    mock_generate_bark,
):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["entities"]["player"]["x"] = 8
    state["entities"]["player"]["y"] = 10
    state["entities"]["drone_1"]["x"] = 8
    state["entities"]["drone_1"]["y"] = 9
    state.update(
        {
            "intent": "SHOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["x"] == 8
    assert result["entities"]["drone_1"]["y"] == 8
    assert result["entities"]["drone_1"]["hp"] == 4
    assert result["turn_resources"]["player"]["bonus_action"] == 0
    assert result["turn_resources"]["player"]["action"] == 1
    assert "力量对抗" in "\n".join(result["journal_events"])
    assert "推进篝火" in "\n".join(result["journal_events"])
    assert "火焰伤害" in "\n".join(result["journal_events"])
    assert '💬 [台词] 玩家: "请你吃火"' in "\n".join(result["journal_events"])
    assert len(result["recent_barks"]) == 1
    assert result["recent_barks"][0]["event_type"] == "ENVIRONMENTAL_SHOVE"
    assert result["entities"]["drone_1"]["status_effects"] == []
    assert result["latest_roll"]["intent"] == "SHOVE"
    assert result["latest_roll"]["result"]["result_type"] == "PUSHED_INTO_CAMPFIRE"
    assert result["latest_roll"]["result"]["is_success"] is True
    assert mock_generate_bark.called


@patch("core.systems.mechanics.random.randint", side_effect=[16, 6])
def test_shove_success_applies_prone_when_not_pushed_into_campfire(mock_randint):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["entities"]["player"]["x"] = 4
    state["entities"]["player"]["y"] = 4
    state["entities"]["drone_1"]["x"] = 4
    state["entities"]["drone_1"]["y"] = 3
    state.update(
        {
            "intent": "SHOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["x"] == 4
    assert result["entities"]["drone_1"]["y"] == 2
    assert result["entities"]["drone_1"]["status_effects"] == [{"type": "prone", "duration": 1}]
    assert "倒地状态" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["result"]["result_type"] == "SUCCESS"


def test_shove_rejects_when_bonus_action_spent():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 0, "movement": 6}}
    state["entities"]["player"]["x"] = 4
    state["entities"]["player"]["y"] = 4
    state["entities"]["drone_1"]["x"] = 4
    state["entities"]["drone_1"]["y"] = 3
    state.update(
        {
            "intent": "SHOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "附赠动作不足" in "\n".join(result["journal_events"])
    assert result["entities"]["drone_1"]["x"] == 4
    assert result["entities"]["drone_1"]["y"] == 3
    assert result["latest_roll"]["result"]["result_type"] == "NO_BONUS_ACTION"


@patch("core.systems.mechanics.random.randint", side_effect=[15, 5])
def test_shove_fails_when_destination_is_blocked_obstacle(mock_randint):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 8
    state["entities"]["drone_1"]["x"] = 5
    state["entities"]["drone_1"]["y"] = 7
    state.update(
        {
            "intent": "SHOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["x"] == 5
    assert result["entities"]["drone_1"]["y"] == 7
    assert result["turn_resources"]["player"]["bonus_action"] == 0
    assert "被岩石阻挡" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["result"]["result_type"] == "PUSH_BLOCKED_OBSTACLE"
    assert result["latest_roll"]["result"]["is_success"] is False


@patch("core.systems.mechanics.parse_dice_string", return_value=2)
def test_status_tick_poisoned_applies_damage_at_turn_start(mock_parse_dice):
    state = get_initial_world_state()
    state["entities"]["player"]["hp"] = 10
    state["entities"]["player"]["status_effects"] = [{"type": "poisoned", "duration": 2}]
    turn_resources = {"player": {"action": 1, "bonus_action": 1, "movement": 6, "_turn_started": False}}

    updated_resources, tick_events = mechanics._begin_turn_for_block(
        entities=state["entities"],
        turn_resources=turn_resources,
        active_block=["player"],
        force_reset=False,
    )

    assert state["entities"]["player"]["hp"] == 8
    assert any("中毒" in event for event in tick_events)
    assert updated_resources["player"]["_turn_started"] is True


def test_status_tick_duration_reduces_and_expires_at_end_of_turn():
    state = get_initial_world_state()
    state["entities"]["player"]["status_effects"] = [{"type": "poisoned", "duration": 1}]
    turn_resources = {"player": {"action": 0, "bonus_action": 0, "movement": 0, "_turn_started": True}}

    updated_resources, tick_events = mechanics._end_turn_for_block(
        entities=state["entities"],
        turn_resources=turn_resources,
        active_block=["player"],
    )

    assert state["entities"]["player"]["status_effects"] == []
    assert any("状态已解除" in event for event in tick_events)
    assert updated_resources["player"]["_turn_started"] is False


@patch("core.systems.mechanics.parse_dice_string", return_value=3)
@patch("core.systems.mechanics.random.randint", side_effect=[5, 17])
def test_melee_attack_against_prone_target_uses_advantage(mock_randint, mock_parse_dice):
    state = get_initial_world_state()
    attacker = state["entities"]["player"]
    defender = state["entities"]["drone_1"]
    attacker["equipment"] = {"main_hand": "scimitar", "ranged": None, "armor": None}
    attacker["x"] = 4
    attacker["y"] = 4
    defender["x"] = 4
    defender["y"] = 3
    defender["status_effects"] = [{"type": "prone", "duration": 1}]
    defender["hp"] = 7

    result = mechanics.execute_combat_attack(attacker, defender, state.get("map_data"))

    log_text = "\n".join(result["journal_events"])
    assert "攻击获得优势 (5, 17) -> 17" in log_text
    assert result["raw_roll_data"]["result"]["rolls"] == [5, 17]
    assert result["raw_roll_data"]["result"]["raw_roll"] == 17
    assert result["raw_roll_data"]["result"]["is_success"] is True


@patch("core.systems.mechanics.parse_dice_string", return_value=2)
@patch("core.systems.mechanics.random.randint", return_value=10)
def test_attack_auto_switches_to_ranged_and_keeps_position_when_target_in_range(
    mock_randint,
    mock_parse_dice,
):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["scout", "drone_1", "player"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"scout": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["entities"]["scout"]["x"] = 0
    state["entities"]["scout"]["y"] = 0
    state["entities"]["scout"]["equipment"] = {"main_hand": "rusty_dagger", "ranged": "shortbow", "armor": None}
    state["entities"]["drone_1"]["x"] = 4
    state["entities"]["drone_1"]["y"] = 5
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "drone_1",
            },
            "user_input": "侦察员射击训练无人机",
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["scout"]["x"] == 0
    assert result["entities"]["scout"]["y"] == 0
    assert result["latest_roll"]["weapon"] == "shortbow"
    assert result["latest_roll"]["weapon_type"] == "ranged"
    assert result["latest_roll"]["ability"] == "DEX"
    assert result["latest_roll"]["ability_modifier"] == 3
    assert result["latest_roll"]["modifier"] == 7
    assert "发起远程攻击" in result["journal_events"][0]


@patch("core.systems.mechanics.parse_dice_string", return_value=8)
@patch("core.systems.mechanics.random.randint", return_value=10)
def test_cast_spell_thunderwave_applies_saving_throw_aoe_and_consumes_slot(
    mock_randint,
    mock_parse_dice,
):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["analyst", "drone_1", "player"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {
        "analyst": {
            "action": 1,
            "bonus_action": 1,
            "movement": 6,
            "spell_slots": {"level_1": 2},
        }
    }
    state["entities"]["analyst"]["x"] = 4
    state["entities"]["analyst"]["y"] = 4
    state["entities"]["drone_1"]["x"] = 4
    state["entities"]["drone_1"]["y"] = 3
    state["entities"]["player"]["x"] = 9
    state["entities"]["player"]["y"] = 9
    state["entities"]["scout"]["x"] = 9
    state["entities"]["scout"]["y"] = 8
    state["entities"]["tactician"]["x"] = 8
    state["entities"]["tactician"]["y"] = 9
    state.update(
        {
            "intent": "CAST_SPELL",
            "intent_context": {
                "action_actor": "analyst",
                "action_target": "drone_1",
                "spell_id": "thunderwave",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    drone = result["entities"]["drone_1"]
    assert drone["hp"] == 0
    assert drone["status"] == "dead"
    assert result["turn_resources"]["analyst"]["action"] == 0
    assert result["turn_resources"]["analyst"]["spell_slots"]["level_1"] == 1
    assert "施放了 雷鸣波" in "\n".join(result["journal_events"])
    assert "消耗1环法术位" in "\n".join(result["journal_events"])
    assert "体质豁免" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["intent"] == "CAST_SPELL"
    assert result["latest_roll"]["spell_id"] == "thunderwave"
    assert result["latest_roll"]["result"]["is_success"] is True


def test_single_target_spell_rejects_when_line_of_sight_blocked_without_resource_cost():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["initiative_order"] = ["analyst", "drone_1", "player"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {
        "analyst": {
            "action": 1,
            "bonus_action": 1,
            "movement": 6,
            "spell_slots": {"level_1": 2},
        }
    }
    state["entities"]["analyst"]["x"] = 5
    state["entities"]["analyst"]["y"] = 8
    state["entities"]["drone_1"]["x"] = 5
    state["entities"]["drone_1"]["y"] = 4
    state.update(
        {
            "intent": "CAST_SPELL",
            "intent_context": {
                "action_actor": "analyst",
                "action_target": "drone_1",
                "spell_id": "sacred_flame",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["drone_1"]["hp"] == 7
    assert "视线范围内" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["result"]["result_type"] == "NO_LOS"
    assert result["turn_resources"]["analyst"]["action"] == 1
    assert result["turn_resources"]["analyst"]["spell_slots"]["level_1"] == 2


@patch("core.systems.mechanics.random.randint", side_effect=[15, 3])
def test_enemy_turn_moves_by_speed_budget_and_attacks_when_in_range(mock_randint):
    state = get_initial_world_state()
    for companion_id in ("scout", "analyst", "tactician"):
        state["entities"][companion_id]["status"] = "dead"
        state["entities"][companion_id]["hp"] = 0
    state["entities"]["player"]["x"] = 4
    state["entities"]["player"]["y"] = 9
    state["entities"]["drone_1"]["x"] = 4
    state["entities"]["drone_1"]["y"] = 3
    state["entities"]["drone_1"]["speed"] = 30

    result = mechanics.execute_enemy_turn("drone_1", state)

    drone = result["entities"]["drone_1"]
    player = result["entities"]["player"]
    assert drone["x"] == 4
    assert drone["y"] == 8
    assert drone["position"] == "靠近 玩家"
    assert player["hp"] == 17
    assert result["turn_resources"]["drone_1"]["movement"] == 1
    assert result["turn_resources"]["drone_1"]["action"] == 0
    assert "敌方AI" in "\n".join(result["journal_events"])
    assert "训练无人机 使用 弯刀 对 玩家 发起攻击" in "\n".join(result["journal_events"])


@patch("core.systems.mechanics.random.randint", side_effect=[15, 3])
def test_enemy_turn_uses_a_star_to_route_around_obstacles(mock_randint):
    state = get_initial_world_state()
    for companion_id in ("scout", "analyst", "tactician"):
        state["entities"][companion_id]["status"] = "dead"
        state["entities"][companion_id]["hp"] = 0
    state["entities"]["player"]["x"] = 5
    state["entities"]["player"]["y"] = 4
    state["entities"]["drone_1"]["x"] = 5
    state["entities"]["drone_1"]["y"] = 8
    state["entities"]["drone_1"]["speed"] = 30
    state["turn_resources"] = {
        "drone_1": {
            "action": 1,
            "bonus_action": 1,
            "movement": 3,
        }
    }

    result = mechanics.execute_enemy_turn("drone_1", state)

    drone = result["entities"]["drone_1"]
    blocked_tiles = {tuple(tile) for tile in state["map_data"]["blocked_movement_tiles"]}
    assert (drone["x"], drone["y"]) not in blocked_tiles
    assert max(abs(drone["x"] - 5), abs(drone["y"] - 4)) <= 1
    assert result["turn_resources"]["drone_1"]["movement"] == 0
    assert result["turn_resources"]["drone_1"]["action"] == 0
    assert "绕过了障碍" in "\n".join(result["journal_events"])
    assert "训练无人机 使用 弯刀 对 玩家 发起攻击" in "\n".join(result["journal_events"])


@patch("core.systems.mechanics.random.randint", side_effect=[12, 4])
def test_enemy_archer_prefers_ranged_attack_position(mock_randint):
    state = get_initial_world_state()
    for companion_id in ("analyst", "tactician", "drone_1", "drone_support"):
        state["entities"][companion_id]["status"] = "dead"
        state["entities"][companion_id]["hp"] = 0
    state["entities"]["player"]["x"] = 9
    state["entities"]["player"]["y"] = 6
    state["entities"]["scout"]["status"] = "dead"
    state["entities"]["scout"]["hp"] = 0
    state["entities"]["drone_sentinel"]["x"] = 9
    state["entities"]["drone_sentinel"]["y"] = 3
    state["turn_resources"] = {
        "drone_sentinel": {
            "action": 1,
            "bonus_action": 1,
            "movement": 6,
        }
    }

    result = mechanics.execute_enemy_turn("drone_sentinel", state)
    archer = result["entities"]["drone_sentinel"]
    distance = max(
        abs(archer["x"] - state["entities"]["player"]["x"]),
        abs(archer["y"] - state["entities"]["player"]["y"]),
    )
    logs = "\n".join(result["journal_events"])

    assert distance >= 4
    assert "短弓" in logs
    assert result["turn_resources"]["drone_sentinel"]["action"] == 0


@patch("core.systems.mechanics.parse_dice_string", return_value=3)
def test_enemy_shaman_prioritizes_healing_low_hp_ally(mock_parse_dice):
    state = get_initial_world_state()
    for companion_id in ("player", "analyst", "scout", "tactician", "drone_sentinel"):
        if companion_id in state["entities"]:
            state["entities"][companion_id]["status"] = "dead"
            state["entities"][companion_id]["hp"] = 0
    state["entities"]["player"]["status"] = "alive"
    state["entities"]["player"]["hp"] = 20
    state["entities"]["player"]["x"] = 4
    state["entities"]["player"]["y"] = 9
    state["entities"]["drone_1"]["hp"] = 2
    state["entities"]["drone_1"]["max_hp"] = 7
    state["entities"]["drone_1"]["x"] = 6
    state["entities"]["drone_1"]["y"] = 4
    state["entities"]["drone_support"]["x"] = 8
    state["entities"]["drone_support"]["y"] = 4
    state["turn_resources"] = {
        "drone_support": {
            "action": 1,
            "bonus_action": 1,
            "movement": 6,
            "spell_slots": {"level_1": 1},
        }
    }

    result = mechanics.execute_enemy_turn("drone_support", state)
    logs = "\n".join(result["journal_events"])
    healed_hp = result["entities"]["drone_1"]["hp"]

    assert healed_hp > 2
    assert "治愈真言" in logs
    assert result["turn_resources"]["drone_support"]["action"] == 0
    assert result["turn_resources"]["drone_support"]["spell_slots"]["level_1"] == 0


@patch("core.systems.mechanics.parse_dice_string", return_value=6)
@patch("core.systems.mechanics.random.randint", return_value=1)
def test_sacred_flame_ignites_powder_barrel_chain_explosion(mock_randint, mock_parse_dice):
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state["entities"]["analyst"]["x"] = 7
    state["entities"]["analyst"]["y"] = 4
    state["entities"]["drone_1"]["x"] = 7
    state["entities"]["drone_1"]["y"] = 4
    state.update(
        {
            "intent": "CAST_SPELL",
            "intent_context": {
                "action_actor": "analyst",
                "action_target": "powder_barrel_1",
                "spell_id": "sacred_flame",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)
    logs = "\n".join(result["journal_events"])
    barrel_1 = result["entities"]["powder_barrel_1"]
    barrel_2 = result["entities"]["powder_barrel_2"]
    drone = result["entities"]["drone_1"]

    assert barrel_1["status"] == "dead"
    assert barrel_2["status"] == "dead"
    assert drone["hp"] < 7
    assert "地形连锁" in logs
    assert "(7,3)" in logs
    assert "(8,3)" in logs
    assert [7, 3] not in result["map_data"]["blocked_movement_tiles"]
    assert [8, 3] not in result["map_data"]["blocked_movement_tiles"]


def test_loot_transfers_dead_entity_inventory_to_player():
    state = get_initial_world_state()
    state["entities"]["drone_1"]["status"] = "dead"
    state["player_inventory"] = {"healing_potion": 2}
    state.update(
        {
            "intent": "LOOT",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {
        "healing_potion": 2,
        "gold_coin": 5,
        "scimitar": 1,
    }
    assert result["entities"]["drone_1"]["inventory"] == {}
    assert "玩家 从 训练无人机 上搜刮到了" in result["journal_events"][0]
    assert "金币 x 5" in result["journal_events"][0]
    assert "弯刀 x 1" in result["journal_events"][0]
    assert result["latest_roll"]["intent"] == "LOOT"
    assert result["latest_roll"]["target"] == "drone_1"
    assert result["latest_roll"]["result"]["is_success"] is True


def test_loot_rejects_target_that_is_not_lootable():
    state = get_initial_world_state()
    state["player_inventory"] = {}
    state.update(
        {
            "intent": "LOOT",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {}
    assert result["entities"]["drone_1"]["inventory"] == {
        "gold_coin": 5,
        "scimitar": 1,
    }
    assert "还无法被搜刮" in result["journal_events"][0]
    assert result["latest_roll"]["intent"] == "LOOT"
    assert result["latest_roll"]["result"]["is_success"] is False


@patch("core.systems.mechanics.random.randint", side_effect=INITIATIVE_PLAYER_FIRST + [18, 4, 5])
def test_killing_drone_sentinel_creates_loot_drop_and_exits_combat(mock_randint):
    state = get_initial_world_state()
    # 保留弓箭手作为最后敌人，其余敌人先移除
    for enemy_id in ("drone_1", "drone_support"):
        enemy = state["entities"][enemy_id]
        enemy["status"] = "dead"
        enemy["hp"] = 0
        enemy["loot_generated"] = True
    state["entities"]["drone_sentinel"]["hp"] = 4
    state["entities"]["drone_sentinel"]["max_hp"] = 5
    state["entities"]["player"]["x"] = 9
    state["entities"]["player"]["y"] = 4
    state["entities"]["drone_sentinel"]["x"] = 9
    state["entities"]["drone_sentinel"]["y"] = 3
    state["entities"]["player"]["equipment"] = {"main_hand": "scimitar", "ranged": None, "armor": None}
    state.update(
        {
            "intent": "ATTACK",
            "intent_context": {"action_actor": "player", "action_target": "drone_sentinel"},
            "current_speaker": "player",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)
    loot_drop_ids = [
        entity_id
        for entity_id, entity in result["entities"].items()
        if isinstance(entity, dict) and entity.get("entity_type") == "loot_drop"
    ]

    assert result["combat_active"] is False
    assert result.get("combat_phase") == "OUT_OF_COMBAT"
    assert result["initiative_order"] == []
    assert result["turn_resources"] == {}
    assert loot_drop_ids
    drop = result["entities"][loot_drop_ids[0]]
    assert drop["inventory"]["shortbow"] >= 1
    assert 1 <= drop["inventory"]["gold_coin"] <= 10
    assert "进入自由探索模式" in "\n".join(result["journal_events"])


def test_loot_drop_requires_adjacent_and_transfers_items():
    state = get_initial_world_state()
    drop_id = "loot_drop_1"
    state["entities"][drop_id] = {
        "name": "训练无人机弓箭手 的遗骸",
        "entity_type": "loot_drop",
        "source_name": "训练无人机弓箭手",
        "faction": "neutral",
        "hp": 0,
        "max_hp": 0,
        "status": "open",
        "x": 9,
        "y": 3,
        "inventory": {"shortbow": 1, "gold_coin": 5},
    }
    state["entities"]["scout"]["x"] = 1
    state["entities"]["scout"]["y"] = 1
    state.update(
        {
            "intent": "LOOT",
            "intent_context": {"action_actor": "scout", "action_target": drop_id},
            "current_speaker": "scout",
            "is_probing_secret": False,
        }
    )

    fail_result = mechanics_node(state)
    assert "必须相邻" in fail_result["journal_events"][0]
    assert drop_id in fail_result["entities"]

    state["entities"]["scout"]["x"] = 9
    state["entities"]["scout"]["y"] = 4
    success_result = mechanics_node(state)

    assert drop_id not in success_result["entities"]
    assert success_result["entities"]["scout"]["inventory"]["shortbow"] == 1
    assert success_result["entities"]["scout"]["inventory"]["gold_coin"] == 5
    assert "侦察员" in success_result["journal_events"][0] or "Scout" in success_result["journal_events"][0]
    assert "搜刮到了" in success_result["journal_events"][0]


@patch("core.systems.mechanics.parse_dice_string", return_value=6)
def test_use_item_consumes_potion_and_restores_player_hp(mock_parse_dice):
    state = get_initial_world_state()
    state["player_inventory"] = {"healing_potion": 2}
    state["entities"]["player"] = {
        "name": "玩家",
        "hp": 8,
        "max_hp": 20,
        "status": "alive",
        "inventory": {},
    }
    state.update(
        {
            "intent": "CONSUME",
            "intent_context": {
                "action_actor": "player",
                "item_id": "healing_potion",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {"healing_potion": 1}
    assert result["entities"]["player"]["hp"] == 14
    assert "玩家 喝下了 治疗药水" in result["journal_events"][0]
    assert "恢复了 6 点生命值" in result["journal_events"][0]
    assert result["latest_roll"]["intent"] == "CONSUME"
    assert result["latest_roll"]["result"]["is_success"] is True


@patch("core.systems.mechanics.parse_dice_string", return_value=7)
def test_use_item_consumes_bonus_action_in_combat(mock_parse_dice):
    state = get_initial_world_state()
    state["combat_active"] = True
    state["combat_phase"] = "IN_COMBAT"
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["player_inventory"] = {"healing_potion": 1}
    state["entities"]["player"]["hp"] = 10
    state["entities"]["player"]["max_hp"] = 20
    state.update(
        {
            "intent": "USE_ITEM",
            "intent_context": {
                "action_actor": "player",
                "item_id": "healing_potion",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["hp"] == 17
    assert result["player_inventory"] == {}
    assert result["turn_resources"]["player"]["bonus_action"] == 0
    assert result["turn_resources"]["player"]["action"] == 1


def test_use_item_rejects_when_bonus_action_spent_in_combat():
    state = get_initial_world_state()
    state["combat_active"] = True
    state["combat_phase"] = "IN_COMBAT"
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 0, "movement": 6}}
    state["player_inventory"] = {"healing_potion": 1}
    state["entities"]["player"]["hp"] = 10
    state["entities"]["player"]["max_hp"] = 20
    state.update(
        {
            "intent": "USE_ITEM",
            "intent_context": {
                "action_actor": "player",
                "item_id": "healing_potion",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "附赠动作不足" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["result_type"] == "NO_BONUS_ACTION"
    assert result["player_inventory"] == {"healing_potion": 1}
    assert result["entities"]["player"]["hp"] == 10


def test_use_item_returns_failure_when_inventory_missing_item():
    state = get_initial_world_state()
    state["player_inventory"] = {}
    state["entities"]["player"] = {
        "name": "玩家",
        "hp": 8,
        "max_hp": 20,
        "status": "alive",
        "inventory": {},
    }
    state.update(
        {
            "intent": "USE_ITEM",
            "intent_context": {
                "action_actor": "player",
                "item_id": "healing_potion",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {}
    assert result["entities"]["player"]["hp"] == 8
    assert "背包里没有 治疗药水" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["is_success"] is False


def test_use_item_rejects_weapon_consumption_and_keeps_inventory():
    state = get_initial_world_state()
    state["player_inventory"] = {"scimitar": 1}
    state.update(
        {
            "intent": "USE_ITEM",
            "intent_context": {
                "action_actor": "player",
                "item_id": "scimitar",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["player_inventory"] == {"scimitar": 1}
    assert "不是可消耗物品" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["is_success"] is False
    assert result["latest_roll"]["result"]["result_type"] == "NOT_CONSUMABLE"


@patch("core.systems.mechanics.roll_d20")
def test_sleight_of_hand_success_unlocks_locked_chest(mock_roll_d20):
    mock_roll_d20.return_value = {
        "total": 18,
        "raw_roll": 16,
        "rolls": [16],
        "is_success": True,
        "result_type": "SUCCESS",
        "log_str": "mocked",
    }
    state = get_initial_world_state()
    state.update(
        {
            "intent": "SLEIGHT_OF_HAND",
            "intent_context": {
                "action_actor": "player",
                "action_target": "iron_chest",
                "difficulty_class": 15,
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["environment_objects"]["iron_chest"]["status"] == "opened"
    assert "沉重的铁箱子 被解锁了" in "\n".join(result["journal_events"])
    assert result["latest_roll"]["result"]["is_success"] is True


def test_move_action_updates_player_coordinates_toward_target():
    state = get_initial_world_state()
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "drone_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["x"] == 4
    assert result["entities"]["player"]["y"] == 4
    assert result["entities"]["player"]["position"] == "靠近 训练无人机"
    assert "玩家移动到了 训练无人机 附近" in result["journal_events"][0]
    assert result["latest_roll"]["intent"] == "MOVE"
    assert result["latest_roll"]["target"] == "drone_1"
    assert result["latest_roll"]["result"]["is_success"] is True


def test_move_action_fuzzy_matches_chest_alias_to_iron_chest():
    state = get_initial_world_state()
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "宝箱",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["x"] == 6
    assert result["entities"]["player"]["y"] == 3
    assert result["entities"]["player"]["position"] == "靠近 沉重的铁箱子"
    assert "沉重的铁箱子" in result["journal_events"][0]
    assert result["latest_roll"]["target"] == "iron_chest"
    assert result["latest_roll"]["result"]["is_success"] is True


def test_move_action_resolves_player_pronoun_target_for_companion():
    state = get_initial_world_state()
    state["entities"]["analyst"]["x"] = 0
    state["entities"]["analyst"]["y"] = 0
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "analyst",
                "action_target": "我",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["analyst"]["x"] == 4
    assert result["entities"]["analyst"]["y"] == 8
    assert result["entities"]["analyst"]["position"] == "靠近 玩家"
    assert result["latest_roll"]["target"] == "player"
    assert result["latest_roll"]["result"]["is_success"] is True


def test_move_action_rejects_out_of_map_boundary():
    state = get_initial_world_state()
    state["entities"]["player"]["x"] = 1
    state["entities"]["player"]["y"] = 1
    state["environment_objects"]["void_marker"] = {
        "name": "虚空边缘",
        "status": "open",
        "x": -5,
        "y": 1,
    }
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "void_marker",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["x"] == 1
    assert result["entities"]["player"]["y"] == 1
    assert "超出地图边界" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["is_success"] is False
    assert result["latest_roll"]["result"]["result_type"] == "COLLISION_BLOCKED"


def test_move_action_rejects_blocked_obstacle_tile():
    state = get_initial_world_state()
    state["entities"]["player"]["x"] = 9
    state["entities"]["player"]["y"] = 5
    state["environment_objects"]["decoy_target"] = {
        "name": "目标木桩",
        "status": "open",
        "x": 4,
        "y": 5,
    }
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "decoy_target",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["x"] == 9
    assert result["entities"]["player"]["y"] == 5
    assert "被岩石阻挡" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["is_success"] is False
    assert result["latest_roll"]["result"]["result_type"] == "COLLISION_BLOCKED"


def test_move_action_rejects_overlap_with_alive_entity():
    state = get_initial_world_state()
    state["entities"]["player"]["x"] = 2
    state["entities"]["player"]["y"] = 7
    state["entities"]["tactician"]["x"] = 7
    state["entities"]["tactician"]["y"] = 7
    state["entities"]["tactician"]["status"] = "alive"
    state["environment_objects"]["decoy_target"] = {
        "name": "目标木桩",
        "status": "open",
        "x": 8,
        "y": 7,
    }
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "decoy_target",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["player"]["x"] == 2
    assert result["entities"]["player"]["y"] == 7
    assert "已被 战术员 占据" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["is_success"] is False
    assert result["latest_roll"]["result"]["result_type"] == "COLLISION_BLOCKED"


def test_move_action_passive_perception_reveals_hidden_trap():
    state = get_initial_world_state()
    state["entities"]["player"]["x"] = 8
    state["entities"]["player"]["y"] = 9
    state["entities"]["player"]["ability_scores"]["WIS"] = 18
    state.update(
        {
            "intent": "MOVE",
            "intent_context": {
                "action_actor": "player",
                "action_target": "9,9",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    trap = result["entities"]["trap_tripwire_1"]
    assert trap["is_hidden"] is False
    assert "察觉到了地上的 绊线陷阱" in "\n".join(result["journal_events"])


@patch("core.systems.mechanics.roll_d20")
def test_disarm_intent_successfully_disables_visible_trap(mock_roll_d20):
    mock_roll_d20.return_value = {
        "total": 16,
        "raw_roll": 16,
        "rolls": [16],
        "is_success": True,
        "result_type": "SUCCESS",
        "log_str": "mocked",
    }
    state = get_initial_world_state()
    state["entities"]["player"]["x"] = 10
    state["entities"]["player"]["y"] = 9
    state["entities"]["trap_tripwire_1"]["is_hidden"] = False
    state["entities"]["trap_tripwire_1"]["status"] = "revealed"
    state.update(
        {
            "intent": "DISARM",
            "intent_context": {
                "action_actor": "player",
                "action_target": "trap_tripwire_1",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "trap_tripwire_1" not in result["entities"]
    assert "成功解除了 绊线陷阱" in "\n".join(result["journal_events"])


@patch("core.systems.mechanics.roll_d20")
def test_unlock_intent_opens_locked_chest(mock_roll_d20):
    mock_roll_d20.return_value = {
        "total": 17,
        "raw_roll": 15,
        "rolls": [15],
        "is_success": True,
        "result_type": "SUCCESS",
        "log_str": "mocked",
    }
    state = get_initial_world_state()
    state["entities"]["player"]["x"] = 10
    state["entities"]["player"]["y"] = 7
    state.update(
        {
            "intent": "UNLOCK",
            "intent_context": {
                "action_actor": "player",
                "action_target": "locked_chest",
            },
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    chest = result["environment_objects"]["locked_chest"]
    assert chest["is_locked"] is False
    assert chest["status"] == "opened"
    assert "成功打开了 上锁的旅行箱" in "\n".join(result["journal_events"])


@patch("core.systems.mechanics.roll_d20")
def test_sleight_of_hand_auto_approaches_before_unlocking_chest(mock_roll_d20):
    mock_roll_d20.return_value = {
        "total": 20,
        "raw_roll": 18,
        "rolls": [18],
        "is_success": True,
        "result_type": "SUCCESS",
        "log_str": "mocked",
    }
    state = get_initial_world_state()
    state["entities"]["scout"]["x"] = 0
    state["entities"]["scout"]["y"] = 0
    state.update(
        {
            "intent": "SLEIGHT_OF_HAND",
            "intent_context": {
                "action_actor": "scout",
                "action_target": "iron_chest",
                "difficulty_class": 15,
            },
            "current_speaker": "scout",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    scout = result["entities"]["scout"]
    assert max(
        abs(scout["x"] - state["environment_objects"]["iron_chest"]["x"]),
        abs(scout["y"] - state["environment_objects"]["iron_chest"]["y"]),
    ) == 1
    assert scout["position"] == "靠近 沉重的铁箱子"
    assert result["environment_objects"]["iron_chest"]["status"] == "opened"
    assert "[自动寻路]" in "\n".join(result["journal_events"])
    assert "沉重的铁箱子 被解锁了" in "\n".join(result["journal_events"])


def test_short_rest_is_rejected_in_combat():
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state["combat_active"] = True
    state["combat_phase"] = "IN_COMBAT"
    state["initiative_order"] = ["player", "drone_1"]
    state["current_turn_index"] = 0
    state["turn_resources"] = {"player": {"action": 1, "bonus_action": 1, "movement": 6}}
    state["entities"]["player"]["hp"] = 11
    state.update(
        {
            "intent": "SHORT_REST",
            "intent_context": {"action_actor": "player"},
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert "CANNOT_REST_IN_COMBAT" in result["journal_events"][0]
    assert result["latest_roll"]["result"]["result_type"] == "CANNOT_REST_IN_COMBAT"
    assert result["entities"]["player"]["hp"] == 11
    assert result["combat_active"] is True


def test_long_rest_restores_hp_spell_slots_and_clears_negative_statuses():
    state = get_initial_world_state()
    _deactivate_additional_enemies(state)
    state["combat_active"] = False
    state["combat_phase"] = "OUT_OF_COMBAT"
    state["initiative_order"] = []
    state["current_turn_index"] = 0
    state["entities"]["scout"]["hp"] = 3
    state["entities"]["scout"]["status_effects"] = [{"type": "poisoned", "duration": 2}]
    state["entities"]["analyst"]["hp"] = 2
    state["entities"]["analyst"]["status_effects"] = [{"type": "prone", "duration": 1}]
    state["entities"]["analyst"]["spell_slots"] = {"level_1": 2}
    state["turn_resources"] = {
        "analyst": {"action": 0, "bonus_action": 0, "movement": 0, "spell_slots": {"level_1": 0}},
        "scout": {"action": 0, "bonus_action": 0, "movement": 0},
    }
    state.update(
        {
            "intent": "LONG_REST",
            "intent_context": {"action_actor": "player"},
            "current_speaker": "analyst",
            "is_probing_secret": False,
        }
    )

    result = mechanics_node(state)

    assert result["entities"]["scout"]["hp"] == result["entities"]["scout"]["max_hp"]
    assert result["entities"]["analyst"]["hp"] == result["entities"]["analyst"]["max_hp"]
    assert result["entities"]["analyst"]["spell_slots"]["level_1"] == 2
    assert result["turn_resources"]["analyst"]["spell_slots"]["level_1"] == 2
    assert all(
        effect.get("type") not in {"poisoned", "prone"}
        for effect in result["entities"]["scout"]["status_effects"]
    )
    assert all(
        effect.get("type") not in {"poisoned", "prone"}
        for effect in result["entities"]["analyst"]["status_effects"]
    )
    assert "长休" in "\n".join(result["journal_events"])
    assert result["combat_active"] is False
    assert result["combat_phase"] == "OUT_OF_COMBAT"
