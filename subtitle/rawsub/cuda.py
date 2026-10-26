# -*- coding: utf-8 -*-
"""
Windows 上 CTranslate2 的 CUDA DLL 装载修复。

faster-whisper 底层是 CTranslate2。pip 装的 `nvidia-cublas-cu12` /
`nvidia-cudnn-cu12` 会把 DLL 放在 site-packages 里，而不是系统目录，
导致首次 GPU 运行时报 "Could not load library cudnn_ops64_9.dll" 之类的错。

解决方式是在 import ctranslate2 之前，把这些目录加进 DLL 搜索路径。
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("rawsub.cuda")

# (pip 包名, 包内 DLL 相对路径)
_NVIDIA_PACKAGES = (
    ("nvidia.cudnn", "bin"),
    ("nvidia.cublas", "bin"),
    ("nvidia.cuda_runtime", "bin"),
    ("nvidia.cuda_nvrtc", "bin"),
)


def _package_dir(package: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def ensure_cuda_dlls() -> list[Path]:
    """把 pip 安装的 NVIDIA 运行库目录加入 DLL 搜索路径与 PATH。

    返回实际生效的目录列表；非 Windows 或没装这些包时返回空列表。
    """
    if sys.platform != "win32":
        return []

    added: list[Path] = []
    for package, sub in _NVIDIA_PACKAGES:
        base = _package_dir(package)
        if base is None:
            continue
        dll_dir = base / sub
        if not dll_dir.is_dir():
            continue
        path_str = str(dll_dir)
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(path_str)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("add_dll_directory 失败 %s: %s", dll_dir, exc)
        if path_str.lower() not in os.environ.get("PATH", "").lower():
            os.environ["PATH"] = path_str + os.pathsep + os.environ.get("PATH", "")
        added.append(dll_dir)

    if added:
        log.debug("已注入 NVIDIA DLL 目录：%s", ", ".join(str(p) for p in added))
    return added


def cuda_available() -> bool:
    """探测 CTranslate2 能否拿到 CUDA 设备。"""
    try:
        ensure_cuda_dlls()
        import ctranslate2  # noqa: PLC0415

        return ctranslate2.get_cuda_device_count() > 0
    except Exception as exc:  # noqa: BLE001
        log.debug("CUDA 探测失败：%s", exc)
        return False


def describe_gpu() -> str:
    """返回一行显卡描述，探测不到时返回提示文本。"""
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        pass
    return "未检测到 NVIDIA 显卡（或 nvidia-smi 不可用）"
