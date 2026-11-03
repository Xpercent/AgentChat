"""HTTP 客户端：只用标准库 urllib，不引入额外依赖。

Agent1 用 send_task 发任务并等待结果；闹钟用 next_task 领任务；Agent2 用 submit_reply 交结果。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://127.0.0.1:18765"

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
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 600.0,
    token: str | None = None,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["X-Agent-Token"] = token

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


def _base(url: str) -> str:
    return url.rstrip("/")


def health(url: str = DEFAULT_URL, *, token: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
    status, body = request("GET", f"{_base(url)}/health", timeout=timeout, token=token)
    if status != 200:
        raise AgentChatError(f"健康检查失败：HTTP {status} {body}", status=status, payload=body)
    return body


def send_task(
    text: str,
    url: str = DEFAULT_URL,
    *,
    token: str | None = None,
    timeout: float = 1200.0,
    wait: bool = True,
) -> dict[str, Any]:
    """Agent1：POST 任务文本，默认阻塞等待 Agent2 的结果（最多 timeout 秒）。"""
    status, body = request(
        "POST",
        f"{_base(url)}/task",
        payload={"text": text, "wait": wait, "timeout": timeout},
        timeout=timeout + 30,
        token=token,
    )
    if status == 200 and isinstance(body, dict):
        return body
    if status == 504 and isinstance(body, dict):
        raise AgentChatError(body.get("detail", "等待结果超时"), status=status, payload=body)
    if status == 401:
        raise AgentChatError("token 校验失败（--token 与服务端不一致）", status=status, payload=body)
    detail = body.get("detail") if isinstance(body, dict) else body
    raise AgentChatError(f"提交任务失败：HTTP {status} {detail}", status=status, payload=body)


def next_task(url: str = DEFAULT_URL, *, token: str | None = None, wait: float = 60.0) -> dict[str, Any] | None:
    """Agent2：领取一个待处理任务；超时无任务返回 None。"""
    status, body = request(
        "GET", f"{_base(url)}/next?wait={wait:g}", timeout=wait + 30, token=token
    )
    if status == 204:
        return None
    if status == 200 and isinstance(body, dict):
        return body
    raise AgentChatError(f"领取任务失败：HTTP {status} {body}", status=status, payload=body)


def submit_reply(idx: int, text: str, url: str = DEFAULT_URL, *, token: str | None = None) -> dict[str, Any]:
    """Agent2：交回结果文本。"""
    status, body = request(
        "POST", f"{_base(url)}/reply", payload={"id": idx, "text": text}, timeout=60, token=token
    )
    if status == 200 and isinstance(body, dict):
        return body
    if status == 409:
        raise AgentChatError(f"#{idx} 已经回复过了", status=status, payload=body)
    if status == 404:
        raise AgentChatError(f"未知的 id：{idx}", status=status, payload=body)
    raise AgentChatError(f"提交结果失败：HTTP {status} {body}", status=status, payload=body)


def get_result(idx: int, url: str = DEFAULT_URL, *, token: str | None = None) -> dict[str, Any]:
    status, body = request("GET", f"{_base(url)}/result/{idx}", timeout=30, token=token)
    if status == 200 and isinstance(body, dict):
        return body
    raise AgentChatError(f"查询结果失败：HTTP {status} {body}", status=status, payload=body)
