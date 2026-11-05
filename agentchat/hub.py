"""中继（hub）的探测与停止。

中继由 Agent2 作为**独立的后台任务**启动（python run.py serve），
不由闹钟派生 —— 闹钟结束时会回收自己的整棵进程树，派生出来的中继活不过它。

    Windows 实测：DETACHED_PROCESS / CREATE_BREAKAWAY_FROM_JOB / ShellExecute
    起的进程都逃不掉，所以这里干脆不派生，只做"在不在"的探测。
"""

from __future__ import annotations

from . import client
from .client import AgentChatError


def is_alive() -> bool:
    """默认端口上有没有中继。"""
    try:
        status, _ = client.request("GET", "/health", timeout=2)
    except AgentChatError:
        return False
    return status == 200


def stop() -> bool:
    if not is_alive():
        return False
    try:
        client.request("POST", "/shutdown", payload={}, timeout=5)
    except AgentChatError:
        return False
    return True
