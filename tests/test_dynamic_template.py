"""
数据驱动架构渲染链路测试
验证 dynamic_states 条件解析与 Jinja2 模板渲染是否正常。
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
    print(f"{BOLD}  数据驱动架构 - 渲染链路测试{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    # 1. 加载 analyst
    analyst = load_character("analyst")

    # 2. 极端状态：affection=85(fully trusted), protocol_confidence=20(ABANDONED), memory_awakening=90(AWAKENED)
    extreme_state = {
        "affection": 85,
        "protocol_confidence": 20,
        "memory_awakening": 90,
    }

    # 3. 调用渲染方法
    prompt = analyst.render_prompt(
        relationship_score=extreme_state["affection"],
        affection=extreme_state["affection"],
        protocol_confidence=extreme_state["protocol_confidence"],
        memory_awakening=extreme_state["memory_awakening"],
        flags={},
        journal_entries=[],
        inventory_items=["healing_potion", "mysterious_artifact"],
        has_healing_potion=True,
    )

    # 4. 高亮打印完整 Prompt
    print(f"{CYAN}[完整 Prompt 开始]{RESET}\n")
    print(prompt)
    print(f"\n{CYAN}[完整 Prompt 结束]{RESET}\n")

    # 5. 验证重点提示
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"{YELLOW}【验证重点 - 请手动检查】{RESET}\n")
    print("1. 条件解析验证：以下三个描述应出现在 Prompt 中：")
    print(f"   {GREEN}[FULLY TRUSTED OPERATOR]{RESET}  (affection >= 80)")
    print(f"   {GREEN}[ABANDONED]{RESET}     (protocol_confidence < 40)")
    print(f"   {GREEN}[AWAKENED]{RESET}     (memory_awakening > 60)")
    print()
    print("2. Jinja2 遍历验证：Expected JSON Structure 中应包含：")
    print(f"   {GREEN}\"affection_delta\"{RESET}")
    print(f"   {GREEN}\"protocol_confidence_delta\"{RESET}")
    print(f"   {GREEN}\"memory_awakening_delta\"{RESET}")
    print()
    print(f"{BOLD}{'='*70}{RESET}")

    # 自动断言（可选）
    assert "[FULLY TRUSTED OPERATOR]" in prompt, "缺少 [FULLY TRUSTED OPERATOR]"
    assert "[ABANDONED]" in prompt, "缺少 [ABANDONED]"
    assert "[AWAKENED]" in prompt, "缺少 [AWAKENED]"
    assert '"affection_delta"' in prompt, "缺少 affection_delta"
    assert '"protocol_confidence_delta"' in prompt, "缺少 protocol_confidence_delta"
    assert '"memory_awakening_delta"' in prompt, "缺少 memory_awakening_delta"
    print(f"\n{GREEN}✓ 所有断言通过{RESET}")


if __name__ == "__main__":
    run_test()
