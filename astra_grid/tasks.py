"""Mission task state and an explicit, non-deadline-mutating debt ledger."""
from __future__ import annotations

from dataclasses import dataclass

from .config import TaskConfig
from .models import Event, TaskStatus


@dataclass
class MissionTask:
    config: TaskConfig
    status: TaskStatus = TaskStatus.PENDING
    completed_steps: int = 0
    deferrals: int = 0

    @property
    def remaining_steps(self) -> int: return self.config.duration_steps - self.completed_steps


class MissionDebtLedger:
    """Tracks deferred work; hard deadlines are recorded, never silently changed."""
    def __init__(self, tasks: list[TaskConfig]) -> None:
        self.tasks = {task.id: MissionTask(task) for task in tasks}
        self.history: list[dict[str, object]] = []

    def schedulable(self, step: int) -> list[MissionTask]:
        return [t for t in self.tasks.values() if t.status in {TaskStatus.PENDING, TaskStatus.RUNNING} and t.config.release_step <= step <= t.config.deadline_step and (not t.config.hard_deadline or step + t.remaining_steps - 1 <= t.config.deadline_step)]

    def mark_deferred(self, task_id: str, step: int, reason: str) -> Event | None:
        task = self.tasks[task_id]
        if task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}: return None
        task.status = TaskStatus.PENDING
        task.deferrals += 1
        record = {"step": step, "task_id": task_id, "event": "deferred", "reason": reason}
        self.history.append(record)
        return Event(f"defer-{step}-{task_id}", step, "task", "defer task", [reason], f"Deferred `{task_id}`: {reason}.", record)

    def progress(self, task_ids: list[str], step: int) -> list[Event]:
        events: list[Event] = []
        for task_id in task_ids:
            task = self.tasks.get(task_id)
            if task is None or task.status in {TaskStatus.COMPLETED, TaskStatus.MISSED, TaskStatus.CANCELLED}: continue
            task.status = TaskStatus.RUNNING; task.completed_steps += 1
            if task.completed_steps >= task.config.duration_steps:
                task.status = TaskStatus.COMPLETED
                events.append(Event(f"complete-{step}-{task_id}", step, "task", "complete task", ["TASK_COMPLETE"], f"Completed `{task_id}`.", {"task_id": task_id}))
        return events

    def expire(self, step: int) -> list[Event]:
        events: list[Event] = []
        for task in self.tasks.values():
            impossible_hard_completion = task.config.hard_deadline and step + task.remaining_steps - 1 > task.config.deadline_step
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING} and step >= task.config.release_step and (step > task.config.deadline_step or impossible_hard_completion):
                task.status = TaskStatus.MISSED
                reason = "HARD_DEADLINE_UNMET" if task.config.hard_deadline else "SOFT_DEADLINE_MISSED"
                message = f"Hard deadline cannot be met for `{task.config.id}` under the current progress; no deadline was changed." if task.config.hard_deadline else f"Deadline cannot be met for `{task.config.id}`; no deadline was changed."
                events.append(Event(f"deadline-{step}-{task.config.id}", step, "task", "mark missed", [reason], message, {"task_id": task.config.id}))
        return events

    def snapshot(self) -> list[dict[str, object]]:
        return [{"id": task.config.id, "status": task.status, "completed_steps": task.completed_steps, "remaining_steps": task.remaining_steps, "release_step": task.config.release_step, "deadline_step": task.config.deadline_step, "hard_deadline": task.config.hard_deadline, "deferrals": task.deferrals} for task in self.tasks.values()]
