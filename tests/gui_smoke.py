# -*- coding: utf-8 -*-
"""GUI 无交互冒烟：实例化主窗口，构建全部页签，刷新数据，然后销毁。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mflow.app import MagnetFlowApp

app = MagnetFlowApp()
for _ in range(5):          # 让 after 轮询和渲染至少转几圈
    app.update()
print("TABS:", list(app.tabs))
print("CONSOLES:", len(app.consoles))
for name, tab in app.tabs.items():
    console = getattr(tab, "console", None)
    if console is not None:
        console.append_ui(f"smoke line for {name}")
app.tabs["磁力库"].refresh()
app.tabs["推送 115"].refresh_pending()
app.tabs["总览"].refresh()
app.update()
app.destroy()
print("GUI_SMOKE_OK")
