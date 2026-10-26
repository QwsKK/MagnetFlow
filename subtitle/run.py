#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
115Translate 统一入口。

用法：
    python run.py doctor          环境自检
    python run.py scan            扫描列出待处理影片
    python run.py run             跑完整流程（转写 -> 翻译 -> 回写）
    python run.py serve-llm       单独拉起 llama.cpp 服务（调试用）
    python run.py test-llm        用一句话测试翻译链路
"""
import sys
from pathlib import Path

# 保证无论从哪里调用，都能 import 到同目录下的 rawsub 包
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rawsub.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
