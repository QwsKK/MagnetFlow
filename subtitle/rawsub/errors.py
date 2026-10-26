# -*- coding: utf-8 -*-
"""全局异常类型。分层定义，便于上层按类型决定「重试 / 跳过 / 换策略」。"""


class RawSubError(Exception):
    """所有本项目异常的基类。"""


# ---------------------------------------------------------------- 配置


class ConfigError(RawSubError):
    """配置文件缺失、字段非法或必填项为空。"""


# ---------------------------------------------------------------- 外部工具


class ToolMissing(RawSubError):
    """外部可执行程序缺失（ffmpeg / ffprobe / llama-server）。"""


class ToolFailure(RawSubError):
    """外部可执行程序执行失败（非零退出码 / 输出不可解析）。"""

    def __init__(self, message: str, returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


# ---------------------------------------------------------------- WebDAV


class WebDAVError(RawSubError):
    """WebDAV 通信层异常（连接、认证、解析）。"""

    def __init__(self, message: str, status_code: int | None = None, url: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class WebDAVAuthError(WebDAVError):
    """401/403：账号密码或权限不对。"""


# ---------------------------------------------------------------- 媒体


class AudioExtractError(RawSubError):
    """音轨抽取失败（流式与整片下载两种策略都试过了）。"""


# ---------------------------------------------------------------- ASR


class TranscribeError(RawSubError):
    """Whisper 转写失败。"""


# ---------------------------------------------------------------- LLM


class LLMServerError(RawSubError):
    """llama.cpp 服务启动失败或健康检查超时。"""


class TranslationError(RawSubError):
    """翻译失败（重试耗尽仍无法拿到行数一致的结果）。"""


# ---------------------------------------------------------------- 流水线


class PipelineAbort(RawSubError):
    """连续失败过多，主动中止本轮运行。"""
