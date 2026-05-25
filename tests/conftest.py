"""
测试级依赖替身。

为本地未安装的可选依赖提供最小 stub，避免测试因环境缺包而在收集阶段失败。
"""

import sys
import types


def _install_langgraph_stub() -> None:
    if "langgraph" in sys.modules:
        return

    langgraph_module = types.ModuleType("langgraph")
    graph_module = types.ModuleType("langgraph.graph")
    message_module = types.ModuleType("langgraph.graph.message")
    checkpoint_module = types.ModuleType("langgraph.checkpoint")
    checkpoint_sqlite_module = types.ModuleType("langgraph.checkpoint.sqlite")
    checkpoint_sqlite_aio_module = types.ModuleType("langgraph.checkpoint.sqlite.aio")

    class StateGraph:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def add_node(self, *args, **kwargs):
            return self

        def add_edge(self, *args, **kwargs):
            return self

        def add_conditional_edges(self, *args, **kwargs):
            return self

        def compile(self, *args, **kwargs):
            return self

    def add_messages(existing, new):
        return list(existing or []) + list(new or [])

    class AsyncSqliteSaver:
        @classmethod
        def from_conn_string(cls, conn_string):
            class _AsyncSaverContext:
                async def __aenter__(self_inner):
                    return {"conn_string": conn_string}

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _AsyncSaverContext()

    graph_module.START = "START"
    graph_module.END = "END"
    graph_module.StateGraph = StateGraph
    message_module.add_messages = add_messages
    message_module.REMOVE_ALL_MESSAGES = "__REMOVE_ALL_MESSAGES__"
    checkpoint_sqlite_aio_module.AsyncSqliteSaver = AsyncSqliteSaver

    sys.modules["langgraph"] = langgraph_module
    sys.modules["langgraph.graph"] = graph_module
    sys.modules["langgraph.graph.message"] = message_module
    sys.modules["langgraph.checkpoint"] = checkpoint_module
    sys.modules["langgraph.checkpoint.sqlite"] = checkpoint_sqlite_module
    sys.modules["langgraph.checkpoint.sqlite.aio"] = checkpoint_sqlite_aio_module


def _install_langchain_openai_stub() -> None:
    module = types.ModuleType("langchain_openai")

    class ChatOpenAI:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def bind_tools(self, tools):
            self.tools = tools
            return self

        async def ainvoke(self, *args, **kwargs):
            raise AssertionError("测试中应显式 mock ChatOpenAI.ainvoke。")

        def invoke(self, *args, **kwargs):
            raise AssertionError("测试中应显式 mock ChatOpenAI.invoke。")

    module.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = module


def _install_chromadb_stub() -> None:
    module = types.ModuleType("chromadb")

    class _Collection:
        def __init__(self):
            self.documents = []
            self.metadatas = []
            self.ids = []

        def add(self, documents, metadatas, ids):
            self.documents.extend(documents)
            self.metadatas.extend(metadatas)
            self.ids.extend(ids)

        def count(self):
            return len(self.documents)

        def query(self, query_texts, n_results):
            return {"documents": [self.documents[:n_results]]}

    class PersistentClient:
        def __init__(self, path):
            self.path = path
            self.collections = {}

        def get_or_create_collection(self, name):
            self.collections.setdefault(name, _Collection())
            return self.collections[name]

        def create_collection(self, name):
            self.collections[name] = _Collection()
            return self.collections[name]

        def delete_collection(self, name):
            self.collections.pop(name, None)

    module.PersistentClient = PersistentClient
    sys.modules["chromadb"] = module


try:
    import langgraph  # type: ignore  # noqa: F401
except ImportError:
    _install_langgraph_stub()

_install_langchain_openai_stub()
try:
    import chromadb  # type: ignore  # noqa: F401
except ImportError:
    _install_chromadb_stub()
