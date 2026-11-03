"""对话落盘：data/i{id}.md（输入/任务）与 data/o{id}.md（输出/结果）。"""

from __future__ import annotations

import re
import threading
from pathlib import Path

_INDEX_RE = re.compile(r"^([io])(\d+)\.md$", re.IGNORECASE)


class TranscriptStore:
    """把每一轮交流写成两个纯文本文件。

    约定（两端看到的内容完全一致）：

        data/i1.md  第 1 轮的任务输入（Agent1 发出的任务文本）
        data/o1.md  第 1 轮的结果输出（Agent2 返回的结果文本）

    id 从 1 开始递增。重启后会扫描文件名里已有的最大 id 继续往后编号，
    只读文件名、不读内容 —— 本工具绝不通过 data 目录传递信息。
    """

    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._next_id = self._scan_last_id() + 1

    # ------------------------------------------------------------------ id
    def _scan_last_id(self) -> int:
        last = 0
        for entry in self.root.iterdir():
            if not entry.is_file():
                continue
            match = _INDEX_RE.match(entry.name)
            if match:
                last = max(last, int(match.group(2)))
        return last

    def next_id(self) -> int:
        """分配下一个对话 id（进程内自增，重启后接着历史最大值）。"""
        with self._lock:
            value = self._next_id
            self._next_id += 1
            return value

    def reserve(self, idx: int) -> None:
        """客户端使用服务端分配的 id 时，同步本地计数器，避免后续重复。"""
        with self._lock:
            if idx >= self._next_id:
                self._next_id = idx + 1

    def last_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    # --------------------------------------------------------------- write
    def path_for(self, kind: str, idx: int) -> Path:
        kind = kind.lower()
        if kind not in ("i", "o"):
            raise ValueError(f"kind 只能是 'i' 或 'o'，收到 {kind!r}")
        if idx < 1:
            raise ValueError(f"id 必须从 1 开始，收到 {idx}")
        return self.root / f"{kind}{idx}.md"

    def _write(self, kind: str, idx: int, text: str) -> Path:
        path = self.path_for(kind, idx)
        body = text.rstrip("\n") + "\n"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
        return path

    def write_input(self, idx: int, text: str) -> Path:
        return self._write("i", idx, text)

    def write_output(self, idx: int, text: str) -> Path:
        return self._write("o", idx, text)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TranscriptStore {self.root} next_id={self._next_id}>"
