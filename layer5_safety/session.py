"""
Session Persistence

JSONL append-only，每条消息单独一行写入，crash-safe。

存储路径: ~/.whalefin/sessions/{session_id}.jsonl
session_id: YYYYMMDD-HHMMSS，启动时生成

resume: --resume {session_id} 或 --resume last
文件被手动删除则提示并开新 session。
"""

import json
from datetime import datetime
from pathlib import Path


SESSIONS_DIR = Path.home() / ".whalefin" / "sessions"


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.jsonl"


def append_message(session_id: str, message: dict) -> None:
    """追加一条消息到 session 文件。"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _session_path(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")


def load_session(session_id: str) -> list | None:
    """
    读取 session，返回 messages list。
    文件不存在返回 None（调用方处理提示）。
    """
    path = _session_path(session_id)
    if not path.exists():
        return None
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            messages.append(json.loads(line))
    return messages


def resolve_session_id(resume_arg: str) -> str:
    """
    --resume last → 返回最新的 session_id
    --resume {id} → 原样返回
    """
    if resume_arg != "last":
        return resume_arg
    files = sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("没有找到任何 session 文件")
    return files[0].stem
