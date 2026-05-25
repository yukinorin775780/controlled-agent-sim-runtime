"""游戏系统/规则层：骰子、背包、机制、任务。"""

from importlib import import_module

_EXPORTS = {
    "CheckResult": ("core.systems.dice", "CheckResult"),
    "roll_d20": ("core.systems.dice", "roll_d20"),
    "get_check_result_text": ("core.systems.dice", "get_check_result_text"),
    "ItemRegistry": ("core.systems.inventory", "ItemRegistry"),
    "Inventory": ("core.systems.inventory", "Inventory"),
    "get_registry": ("core.systems.inventory", "get_registry"),
    "get_item_data": ("core.systems.inventory", "get_item_data"),
    "format_inventory_dict_to_display_list": (
        "core.systems.inventory",
        "format_inventory_dict_to_display_list",
    ),
    "init_registry": ("core.systems.inventory", "init_registry"),
    "execute_skill_check": ("core.systems.mechanics", "execute_skill_check"),
    "process_dialogue_triggers": ("core.systems.mechanics", "process_dialogue_triggers"),
    "apply_item_effect": ("core.systems.mechanics", "apply_item_effect"),
    "check_condition": ("core.systems.mechanics", "check_condition"),
    "QuestManager": ("core.systems.quest", "QuestManager"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    return getattr(module, attr_name)

__all__ = [
    "CheckResult",
    "roll_d20",
    "get_check_result_text",
    "ItemRegistry",
    "Inventory",
    "get_registry",
    "get_item_data",
    "format_inventory_dict_to_display_list",
    "init_registry",
    "execute_skill_check",
    "process_dialogue_triggers",
    "apply_item_effect",
    "check_condition",
    "QuestManager",
]
