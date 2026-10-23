"""支持 ``python -m recruiting_capture ...``（需已把 src 加入 PYTHONPATH 或安装）。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
