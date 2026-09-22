"""Scheduled and background tasks.

Runs while the app is open (internal asyncio scheduler); can also emit a
Windows Task Scheduler registration command (no admin required) for tasks that
must run while Wotan is closed. Results are recorded in the inbox and shown as
a Windows notification.
"""

from __future__ import annotations

import asyncio
import platform
import shlex
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..db import Database
from ..logging_setup import get_logger
from ..util import new_id, utc_iso

log = get_logger("wotan.agent.scheduler", component="scheduler")


@dataclass
class ScheduledTask:
    id: str
    name: str
    prompt: str  # the user request to run
    cron: str = ""  # "every 30m", "daily 09:00", "once 2026-01-01T09:00", or "* * * * *"
    kind: str = "once"  # once | interval | daily | cron
    interval_seconds: float = 0
    next_run: float = 0
    enabled: bool = True
    runs: int = 0
    last_result: str = ""


def parse_when(when: str) -> tuple[str, float]:
    """Parse natural schedules: 'in 10 minutes', 'every 2h', 'daily 09:30', 'once <iso>'."""
    import re
    import time as _t
    import datetime as _dt

    w = when.strip().lower()
    m = re.match(r"in\s+(\d+)\s*(s|sec|second|seconds|m|min|minute|minutes|h|hour|hours|d|day|days)", w)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = m.group(2)[0]
        return "once", _t.time() + int(m.group(1)) * mult[unit]
    m = re.match(r"every\s+(\d+)\s*(s|sec|m|min|h|hour|hours|d|day|days)", w)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return "interval", float(int(m.group(1)) * mult[m.group(2)[0]])
    m = re.match(r"daily\s+(\d{1,2}):(\d{2})", w)
    if m:
        now = _dt.datetime.now()
        target = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if target.timestamp() <= _t.time():
            target += _dt.timedelta(days=1)
        return "daily", target.timestamp()
    try:
        ts = _dt.datetime.fromisoformat(when.strip()).timestamp()
        return "once", ts
    except ValueError:
        pass
    return "cron", 0.0


def windows_task_command(task: ScheduledTask, wotan_command: str) -> str:
    """Build a Task Scheduler registration command (works without admin for the
    current user). The user runs it explicitly - Wotan never does it silently."""
    if platform.system() != "Windows":
        return "# Windows only: register a Task Scheduler task for this schedule"
    trigger = "/sc hourly /mo 1" if task.kind == "interval" else "/sc daily /st 09:00"
    return (
        f'schtasks /create /tn "Wotan-{task.name}" /tr "{wotan_command}" {trigger} /f'
    )


class Scheduler:
    def __init__(self, db: Database, run_prompt: Callable[[str], Awaitable[str]] | None = None) -> None:
        self.db = db
        self.run_prompt = run_prompt
        self.tasks: dict[str, ScheduledTask] = {}
        self._task: asyncio.Task | None = None
        self._notify: Callable[[str, str], None] | None = None

    def set_runner(self, run_prompt: Callable[[str], Awaitable[str]]) -> None:
        self.run_prompt = run_prompt

    def add(self, name: str, prompt: str, when: str) -> ScheduledTask:
        kind, ts = parse_when(when)
        interval = ts if kind == "interval" else 0.0
        next_run = ts if kind != "interval" else 0.0
        st = ScheduledTask(
            id=new_id("sch-"),
            name=name or prompt[:40],
            prompt=prompt,
            cron=when,
            kind=kind,
            interval_seconds=interval,
            next_run=next_run,
        )
        self.tasks[st.id] = st
        log.info("scheduled task added", extra={"data": {"name": st.name, "when": when}})
        return st

    def cancel(self, task_id: str) -> bool:
        return self.tasks.pop(task_id, None) is not None

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": t.id, "name": t.name, "prompt": t.prompt, "when": t.cron, "kind": t.kind,
                "next_run": t.next_run or None, "enabled": t.enabled, "runs": t.runs, "last_result": t.last_result,
                "windows_command": windows_task_command(t, f'wotan run "{t.prompt}"'),
            }
            for t in self.tasks.values()
        ]

    def notify(self, title: str, body: str) -> None:
        self.db.add_inbox("scheduled_result", title, body)
        if platform.system() == "Windows":
            try:
                import subprocess

                ps = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                    f"$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('ToastText02');"
                    f"$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode('{title}')) | Out-Null;"
                    f"$t.GetElementsByTagName('text').Item(1).AppendChild($t.CreateTextNode('{body[:200]}')) | Out-Null;"
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Wotan').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
                )
                subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    async def _run_due(self) -> None:
        import time

        now = time.time()
        for st in list(self.tasks.values()):
            if not st.enabled:
                continue
            if st.kind == "interval":
                # interval tasks first run immediately, then every interval_seconds
                if st.next_run == 0.0:
                    st.next_run = now
                if now < st.next_run:
                    continue
                st.next_run = now + st.interval_seconds
            else:
                if st.next_run == 0.0 or now < st.next_run:
                    continue
                st.enabled = False if st.kind == "once" else st.enabled
                if st.kind == "daily":
                    st.next_run = now + 86400
            st.runs += 1
            result = ""
            try:
                if self.run_prompt is not None:
                    result = await self.run_prompt(st.prompt)
                else:
                    result = "no runner attached"
            except Exception as exc:
                result = f"task failed: {exc}"
            st.last_result = result[:300]
            self.notify(f"Wotan task: {st.name}", st.last_result or "done (no output)")
            log.info("scheduled task executed", extra={"data": {"name": st.name, "runs": st.runs}})

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self._run_due()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(2.0)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
