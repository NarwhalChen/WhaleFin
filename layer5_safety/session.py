"""
Session Persistence

JSONL append-only，每条消息单独一行写入，crash-safe。

存储路径: ~/.whalefin/projects/{encoded-cwd}/{uuid}.jsonl
session_id: UUID v4，启动时生成

last 指针: ~/.whalefin/projects/{encoded-cwd}/last — 存一行 session_id，
每次新建或 resume session 时覆盖写，不依赖 mtime（对齐 CC 设计）。

resume: --resume {session_id} 或 --resume last
文件被手动删除则提示并开新 session。
"""

import json
import uuid
from pathlib import Path


def _sessions_dir() -> Path:
    encoded = str(Path.cwd()).replace("/", "-")
    return Path.home() / ".whalefin" / "projects" / encoded


def _last_ptr() -> Path:
    return _sessions_dir() / "last"


def new_session_id() -> str:
    return str(uuid.uuid4())


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.jsonl"


def mark_active(session_id: str) -> None:
    """新建或 resume 时调用，更新 last 指针。"""
    d = _sessions_dir()
    d.mkdir(parents=True, exist_ok=True)
    _last_ptr().write_text(session_id + "\n", encoding="utf-8")


def append_message(session_id: str, message: dict) -> None:
    """追加一条消息到 session 文件。"""
    _sessions_dir().mkdir(parents=True, exist_ok=True)
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
    --resume last → 读 last 指针文件
    --resume {id} → 原样返回
    """
    if resume_arg != "last":
        return resume_arg
    ptr = _last_ptr()
    if not ptr.exists():
        raise FileNotFoundError("没有找到任何 session 文件")
    return ptr.read_text(encoding="utf-8").strip()
