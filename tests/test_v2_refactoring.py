"""
V2 重构回归测试。

支持：
- 从项目根目录：`pytest tests/test_v2_refactoring.py` 或 `python -m pytest tests/...`
- 从任意目录：`python tests/test_v2_refactoring.py`（依赖下方 sys.path 注入）
"""
import sys
from pathlib import Path

# 将仓库根目录加入 path，否则直接运行本文件会出现 No module named 'core'
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import unittest
from typing import Any, Callable, Optional
from unittest.mock import patch

# =====================================================================
# 测试阶段一：验证物理文件拆分 (Directory Restructuring)
# =====================================================================
world_tick_node: Optional[Callable[..., Any]] = None
mechanics_node: Optional[Callable[..., Any]] = None
try:
    from core.graph.nodes.dm import dm_node  # noqa: F401 — 验证 dm 子模块可导入
    from core.graph.nodes.input import world_tick_node as _world_tick_node
    from core.graph.nodes.mechanics import mechanics_node as _mechanics_node
    from core.graph.nodes.utils import load_default_entities  # noqa: F401

    world_tick_node = _world_tick_node
    mechanics_node = _mechanics_node
    IMPORTS_OK = True
except ImportError as e:
    IMPORTS_OK = False
    IMPORT_ERROR = str(e)


class TestV2Refactoring(unittest.TestCase):

    def test_01_directory_restructuring(self):
        """验证 1：物理文件拆分是否成功，import 路径是否畅通"""
        msg = (
            f"模块导入失败，拆分可能未完成或存在遗漏: {IMPORT_ERROR if not IMPORTS_OK else ''}\n"
            "提示：请在项目根目录的 venv 中安装依赖（含 langgraph），例如 pip install -r requirements.txt"
        )
        self.assertTrue(IMPORTS_OK, msg)

    def test_02_remove_hardcoding(self):
        """验证 2：清理硬编码兜底 (world_tick_node 必须动态加载实体)"""
        if not IMPORTS_OK:
            self.skipTest("导入失败，跳过本项测试")
        
        # 传入一个没有 entities 的初始空状态
        state = {"turn_count": 0, "entities": None}
        assert world_tick_node is not None
        result = world_tick_node(state)
        
        # 验证是否动态生成了 entities
        self.assertIn("entities", result, "world_tick_node 应该返回 entities 字典")
        entities = result["entities"]
        
        # 验证是否通过 loader 动态加载了 YAML（至少存在分析员和侦察员，且不能是写死的空字典）
        self.assertTrue(len(entities) >= 2, "实体字典不应为空，必须从 characters/ 目录动态加载")
        self.assertIn("analyst", entities)
        self.assertIn("scout", entities)
        
        # 验证数据驱动是否生效（比如分析员应该带有 inventory 和 affection）
        self.assertIn("inventory", entities["analyst"])
        self.assertIn("affection", entities["scout"])

    @patch("core.graph.nodes.mechanics.mechanics") # 拦截 nodes/mechanics.py 中对 systems/mechanics.py 的调用
    def test_03_logic_decoupling(self, mock_mechanics_system):
        """验证 3：物理机制与情感彻底解耦 (机制节点不再强行扣除好感度)"""
        if not IMPORTS_OK:
            self.skipTest("导入失败，跳过本项测试")

        # 模拟底层物理引擎返回：掷骰失败，且【不再返回 relationship_delta】
        mock_mechanics_system.execute_skill_check.return_value = {
            "journal_events": ["Skill Check | INTIMIDATION | DC 18 | Roll 8 vs 18 | Result: FAILURE"],
            "raw_roll_data": {"intent": "INTIMIDATION", "result": {"is_success": False}}
        }

        # 构建触发威吓检定的 Graph State
        state = {
            "intent": "INTIMIDATION",
            "is_probing_secret": False,
            "current_speaker": "scout",
            "entities": {
                "scout": {"hp": 20, "affection": 10} # 初始好感度
            }
        }

        # 执行机制节点
        assert mechanics_node is not None
        result = mechanics_node(state)

        # 断言 A：机制节点必须忠实传递日志和骰子数据
        self.assertIn("journal_events", result)
        self.assertIn("latest_roll", result)

        # 断言 B：绝对禁止机制节点修改 entities (好感度)
        self.assertNotIn(
            "entities", 
            result, 
            "🚨 解耦失败！mechanics_node 仍然在试图修改实体状态（如好感度）。请检查代码是否清理干净。"
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)