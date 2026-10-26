# -*- coding: utf-8 -*-
"""MagnetFlow 图形界面入口。

用法：
    python app.py           # 在项目根目录
    双击 启动界面.bat        # 用项目 .venv 无窗口启动
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mflow.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
