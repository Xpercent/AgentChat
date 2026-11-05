"""命令行入口：python -m agentchat / python run.py <子命令>

角色分工：

    Agent2  python run.py serve              中继（后台任务①，常驻）
    Agent2  python run.py watch              闹钟（后台任务②，收到任务或满 9 分钟后退出）
    Agent2  python run.py reply --id 1 ...   交结果
    Agent1  python run.py send --text "..."  发任务（最多等 20 分钟）
    任意    python run.py stop              停中继
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__, client
from .client import AgentChatError
from .store import TranscriptStore

EXIT_TIMEOUT = 3
EXIT_NETWORK = 4

DEFAULT_WAIT = 1200.0  # Agent1 等结果的上限：20 分钟


# --------------------------------------------------------------------- 工具
def _setup_io() -> None:
    """把标准流定死为 UTF-8。

    本工具全链路都是 UTF-8，但 Windows 上 Python 往管道/文件写时默认用本地代码页
    （中文系统是 GBK）。调用方（codex、各类 Agent 运行时）一律按 UTF-8 读，
    于是中文结果就变成 "��ã�" 这样的乱码。这里不依赖外部环境变量，自己改掉。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 被替换成非 TextIOWrapper 的情况
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 流已关闭等
            pass


def _read_text(args: argparse.Namespace) -> str:
    src = getattr(args, "file", None)
    if src:
        if src == "-":
            return sys.stdin.read()
        path = Path(src).expanduser()
        if not path.is_file():
            raise SystemExit(f"文件不存在：{path.resolve()}（当前目录 {Path.cwd()}）")
        raw = path.read_bytes()
        try:
            return raw.decode("utf-8-sig")  # 容忍记事本加的 BOM
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")
    if getattr(args, "text", None) is not None:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("缺少文本：用 --text \"...\"、--file path.md（或 --file - 读 stdin）")


def _die(message: str, code: int = 1) -> None:
    print(f"[agentchat] {message}", file=sys.stderr)
    raise SystemExit(code)


def _log_transcript(idx: int, text: str | None, reply: str | None) -> None:
    store = TranscriptStore()
    store.reserve(idx)
    if text is not None:
        store.write_input(idx, text)
    if reply is not None:
        store.write_output(idx, reply)


# --------------------------------------------------------------------- serve
def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import AgentServer, create_app

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    server = AgentServer()
    app = create_app(server)

    line = "─" * 62
    print(line, flush=True)
    print(f" agentchat {__version__} · 中继常驻", flush=True)
    print(f" 地址      {client.DEFAULT_URL}", flush=True)
    print(f" 数据目录  {server.store.root}  (i{{id}}.md / o{{id}}.md)", flush=True)
    print(f" 闹钟      由 watch 挂载；本进程只负责收发，不会思考", flush=True)
    print(line, flush=True)

    try:
        uvicorn.run(
            app,
            host=client.DEFAULT_HOST,
            port=client.DEFAULT_PORT,
            log_config=None,
            access_log=False,
            log_level="info",
        )
    except KeyboardInterrupt:  # pragma: no cover
        print("\n[agentchat] 中继已停止", file=sys.stderr)
    return 0


# --------------------------------------------------------------------- watch
def _cmd_watch(args: argparse.Namespace) -> int:
    from . import beacon

    return beacon.run()


# ---------------------------------------------------------------------- send
def _cmd_send(args: argparse.Namespace) -> int:
    text = _read_text(args)
    try:
        result = client.send_task(text, wait=not args.detach)
    except AgentChatError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        idx = payload.get("id")
        if isinstance(idx, int):
            _log_transcript(idx, text, None)
            print(f"[agentchat] {exc}（任务已记录为 #{idx}，稍后可用 wait --id {idx} 补取）", file=sys.stderr)
        else:
            print(f"[agentchat] {exc}", file=sys.stderr)
        return EXIT_TIMEOUT if exc.status == 504 else EXIT_NETWORK

    idx = result.get("id")
    reply = result.get("reply")
    if isinstance(idx, int):
        _log_transcript(idx, text, reply if isinstance(reply, str) else None)

    if args.detach:
        print(f"[agentchat] 已提交 #{idx}，用 wait --id {idx} 取结果", file=sys.stderr)
    else:
        print(reply if reply is not None else "")
    return 0


