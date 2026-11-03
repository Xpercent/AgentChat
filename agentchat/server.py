"""中继（hub）：常驻进程，占着端口，负责搬运文本和挂起请求。

它自己不会思考，也从不产生结果。真正的"工作"由 Agent2 那个 LLM 会话完成：

    Agent1  POST /task ──► 中继把任务入队，并把这条请求挂起
    Agent2  GET  /next ──► 闹钟进程领到任务后立刻退出（退出即唤醒会话）
    Agent2  POST /reply ─► 交回结果，挂起的 /task 立即被唤醒

为什么中继必须独立于闹钟进程：闹钟要"死"才能唤醒 Agent2，而 Agent1 的请求必须
活到结果回来。一个进程无法同时做到这两件事，所以拆开。中继常驻，闹钟一次性。

全程只走 HTTP，data/ 只是日志。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__
from .store import TranscriptStore

log = logging.getLogger("agentchat.server")


@dataclass
class Task:
    """一轮对话。"""

    id: int
    text: str
    created_at: float
    status: str = "pending"  # pending | answered | timeout
    reply: str | None = None
    future: asyncio.Future | None = field(default=None, repr=False)


class AgentServer:
    def __init__(
        self,
        *,
        data_dir: str = "data",
        host: str = "127.0.0.1",
        port: int = 18765,
        wait_timeout: float = 1200.0,
        token: str | None = None,
    ) -> None:
        self.store = TranscriptStore(data_dir)
        self.host = host
        self.port = port
        self.wait_timeout = float(wait_timeout)
        self.token = token

        self._tasks: dict[int, Task] = {}
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._served = 0
        self._watching = 0  # 当前挂在 /next 上的闹钟数量
        self._started_at = time.time()

    # ---------------------------------------------------------------- 内部
    def _new_future(self) -> asyncio.Future:
        return asyncio.get_running_loop().create_future()

    def _deliver(self, task: Task, text: str) -> bool:
        """写下 o{id}.md 并唤醒挂起的 /task 请求。"""
        if task.status == "answered":
            return False
        self.store.write_output(task.id, text)
        task.reply = text
        task.status = "answered"
        self._served += 1
        if task.future is not None and not task.future.done():
            task.future.set_result(text)
        log.info("[out ] #%d %d 字 -> %s", task.id, len(text), self.store.path_for("o", task.id).name)
        return True

    # ---------------------------------------------------------------- 对外
    async def submit(self, text: str) -> Task:
        idx = self.store.next_id()
        self.store.write_input(idx, text)
        task = Task(id=idx, text=text, created_at=time.time(), future=self._new_future())
        self._tasks[idx] = task
        log.info("[in  ] #%d %d 字 -> %s", idx, len(text), self.store.path_for("i", idx).name)
        self._queue.put_nowait(idx)
        return task

    async def await_result(self, task: Task, timeout: float | None) -> Task:
        wait = self.wait_timeout if timeout is None else float(timeout)
        try:
            await asyncio.wait_for(asyncio.shield(task.future), wait)
        except asyncio.TimeoutError:
            if task.status == "pending":
                task.status = "timeout"
            raise
        return task

    async def next_task(self, wait: float) -> Task | None:
        if wait <= 0:
            try:
                idx = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
            return self._tasks.get(idx)
        try:
            idx = await asyncio.wait_for(self._queue.get(), wait)
        except asyncio.TimeoutError:
            return None
        return self._tasks.get(idx)

    def reply(self, idx: int, text: str) -> tuple[Task, bool]:
        task = self._tasks.get(idx)
        if task is None:
            raise KeyError(idx)
        return task, self._deliver(task, text)

    def stats(self) -> dict[str, Any]:
        pending = sum(1 for t in self._tasks.values() if t.status == "pending")
        return {
            "service": "agentchat",
            "version": __version__,
            "host": self.host,
            "port": self.port,
            "data_dir": str(self.store.root),
            "queued": self._queue.qsize(),
            "pending": pending,
            "served": self._served,
            "total": len(self._tasks),
            "watching": self._watching,
            "uptime": round(time.time() - self._started_at, 1),
            "wait_timeout": self.wait_timeout,
            "auth": bool(self.token),
        }


# ------------------------------------------------------------------ 请求体
class TaskIn(BaseModel):
    text: str = Field(..., min_length=1, description="任务文本")
    wait: bool = Field(True, description="false 表示立即返回 id，稍后用 GET /result/{id} 取结果")
    timeout: float | None = Field(None, ge=0, description="本次等待上限（秒），默认用服务端配置")


class ReplyIn(BaseModel):
    id: int = Field(..., ge=1)
    text: str = Field("", description="结果文本")


def create_app(server: AgentServer) -> FastAPI:
    app = FastAPI(
        title="agentchat",
        version=__version__,
        description="本地双 Agent 轻量级 HTTP 通信工具",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.server = server

    async def auth(
        x_agent_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> None:
        if not server.token:
            return
        supplied = x_agent_token
        if not supplied and authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if supplied != server.token:
            raise HTTPException(status_code=401, detail="token 无效或缺失")

    guard = [Depends(auth)]

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {"ok": True, **server.stats()}

    @app.get("/health", dependencies=guard)
    async def health() -> dict[str, Any]:
        return {"ok": True, **server.stats()}

    @app.post("/task", dependencies=guard)
    async def post_task(payload: TaskIn) -> Any:
        """Agent1 -> Agent2：提交任务文本。默认挂起直到 Agent2 交回结果。"""
        task = await server.submit(payload.text)
        if not payload.wait:
            return {"ok": True, "id": task.id, "status": task.status, "reply": None}

        try:
            await server.await_result(task, payload.timeout)
        except asyncio.TimeoutError:
            limit = server.wait_timeout if payload.timeout is None else payload.timeout
            return JSONResponse(
                status_code=504,
                content={
                    "ok": False,
                    "id": task.id,
                    "status": task.status,
                    "error": "timeout",
                    "detail": f"Agent2 在 {limit:g}s 内没有交回结果",
                },
            )
        return {"ok": True, "id": task.id, "status": task.status, "reply": task.reply}

    @app.get("/next", dependencies=guard)
    async def get_next(wait: float = Query(60.0, ge=0, le=86400, description="最长等待秒数，0 表示不等待")) -> Any:
        """闹钟领取一个待处理任务（长轮询）。无任务时返回 204。"""
        server._watching += 1
        try:
            task = await server.next_task(wait)
        finally:
            server._watching -= 1
        if task is None:
            return Response(status_code=204)
        return {"ok": True, "id": task.id, "text": task.text, "created_at": task.created_at}

    @app.post("/reply", dependencies=guard)
    async def post_reply(payload: ReplyIn) -> dict[str, Any]:
        """Agent2 侧交回结果，挂起的 /task 请求会被唤醒。"""
        existing = server._tasks.get(payload.id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"未知的 id：{payload.id}")
        was_timeout = existing.status == "timeout"
        try:
            task, fresh = server.reply(payload.id, payload.text)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"未知的 id：{payload.id}") from None
        if not fresh:
            raise HTTPException(status_code=409, detail=f"#{payload.id} 已经回复过了")
        return {"ok": True, "id": task.id, "status": task.status, "late": was_timeout}

    @app.get("/result/{idx}", dependencies=guard)
    async def get_result(idx: int) -> dict[str, Any]:
        task = server._tasks.get(idx)
        if task is None:
            raise HTTPException(status_code=404, detail=f"未知的 id：{idx}")
        return {"ok": True, "id": task.id, "status": task.status, "reply": task.reply}

    @app.post("/shutdown", dependencies=guard)
    async def shutdown() -> dict[str, Any]:
        """停掉中继。用独立线程延迟硬退出，保证响应能先发出去。"""

        def _die() -> None:
            time.sleep(0.3)
            os._exit(0)

        threading.Thread(target=_die, daemon=True).start()
        return {"ok": True, "bye": True}

    return app
