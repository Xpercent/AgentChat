#!/usr/bin/env python
"""免安装启动器：在任何目录下都能这样调用本工具。

    python <本文件路径> serve --port 18765
    python <本文件路径> send --text "..."
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentchat.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
