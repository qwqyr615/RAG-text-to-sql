"""多轮会话存储测试。

用 ``agent/sessions/`` 作为落盘目录（已加入 .gitignore），只创建与删除单个文件，
不做目录枚举，因此在受限环境下也能跑。
"""

from __future__ import annotations

import json

from core.config import BASE_DIR
from sessions import (
    ConversationTurn,
    InMemorySessionStore,
    JsonFileSessionStore,
    build_session_store,
    new_session_id,
)

SCRATCH_DIR = BASE_DIR / "sessions"


def _turn(index: int) -> ConversationTurn:
    return ConversationTurn(
        question=f"问题{index}", answer=f"结论{index}", sql=f"SELECT {index}"
    )


def test_in_memory_keeps_only_recent_turns() -> None:
    store = InMemorySessionStore(max_turns=2)
    for index in range(5):
        store.append("s1", _turn(index))

    turns = store.load("s1")
    assert [turn.question for turn in turns] == ["问题3", "问题4"]


def test_max_turns_zero_disables_history() -> None:
    store = InMemorySessionStore(max_turns=0)
    store.append("s1", _turn(1))
    assert store.load("s1") == []


def test_clear_and_list_sessions() -> None:
    store = InMemorySessionStore(max_turns=3)
    store.append("alpha", _turn(1))
    store.append("beta", _turn(2))

    assert store.list_sessions() == ["alpha", "beta"]
    store.clear("alpha")
    assert store.load("alpha") == []
    assert store.list_sessions() == ["beta"]


def test_answer_text_includes_sql_for_followups() -> None:
    turn = ConversationTurn(question="q", answer="a", sql="SELECT 1")
    text = turn.answer_text()
    assert "a" in text
    assert "SELECT 1" in text
    assert "上一轮生成的 SQL" in text


def test_answer_text_truncates_long_sql() -> None:
    turn = ConversationTurn(question="q", answer="a", sql="SELECT " + "x" * 900)
    text = turn.answer_text(sql_limit=50)
    assert len(text) < 200
    assert "…" in text
    assert "x" * 900 not in text


def test_json_file_store_round_trip() -> None:
    store = JsonFileSessionStore(directory=SCRATCH_DIR, max_turns=2)
    session_id = f"unit-{new_session_id()}"
    path = store._path(session_id)

    try:
        store.append(session_id, _turn(1))
        store.append(session_id, _turn(2))
        store.append(session_id, _turn(3))

        turns = store.load(session_id)
        assert [turn.question for turn in turns] == ["问题2", "问题3"]

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, list) and len(payload) == 2
    finally:
        store.clear(session_id)

    assert not path.exists()


def test_json_file_store_tolerates_broken_file() -> None:
    store = JsonFileSessionStore(directory=SCRATCH_DIR, max_turns=2)
    session_id = f"broken-{new_session_id()}"
    path = store._path(session_id)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    try:
        assert store.load(session_id) == []
    finally:
        store.clear(session_id)


def test_non_ascii_session_id_is_safely_named() -> None:
    store = JsonFileSessionStore(directory=SCRATCH_DIR, max_turns=1)
    session_id = "会话/..\\非法"
    path = store._path(session_id)

    assert path.parent == SCRATCH_DIR
    assert path.name.endswith(".json")
    assert "/" not in path.name and "\\" not in path.name


def test_build_session_store_defaults_to_memory() -> None:
    store = build_session_store(kind="unknown", max_turns=1)
    assert isinstance(store, InMemorySessionStore)
    assert store.max_turns == 1
