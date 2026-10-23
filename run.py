#!/usr/bin/env python
"""免安装运行入口：把 src/ 加入 sys.path 后转交 CLI。

用法：
    python run.py demo
    python run.py validate --plan configs/boss_1080p_example.json
    python run.py run --plan configs/boss_1080p_example.json
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:  # Windows 控制台默认编码可能不是 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

from recruiting_capture.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
