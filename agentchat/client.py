"""HTTP 客户端：只用标准库 urllib，不引入额外依赖。

Agent1 用 send_task 发任务并等待结果；闹钟用 next_task 领任务；Agent2 用 submit_reply 交结果。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

# 本工具只在本机通信：显式绕过系统代理，避免 localhost 请求被代理拦截或拖慢长轮询。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class AgentChatError(RuntimeError):
    """网络层错误（连不上、超时）。"""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 600.0,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    url = f"{DEFAULT_URL}{path}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, _decode(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())
    except (socket.timeout, TimeoutError) as exc:
        raise AgentChatError(f"请求 {url} 超时（{timeout:g}s）", status=None) from exc
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc)).rstrip("。.")
        raise AgentChatError(
            f"连不上 {url}：{reason}。请确认 Agent2 已经上了闹钟（python run.py watch）。"
        ) from exc


def send_task(
    text: str,
    *,
    wait: bool = True,
) -> dict[str, Any]:
    """Agent1：POST 任务文本，默认阻塞等待 Agent2 的结果（最多 20 分钟）。"""
    status, body = request(
        "POST",
        "/task",
        payload={"text": text, "wait": wait},
        timeout=1230.0,
    )
    if status == 200 and isinstance(body, dict):
        return body
    if status == 504 and isinstance(body, dict):
        raise AgentChatError(body.get("detail", "等待结果超时"), status=status, payload=body)
    detail = body.get("detail") if isinstance(body, dict) else body
    raise AgentChatError(f"提交任务失败：HTTP {status} {detail}", status=status, payload=body)


def next_task(*, wait: float = 60.0) -> dict[str, Any] | None:
    """Agent2：领取一个待处理任务；超时无任务返回 None。"""
    status, body = request(
        "GET", f"/next?wait={wait:g}", timeout=wait + 30
    )
    if status == 204:
        return None
    if status == 200 and isinstance(body, dict):
        return body
    raise AgentChatError(f"领取任务失败：HTTP {status} {body}", status=status, payload=body)


def submit_reply(idx: int, text: str) -> dict[str, Any]:
    """Agent2：交回结果文本。"""
    status, body = request(
        "POST", "/reply", payload={"id": idx, "text": text}, timeout=60
    )
    if status == 200 and isinstance(body, dict):
        return body
    if status == 409:
        raise AgentChatError(f"#{idx} 已经回复过了", status=status, payload=body)
    if status == 404:
        raise AgentChatError(f"未知的 id：{idx}", status=status, payload=body)
    raise AgentChatError(f"提交结果失败：HTTP {status} {body}", status=status, payload=body)


def get_result(idx: int) -> dict[str, Any]:
    status, body = request("GET", f"/result/{idx}", timeout=30)
    if status == 200 and isinstance(body, dict):
        return body
    raise AgentChatError(f"查询结果失败：HTTP {status} {body}", status=status, payload=body)
