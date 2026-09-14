"""多轮会话存储。

``Text2SQLAgent`` 本身是无状态的：每次 ``ask()`` 都重新组装 Prompt 并调用一次
LangChain Agent。多轮上下文由本模块按 ``session_id`` 维护，并以 ``chat_history``
的形式注入 Agent 的 Prompt，因此追问（例如「那 Night 班次呢」）可以复用上一轮的
问题、SQL 与结论。

两种实现：

- :class:`InMemorySessionStore`：进程内，适合单机演示与单元测试；
- :class:`JsonFileSessionStore`：每个会话一个 JSON 文件，进程重启后不丢。

两者都只保留最近 ``max_turns`` 轮，避免 Prompt 无限膨胀。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from core.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

__all__ = [
    "SESSIONS_DIR",
    "ConversationTurn",
    "InMemorySessionStore",
    "JsonFileSessionStore",
    "SessionStore",
    "build_session_store",
    "new_session_id",
]

SESSIONS_DIR = BASE_DIR / "sessions"


def new_session_id() -> str:
    """生成一个新的会话 ID。"""
    return uuid.uuid4().hex[:12]


@dataclass
class ConversationTurn:
    """一轮问答。"""

    question: str
    answer: str = ""
    sql: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def answer_text(self, sql_limit: int = 600) -> str:
        """作为 AI 消息的内容：结论 + 上一轮 SQL。

        带上 SQL 是为了让模型在追问时能改写上一轮的查询，而不是从零重新猜表结构。
        """
        parts = [self.answer.strip() or "（上一轮未返回结论）"]
        if self.sql:
            sql = self.sql.strip()
            if len(sql) > sql_limit:
                sql = sql[:sql_limit] + " …"
            parts.append(f"（上一轮生成的 SQL：{sql}）")
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ConversationTurn":
        return cls(
            question=str(data.get("question") or ""),
            answer=str(data.get("answer") or ""),
            sql=(str(data["sql"]) if data.get("sql") else None),
            timestamp=str(data.get("timestamp") or ""),
        )


class SessionStore(Protocol):
    """会话存储接口。"""

    max_turns: int

    def load(self, session_id: str) -> list[ConversationTurn]:
        """读取一个会话的历史（按时间正序）。"""

    def append(self, session_id: str, turn: ConversationTurn) -> None:
        """追加一轮问答，并裁剪到 ``max_turns``。"""

    def clear(self, session_id: str) -> None:
        """清空一个会话。"""

    def list_sessions(self) -> list[str]:
        """列出全部会话 ID。"""


class InMemorySessionStore:
    """进程内会话存储。"""

    def __init__(self, max_turns: int = 6) -> None:
        self.max_turns = max(0, int(max_turns))
        self._sessions: dict[str, list[ConversationTurn]] = {}

    def load(self, session_id: str) -> list[ConversationTurn]:
        return list(self._sessions.get(session_id, []))

    def append(self, session_id: str, turn: ConversationTurn) -> None:
        if self.max_turns == 0:
            return
        turns = self._sessions.setdefault(session_id, [])
        turns.append(turn)
        if len(turns) > self.max_turns:
            del turns[: len(turns) - self.max_turns]

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        return sorted(self._sessions)


class JsonFileSessionStore:
    """每个会话一个 JSON 文件的会话存储。"""

    def __init__(self, directory: Path | None = None, max_turns: int = 6) -> None:
        self.max_turns = max(0, int(max_turns))
        self.directory = Path(directory) if directory else SESSIONS_DIR

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{_safe_file_name(session_id)}.json"

    def load(self, session_id: str) -> list[ConversationTurn]:
        path = self._path(session_id)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("会话文件解析失败，按空历史处理：%s（%s）", path, exc)
            return []
        if not isinstance(raw, list):
            return []
        turns = [
            ConversationTurn.from_dict(item) for item in raw if isinstance(item, dict)
        ]
        return turns[-self.max_turns :] if self.max_turns else []

    def append(self, session_id: str, turn: ConversationTurn) -> None:
        if self.max_turns == 0:
            return
        turns = self.load(session_id)
        turns.append(turn)
        if len(turns) > self.max_turns:
            del turns[: len(turns) - self.max_turns]

        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [item.to_dict() for item in turns], ensure_ascii=False, indent=2
        )
        self._path(session_id).write_text(payload, encoding="utf-8")

    def clear(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.is_file():
            path.unlink()

    def list_sessions(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))


def _safe_file_name(session_id: str) -> str:
    """把会话 ID 转成安全文件名，非 ASCII 时附加哈希避免碰撞。"""
    cleaned = re.sub(r"[^0-9A-Za-z_.\-]", "_", session_id).strip("_")
    if cleaned == session_id and cleaned:
        return cleaned
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:8]
    return f"{(cleaned or 'session')[:48]}_{digest}"


def build_session_store(
    kind: str | None = None, max_turns: int | None = None
) -> SessionStore:
    """按配置构建会话存储。"""
    resolved_kind = (kind or settings.session_store or "memory").strip().lower()
    turns = settings.session_max_turns if max_turns is None else max_turns

    if resolved_kind == "file":
        return JsonFileSessionStore(max_turns=turns)
    if resolved_kind != "memory":
        logger.warning("未知的 SESSION_STORE=%s，回退为 memory", resolved_kind)
    return InMemorySessionStore(max_turns=turns)
