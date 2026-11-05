"""闹钟：Agent2 会话结束前挂一个 watch，收到任务或满 9 分钟时输出指引并退出。

关键点：**进程退出本身就是信号**。后台任务结束会唤醒 Agent2 的会话，
所以闹钟不需要"通知"任何人，它只要在该响的时候死掉。

因此闹钟必须是独立的后台任务，不能自己拉起中继：它派生出去的任何进程
都会被它结束时的进程树回收一起带走（Windows 上实测如此，连
CREATE_BREAKAWAY_FROM_JOB 和 ShellExecute 都逃不掉）。中继要由 Agent2
另起一个后台任务来跑。

watch 的 stdout 必须自带"醒来该做什么"——Agent2 醒来第一眼看的就是它。
"""

from __future__ import annotations

import sys
import time

from . import client, hub
from .client import AgentChatError

EXIT_TASK = 0
EXIT_NETWORK = 4
EXIT_NO_HUB = 5

MAX_FAILURES = 3
WATCH_LIFETIME = 9 * 60  # 每个 watch 独立计时，提前于宿主的 10 分钟唤醒窗口退出


def _rule(title: str) -> str:
    bar = "=" * 62
    return f"{bar}\n {title}\n{bar}"


def announce(task: dict) -> None:
    """把任务和下一步动作打进 stdout —— 这是唤醒后唯一能看到的东西。"""
    idx = task.get("id")
    text = (task.get("text") or "").rstrip("\n")

    print(_rule(f"agentchat 唤醒：收到任务 #{idx}"), flush=True)
    print(text, flush=True)
    print("", flush=True)
    print(_rule("醒来后三件事（做完就结束会话）"), flush=True)
    print(" 1. 干活，把结果整理成文本（临时文件只写 .tmp/）", flush=True)
    print(f' 2. python run.py reply --id {idx} --text "结果"    # 短结果', flush=True)
    print(f"    python run.py reply --id {idx} --file .tmp/结果.md   # 长结果，先 mkdir -p .tmp", flush=True)
    print(" 3. python run.py watch     # 重新上闹钟（后台任务），再结束会话", flush=True)
    print("=" * 62, flush=True)


def run() -> int:
    deadline = time.monotonic() + WATCH_LIFETIME
    if not hub.is_alive():
        print(_rule("agentchat：中继没在运行，这不是任务"), flush=True)
        print("闹钟上不去。先把中继作为后台任务启动，再重新上闹钟：", flush=True)
        print("   python run.py serve      # 后台任务①：中继，常驻", flush=True)
        print("   python run.py watch      # 后台任务②：闹钟，一次性", flush=True)
        print("=" * 62, flush=True)
        return EXIT_NO_HUB

    failures = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            task = client.next_task(wait=remaining, timeout=remaining)
        except AgentChatError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            failures += 1
            print(f"[agentchat] 领取失败（第 {failures} 次）：{exc}", file=sys.stderr, flush=True)
            if failures >= MAX_FAILURES:
                return EXIT_NETWORK
            time.sleep(min(2**failures, 10, remaining))
            continue

        failures = 0
        if task is None:
            continue  # 提前结束的长轮询继续等，但不重置本进程的保活计时

        announce(task)
        return EXIT_TASK

    print(_rule("agentchat 保活唤醒：watch 已挂起 9 分钟，这不是任务"), flush=True)
    print("无需执行任务或 reply。请立即重新启动后台闹钟：", flush=True)
    print("   python run.py watch      # 后台任务，重新开始计时", flush=True)
    print("启动后结束当前会话，等待下一次唤醒；中继 serve 不需要重启。", flush=True)
    print("=" * 62, flush=True)
    return EXIT_TASK
