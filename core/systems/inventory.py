"""
Inventory Management Module (Data-Driven Architecture)
Handles item storage and management using a registry-based system.
"""

import os
from typing import Dict, List, Optional, Any
import yaml


class ItemRegistry:
    """
    Singleton class for managing static item data from YAML configuration.
    Acts as the "Single Source of Truth" for item definitions.
    """
    _instance: Optional['ItemRegistry'] = None
    _items: Dict[str, Dict[str, Any]] = {}
    _weapons: Dict[str, Dict[str, Any]] = {}
    _loaded: bool = False

    @classmethod
    def _default_config_path(cls) -> str:
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "config", "items.yaml")
        )

    @classmethod
    def _ensure_loaded(cls) -> None:
        if not cls._loaded:
            cls.load(cls._default_config_path())

    @staticmethod
    def _normalize_lookup_key(value: Any) -> str:
        return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    
    def __new__(cls) -> 'ItemRegistry':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def load(cls, filepath: str) -> bool:
        """
        Load item and weapon definitions from YAML files.
        
        Args:
            filepath: Path to the items.yaml configuration file. The registry also
                auto-loads weapons.yaml from the same directory when present.
            
        Returns:
            bool: True if loaded successfully, False otherwise
        """
        try:
            if not os.path.exists(filepath):
                print(f"[ItemRegistry] Warning: Item database not found: {filepath}")
                return False
            
            with open(filepath, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if data and 'items' in data:
                cls._items = data['items'] or {}
                cls._weapons = {}
                weapons_path = os.path.join(os.path.dirname(filepath), "weapons.yaml")
                if os.path.exists(weapons_path):
                    with open(weapons_path, 'r', encoding='utf-8') as wf:
                        weapons_data = yaml.safe_load(wf) or {}
                    cls._weapons = weapons_data.get("weapons", {}) or {}
                cls._loaded = True
                return True
            else:
                print("[ItemRegistry] Warning: No 'items' key found in YAML")
                return False
                
        except Exception as e:
            print(f"[ItemRegistry] Error loading item database: {e}")
            return False
    
    @classmethod
    def get(cls, item_id: str) -> Dict[str, Any]:
        """
        Get item configuration by ID.
        
        Args:
            item_id: The unique identifier for the item
            
        Returns:
            Dict containing item data, or fallback data if not found
        """
        cls._ensure_loaded()
        normalized_id = cls.resolve_item_id(item_id) or str(item_id or "").strip().lower()
        if normalized_id in cls._items:
            return cls._items[normalized_id]
        if normalized_id in cls._weapons:
            return cls._weapons[normalized_id]
        
        # Fallback data for unknown items
        return {
            "name": item_id,  # Use ID as name fallback
            "description": "未知物品",
            "type": "unknown",
            "stackable": True,
            "weight": 0.0
        }
    
    @classmethod
    def get_name(cls, item_id: str) -> str:
        """
        Get the display name for an item.
        
        Args:
            item_id: The unique identifier for the item
            
        Returns:
            str: The item's display name
        """
        item_data = cls.get(item_id)
        return item_data.get("name", item_id)

    @classmethod
    def get_item_data(cls, item_id: str) -> Dict[str, Any]:
        """Alias for get(), kept explicit for systems that read item/weapon data."""
        return cls.get(item_id)

    @classmethod
    def resolve_item_id(cls, item_ref: Any) -> str:
        """
        Resolve a YAML equipment entry or display name to a canonical item id.
        Supports exact ids, case-insensitive names, normalized English names,
        and optional aliases from item/weapon config.
        """
        cls._ensure_loaded()
        raw_ref = str(item_ref or "").strip()
        if not raw_ref:
            return ""
        normalized_ref = cls._normalize_lookup_key(raw_ref)
        all_data = {**cls._items, **cls._weapons}
        for item_id, item_data in all_data.items():
            normalized_id = cls._normalize_lookup_key(item_id)
            normalized_name = cls._normalize_lookup_key(item_data.get("name", ""))
            if normalized_ref in {normalized_id, normalized_name}:
                return str(item_id)
            for alias in item_data.get("aliases", []) or []:
                if normalized_ref == cls._normalize_lookup_key(alias):
                    return str(item_id)
        return normalized_ref
    
    @classmethod
    def is_stackable(cls, item_id: str) -> bool:
        """
        Check if an item is stackable.
        
        Args:
            item_id: The unique identifier for the item
            
        Returns:
            bool: True if stackable, False otherwise
        """
        item_data = cls.get(item_id)
        return item_data.get("stackable", True)
    
    @classmethod
    def get_max_stack(cls, item_id: str) -> int:
        """
        Get the maximum stack size for an item.
        
        Args:
            item_id: The unique identifier for the item
            
        Returns:
            int: Maximum stack size (default 99 if not specified)
        """
        item_data = cls.get(item_id)
        if not item_data.get("stackable", True):
            return 1
        return item_data.get("max_stack", 99)
    
    @classmethod
    def is_loaded(cls) -> bool:
        """Check if the registry has been loaded."""
        return cls._loaded
    
    @classmethod
    def all_items(cls) -> Dict[str, Dict[str, Any]]:
        """Get all registered items and weapons through a unified view."""
        cls._ensure_loaded()
        return {**cls._items, **cls._weapons}

    @classmethod
    def all_weapons(cls) -> Dict[str, Dict[str, Any]]:
        """Get all registered weapon definitions."""
        cls._ensure_loaded()
        return cls._weapons.copy()


# Global registry instance
_registry = ItemRegistry()


def get_registry() -> ItemRegistry:
    """Get the global ItemRegistry instance."""
    return _registry


def get_item_data(item_id: str) -> Dict[str, Any]:
    """Unified item/weapon lookup convenience function."""
    return _registry.get_item_data(item_id)


def format_inventory_dict_to_display_list(inv_dict: Dict[str, int]) -> List[str]:
    """
    将状态中的背包字典（item_id -> 数量）转为易读的显示名列表，供提示词/UI 使用。
    与 Inventory.list_item_names() 逻辑一致，但直接接受 dict，无需实例化 Inventory。
    
    Args:
        inv_dict: 物品 ID 到数量的映射，如 {"healing_potion": 2, "gold_coin": 10}
    
    Returns:
        显示名列表，如 ["治疗药水 x2", "金币 x10"]；空背包返回 []
    """
    if not inv_dict:
        return []
    registry = get_registry()
    result: List[str] = []
    for item_id, qty in inv_dict.items():
        name = registry.get_name(item_id)
        if qty > 1:
            result.append(f"{name} x{qty}")
        else:
            result.append(name)
    return result


class Inventory:
    """
    Instance-based inventory system with quantity tracking.
    Uses ItemRegistry for static item data lookup.
    """
    
    def __init__(self):
        """Initialize an empty inventory."""
        self.items: Dict[str, int] = {}  # {item_id: quantity}
    
    def add(self, item_id: str, qty: int = 1) -> bool:
        """
        Add item(s) to the inventory.
        
        Args:
            item_id: The unique identifier for the item
            qty: Quantity to add (default 1)
            
        Returns:
            bool: True if added successfully
        """
        if qty <= 0:
            return False
        
        registry = get_registry()
        
        # Check stacking rules
        if not registry.is_stackable(item_id):
            # Non-stackable items: can only have 1
            if item_id in self.items:
                return False  # Already have one
            self.items[item_id] = 1
        else:
            # Stackable items
            current_qty = self.items.get(item_id, 0)
            max_stack = registry.get_max_stack(item_id)
            new_qty = min(current_qty + qty, max_stack)
            self.items[item_id] = new_qty
        
        return True
    
    def remove(self, item_id: str, qty: int = 1) -> bool:
        """
        Remove item(s) from the inventory.
        
        Args:
            item_id: The unique identifier for the item
            qty: Quantity to remove (default 1)
            
        Returns:
            bool: True if removed successfully, False if insufficient quantity
        """
        if qty <= 0:
            return False
        
        if item_id not in self.items:
            return False
        
        current_qty = self.items[item_id]
        if current_qty < qty:
            return False
        
        new_qty = current_qty - qty
        if new_qty <= 0:
            del self.items[item_id]
        else:
            self.items[item_id] = new_qty
        
        return True
    
    def has(self, item_id: str, qty: int = 1) -> bool:
        """
        Check if the inventory has sufficient quantity of an item.
        
        Args:
            item_id: The unique identifier for the item
            qty: Required quantity (default 1)
            
        Returns:
            bool: True if has sufficient quantity
        """
        return self.items.get(item_id, 0) >= qty
    
    def get_quantity(self, item_id: str) -> int:
        """
        Get the quantity of an item in the inventory.
        
        Args:
            item_id: The unique identifier for the item
            
        Returns:
            int: Quantity of the item (0 if not present)
        """
        return self.items.get(item_id, 0)
    
    def list_items(self) -> str:
        """
        Get a formatted string of all items in the inventory.
        Uses ItemRegistry to get Chinese names.
        
        Returns:
            str: Comma-separated list of items with quantities,
                 or "Empty" if inventory is empty
        """
        if not self.items:
            return "Empty"
        
        registry = get_registry()
        formatted_items: List[str] = []
        
        for item_id, qty in self.items.items():
            # Get display name from registry
            name = registry.get_name(item_id)
            
            # Format with quantity if more than 1
            if qty > 1:
                formatted_items.append(f"{name} x{qty}")
            else:
                formatted_items.append(name)
        
        return ", ".join(formatted_items)
    
    def list_item_names(self) -> List[str]:
        """
        Get a list of item display names (with quantity when > 1) for prompt/UI.
        
        Returns:
            List[str]: e.g. ["Healing Potion x2", "Gold Coin x10"] or []
        """
        if not self.items:
            return []
        registry = get_registry()
        result: List[str] = []
        for item_id, qty in self.items.items():
            name = registry.get_name(item_id)
            if qty > 1:
                result.append(f"{name} x{qty}")
            else:
                result.append(name)
        return result
    
    def list_items_detailed(self) -> List[Dict[str, Any]]:
        """
        Get detailed information for all items in the inventory.
        
        Returns:
            List of dicts with item_id, quantity, and full item data
        """
        registry = get_registry()
        result: List[Dict[str, Any]] = []
        
        for item_id, qty in self.items.items():
            item_data = registry.get(item_id)
            result.append({
                "item_id": item_id,
                "quantity": qty,
                **item_data
            })
        
        return result
    
    def to_dict(self) -> Dict[str, int]:
        """
        Serialize inventory to dictionary for saving.
        
        Returns:
            Dict[str, int]: Copy of internal item storage
        """
        return self.items.copy()
    
    def from_dict(self, data: Dict[str, int]) -> None:
        """
        Deserialize inventory from dictionary (for loading).
        
        Args:
            data: Dictionary mapping item_id to quantity
        """
        self.items = data.copy() if data else {}
    
    def clear(self) -> None:
        """Clear all items from the inventory."""
        self.items.clear()
    
    def is_empty(self) -> bool:
        """Check if the inventory is empty."""
        return len(self.items) == 0
    
    def count_unique_items(self) -> int:
        """Get the number of unique item types in the inventory."""
        return len(self.items)
    
    def count_total_items(self) -> int:
        """Get the total quantity of all items."""
        return sum(self.items.values())


# Auto-load registry on module import (optional)
def init_registry(config_path: str = "config/items.yaml") -> bool:
    """
    Initialize the global item registry.
    
    Args:
        config_path: Path to the items.yaml file
        
    Returns:
        bool: True if loaded successfully
    """
    return _registry.load(config_path)
