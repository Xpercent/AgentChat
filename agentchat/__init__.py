"""agentchat —— 本地双 Agent 轻量级 HTTP 通信工具。

只做两件事：
1. 通过 HTTP 在两个 Agent 之间传递任务文本 / 结果文本；
2. 把每一轮交流落盘到 data/i{id}.md（输入）与 data/o{id}.md（输出）。

两个 Agent 之间不通过 data 目录通信，data 只是日志。
"""

from .store import TranscriptStore

__version__ = "0.1.0"

__all__ = ["TranscriptStore", "__version__"]
