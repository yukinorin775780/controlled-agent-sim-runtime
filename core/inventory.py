"""
V2 兼容层：core.inventory 重定向至 core.systems.inventory。
供 ui/renderer.py、characters/loader.py、main.py 等使用。
"""

from core.systems.inventory import (
    ItemRegistry,
    Inventory,
    get_registry,
    get_item_data,
    format_inventory_dict_to_display_list,
    init_registry,
)

__all__ = [
    "ItemRegistry",
    "Inventory",
    "get_registry",
    "get_item_data",
    "format_inventory_dict_to_display_list",
    "init_registry",
]
