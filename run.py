# -*- coding: utf-8 -*-
"""MagnetFlow 命令行入口（图形界面之外的等价操作）。

用法（项目根目录，用 .venv 的 python）：
    python run.py web [--port 8420] [--host 127.0.0.1]
                            打开 Web 可视化控制台（推荐主界面），浏览器自动启动
    python run.py gui                      打开桌面窗口界面（备选）
    python run.py chain [参数...]          一键全流程（采集→推送→字幕），--help 看参数
    python run.py harvest --site example --pages 1 3 [--keyword K] [--limit N] [--dry-run]
    python run.py push [--test] [--limit N] [--site s] [--dry-run]
    python run.py subtitle run|scan|status|doctor|reset ...   （原 115Translate CLI 全透传）
    python run.py doctor                   三合一环境体检（采集器+字幕+推送）
    python run.py setup                    显示依赖安装指引
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mflow import paths  # noqa: E402


def _run(argv: list[str], cwd: Path) -> int:
    print(f"$ {' '.join(argv)}   (cwd={cwd})", flush=True)
    return subprocess.call([str(a) for a in argv], cwd=str(cwd))


def cmd_gui(_args: list[str]) -> int:
    return _run([paths.python_exe(), str(paths.ROOT / "app.py")], paths.ROOT)


def cmd_web(args: list[str]) -> int:
    """启动本地 Web 控制台（FastAPI + 浏览器界面）。"""
    extra = args or ["--port", "8420"]
    return _run([paths.python_exe(), "-m", "mflow.web.server", *extra],
                paths.ROOT)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "gui":
        return cmd_gui(rest)
    if cmd == "web":
        return cmd_web(rest)
    if cmd == "chain":
        return _run([paths.python_exe(), "-m", "mflow.chain", *rest], paths.ROOT)
    if cmd == "push":
        return _run([paths.python_exe(), "-m", "mflow.push115", *rest], paths.ROOT)
    if cmd == "harvest":
        return _run([paths.python_exe(), str(paths.HARVEST_SCRIPT), *rest],
                    paths.HARVESTER_DIR)
    if cmd == "subtitle":
        return _run([paths.python_exe(), str(paths.SUBTITLE_RUN), *rest],
                    paths.SUBTITLE_DIR)
    if cmd == "doctor":
        return _doctor()
    if cmd == "setup":
        print("依赖安装（在项目根目录执行）：\n"
              "  py -3.12 -m venv .venv\n"
              "  .venv\\Scripts\\python -m pip install -r requirements.txt "
              "-i https://pypi.tuna.tsinghua.edu.cn/simple\n"
              "之后双击 启动界面.bat 或 python run.py gui")
        return 0
    print(f"未知命令：{cmd}\n{__doc__}")
    return 2


def _doctor() -> int:
    """三个环节各自体检，汇总报告。"""
    py = paths.python_exe()
    results = []

    code = _run([py, str(paths.HARVEST_SCRIPT), "--list-sites"], paths.HARVESTER_DIR)
    results.append(("采集器", "正常" if code == 0 else f"异常（退出码 {code}）"))

    print()
    code = _run([py, str(paths.SUBTITLE_RUN), "doctor"], paths.SUBTITLE_DIR)
    results.append(("字幕流水线", "正常" if code == 0 else f"体检未过（退出码 {code}）"))

    print()
    code = _run([py, "-m", "mflow.push115", "--test"], paths.ROOT)
    if code == 0:
        results.append(("推送 115", "正常"))
    else:
        results.append(("推送 115", "未就绪（cookie 未配或接口失败，见上方提示）"))

    print("\n========== 体检汇总 ==========")
    bad = 0
    for name, status in results:
        mark = "✓" if "正常" in status else "!"
        if mark == "!":
            bad += 1
        print(f"  {mark} {name}：{status}")
    print("提示：推送未就绪不影响采集与字幕单独使用。")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
