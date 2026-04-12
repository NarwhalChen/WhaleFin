"""
后台任务生命周期管理

BackgroundManager: 追踪后台 agent 任务的 id / status / result
NotificationQueue: 任务完成后写入，run_loop 每轮开始前 drain 进 messages

使用方式:
    bg = BackgroundManager()
    task_id = bg.start(agent_type, task_desc, coro)

    # run_loop 每轮开始前:
    bg.drain_into(messages)
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class TaskRecord:
    task_id: str
    agent_type: str
    task_desc: str
    status: Literal["running", "done", "error"] = "running"
    result: str = ""


class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, TaskRecord] = {}
        self._asyncio_tasks: dict[str, object] = {}  # task_id → asyncio.Task，用于 cancel
        self._notify_queue: list[str] = []
        self._counter = 0

    def start(self, agent_type: str, task_desc: str, coro) -> str:
        """启动后台任务，返回 task_id。"""
        self._counter += 1
        task_id = f"bg_{self._counter}"

        record = TaskRecord(
            task_id=task_id,
            agent_type=agent_type,
            task_desc=task_desc,
        )
        self._tasks[task_id] = record

        t = asyncio.create_task(self._run(record, coro))
        self._asyncio_tasks[task_id] = t
        return task_id

    async def _run(self, record: TaskRecord, coro) -> None:
        try:
            result = await coro
            record.status = "done"
            record.result = result or ""
            self._notify_queue.append(
                f"[Background task {record.task_id} done] "
                f"{record.agent_type}: {record.result[:200]}"
            )
        except Exception as e:
            record.status = "error"
            record.result = str(e)
            self._notify_queue.append(
                f"[Background task {record.task_id} error] "
                f"{record.agent_type}: {e}"
            )

    def drain_into(self, messages: list) -> int:
        """
        把已完成的通知注入 messages，让 Claude 下一轮能看到结果。
        返回注入的条数。
        """
        if not self._notify_queue:
            return 0
        notifications = self._notify_queue.copy()
        self._notify_queue.clear()
        content = "\n".join(notifications)
        messages.append({"role": "user", "content": content})
        return len(notifications)
