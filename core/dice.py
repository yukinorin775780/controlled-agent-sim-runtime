"""
V2 兼容层：core.dice 重定向至 core.systems.dice。
供 ui/renderer.py 等未修改模块使用。
"""

from core.systems.dice import (
    CheckResult,
    roll_d20,
    get_check_result_text,
)

__all__ = ["CheckResult", "roll_d20", "get_check_result_text"]
