"""
Registry 保护性测试。
锁定 items.yaml 与 weapons.yaml 的统一加载和查询契约。
"""

from core.systems.inventory import get_registry, init_registry


def test_registry_loads_items_and_weapons_from_config_directory():
    assert init_registry("config/items.yaml") is True

    registry = get_registry()
    potion = registry.get("healing_potion")
    scimitar = registry.get("scimitar")
    rusty_dagger = registry.get("rusty_dagger")
    scale_mail = registry.get("Scale Mail")

    assert potion["type"] == "consumable"
    assert scimitar["equip_slot"] == "main_hand"
    assert scimitar["damage_dice"] == "1d6"
    assert scimitar["range"] == 1
    assert rusty_dagger["damage_dice"] == "1d4"
    assert scale_mail["equip_slot"] == "armor"
    assert registry.get_name("rusty_dagger") == "生锈匕首"
    assert registry.resolve_item_id("Mace") == "mace"
    assert registry.resolve_item_id("Dagger") == "rusty_dagger"
    assert registry.resolve_item_id("Scale Mail") == "scale_mail"
    assert "healing_potion" in registry.all_items()
    assert "scimitar" in registry.all_items()
