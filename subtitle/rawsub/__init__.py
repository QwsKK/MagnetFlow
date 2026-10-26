# -*- coding: utf-8 -*-
"""
rawsub —— 115 网盘视频自动中文字幕流水线。

模块分层（从下往上）：
    config        配置加载与校验
    webdav        WebDAV 客户端 + 片库递归扫描
    media         ffmpeg 音轨抽取（流式优先，整片下载兜底）
    asr           faster-whisper 转写 + SRT 数据结构
    llm           llama.cpp server 进程托管
    translate     OpenAI 兼容客户端 + 分批翻译 + 行数校验
    state         SQLite 处理状态（增量去重、失败重试）
    pipeline      分阶段编排
    cli           命令行入口
"""

__version__ = "1.0.0"