# --------------------------------------------------------------------- reply
def _cmd_reply(args: argparse.Namespace) -> int:
    text = _read_text(args)
    try:
        client.submit_reply(args.id, text)
    except AgentChatError as exc:
        _die(str(exc))
        return 1
    print(f"[agentchat] #{args.id} 已交回（{len(text)} 字）")
    print("[agentchat] 下一步：重新上闹钟（后台任务）python run.py watch，然后结束会话")
    return 0


# ---------------------------------------------------------------------- wait
def _cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            result = client.get_result(args.id)
        except AgentChatError as exc:
            _die(str(exc), EXIT_NETWORK)
            return EXIT_NETWORK
        if result.get("status") == "answered":
            print(result.get("reply") or "")
            return 0
        if time.monotonic() >= deadline:
            print(f"[agentchat] #{args.id} 等待 {args.timeout:g}s 仍未返回", file=sys.stderr)
            return EXIT_TIMEOUT
        time.sleep(2.0)


# ---------------------------------------------------------------------- stop
def _cmd_stop(args: argparse.Namespace) -> int:
    from . import hub

    if hub.stop():
        print("[agentchat] 中继已停止", file=sys.stderr)
    else:
        print("[agentchat] 中继没在运行", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat",
        description="本地双 Agent 轻量级 HTTP 通信工具（Agent1 发任务 / Agent2 被闹钟唤醒）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Agent2 布置（各起一个后台任务，然后结束会话）：\n"
            "  python run.py serve                             # 中继，常驻\n"
            "  python run.py watch                             # 闹钟，收到任务或满 9 分钟后结束\n"
            "Agent1 发任务：\n"
            "  python run.py send --text \"帮我查一下今天的行情\"    # 最多等 20 分钟\n"
            "Agent2 被唤醒后：\n"
            "  python run.py reply --id 1 --text \"结果\"          # 短结果\n"
            "  python run.py reply --id 1 --file .tmp/结果.md  # 长结果，写进 .tmp/\n"
            "  python run.py watch                             # 重新上闹钟\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"agentchat {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Agent2：启动中继（后台任务，常驻不退出）")
    p_serve.set_defaults(func=_cmd_serve)

    p_watch = sub.add_parser("watch", help="Agent2：后台等待任务；收到任务或满 9 分钟时输出指引并退出")
    p_watch.set_defaults(func=_cmd_watch)

    p_send = sub.add_parser("send", help="Agent1：发送任务并等待结果（默认最多 20 分钟）")
    p_send.add_argument("--text", default=None, help="任务文本")
    p_send.add_argument("--file", default=None, help="从文件读取任务文本")
    p_send.add_argument("--detach", action="store_true", help="只提交不等待，稍后用 wait 取结果")
    p_send.set_defaults(func=_cmd_send)

    p_reply = sub.add_parser("reply", help="Agent2：交回结果文本")
    p_reply.add_argument("--id", type=int, required=True, help="任务 id（watch 打印的 id）")
    p_reply.add_argument("--text", default=None)
    p_reply.add_argument("--file", default=None)
    p_reply.set_defaults(func=_cmd_reply)

    p_wait = sub.add_parser("wait", help="Agent1：等待某个 id 的结果（超时后补取）")
    p_wait.add_argument("--id", type=int, required=True)
    p_wait.add_argument("--timeout", type=float, default=DEFAULT_WAIT)
    p_wait.set_defaults(func=_cmd_wait)

    p_stop = sub.add_parser("stop", help="停掉中继")
    p_stop.set_defaults(func=_cmd_stop)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_io()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n[agentchat] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
