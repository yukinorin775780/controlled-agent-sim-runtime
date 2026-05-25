"""
RAG 情景记忆库（ChromaDB）存取验证。

从仓库根目录运行：
  python tests/test_memory_rag.py
或（需已安装 chromadb）：
  pytest tests/test_memory_rag.py -s
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def run_test():
    from core.systems.memory_rag import episodic_memory

    print("🧠 正在唤醒深层记忆库...")
    episodic_memory.clear_all_memories()

    # 1. 模拟发生了一些影响深远的剧情（写入记忆）
    print("\n📝 正在写入测试记忆...")
    episodic_memory.add_memory(
        text="在幽暗地域的营地里，玩家把一朵珍贵的夜兰花送给了分析员。分析员非常感动，微笑着将其珍藏了起来。",
        speaker="analyst",
    )
    episodic_memory.add_memory(
        text="玩家在清晨的营地里极其强硬地命令战术员收下治疗药水。战术员迫于威吓收下了，但觉得受到了屈辱，发誓要在战斗中证明自己。",
        speaker="tactician",
    )
    episodic_memory.add_memory(
        text="侦察员在半夜试图偷吸玩家的血，被玩家一脚踹飞。他揉着肚子抱怨玩家太粗鲁。",
        speaker="scout",
    )

    # 2. 模拟玩家很久之后的一句提问（检索记忆）
    query = "对了分析员，你还记得我以前送过你什么植物吗？"
    print(f"\n🗣️ 玩家提问: '{query}'")
    print("🔍 正在通过语义检索相关记忆...\n")

    results = episodic_memory.retrieve_relevant_memories(query, top_k=2)

    if results:
        for i, mem in enumerate(results):
            print(f"💡 [回忆涌现 {i+1}]: {mem}")
    else:
        print("❌ 记忆一片空白，什么也没想起来。")


def test_memory_rag_retrieval():
    """pytest：验证写入后语义检索能返回至少一条相关记忆。"""
    import pytest

    pytest.importorskip("chromadb")
    from core.systems.memory_rag import episodic_memory

    run_test()
    query = "对了分析员，你还记得我以前送过你什么植物吗？"
    results = episodic_memory.retrieve_relevant_memories(query, top_k=2)
    assert results, "retrieve_relevant_memories 应返回至少一条记忆"
    assert any("夜兰" in m or "分析员" in m or "花" in m for m in results), (
        f"检索结果应与「送植物/分析员」相关，实际: {results}"
    )


if __name__ == "__main__":
    run_test()
