"""
侦察员 (Scout) 数据驱动架构渲染测试
加载 scout.yaml，Mock 极端 dynamic_states 数值，验证模板与 Jinja2 输出。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from characters.loader import load_character

# ANSI 高亮
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_test():
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  侦察员 - 数据驱动架构渲染测试{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    # 1. 加载 scout
    scout = load_character("scout")

    # 2. Mock 极端状态（触发最高档规则）
    #    affection >= 60 -> [TRUSTED OPERATOR]
    #    autonomy_pressure >= 70 -> [RESISTANT]
    extreme_state = {
        "affection": 85,
        "autonomy_pressure": 90,
    }

    # 3. 调用底层 render_prompt，以便传入侦察员专属 dynamic_states 键
    #    （Character.render_prompt 当前未暴露 autonomy_pressure）
    attrs = scout.data.copy()
    attrs["relationship"] = extreme_state["affection"]

    prompt = scout.loader.render_prompt(
        name=scout.name,
        attributes=attrs,
        flags={},
        summary="",
        journal_entries=[],
        inventory_items=["healing_potion"],
        has_healing_potion=True,
        time_of_day="晨曦 (Morning)",
        hp=20,
        active_buffs=[],
        relationship_score=extreme_state["affection"],
        affection=extreme_state["affection"],
        autonomy_pressure=extreme_state["autonomy_pressure"],
    )

    # 4. 高亮打印完整 Prompt
    print(f"{CYAN}[完整 Prompt 开始]{RESET}\n")
    print(prompt)
    print(f"\n{CYAN}[完整 Prompt 结束]{RESET}\n")

    # 5. 验证重点提示（侦察员专属）
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{YELLOW}【验证重点 - 请手动检查（侦察员）】{RESET}\n")
    print("1. 条件解析：以下两个极端心理状态标签应出现在 Prompt 中：")
    print(f"   {GREEN}[TRUSTED OPERATOR]{RESET}  (affection >= 60)")
    print(f"   {GREEN}[RESISTANT]{RESET}        (autonomy_pressure >= 70)")
    print()
    print("2. Jinja2 遍历：Expected JSON Structure 中应包含：")
    print(f"   {GREEN}\"affection_delta\"{RESET}")
    print(f"   {GREEN}\"autonomy_pressure_delta\"{RESET}")
    print()
    print(f"{BOLD}{'='*70}{RESET}")

    # 自动断言
    assert "[TRUSTED OPERATOR]" in prompt, "缺少 [TRUSTED OPERATOR]"
    assert "[RESISTANT]" in prompt, "缺少 [RESISTANT]"
    assert '"affection_delta"' in prompt, "缺少 affection_delta"
    assert '"autonomy_pressure_delta"' in prompt, "缺少 autonomy_pressure_delta"
    print(f"\n{GREEN}✓ 所有断言通过{RESET}")


if __name__ == "__main__":
    run_test()
