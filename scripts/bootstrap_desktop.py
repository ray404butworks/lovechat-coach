"""Double-click installer: visible progress, hidden setup processes, local venv."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


def main():
    project = Path(__file__).resolve().parent.parent
    root = tk.Tk()
    root.title("恋聊教练 · 安装准备")
    root.geometry("620x230")
    body = ttk.Frame(root, padding=24)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="正在准备恋聊教练", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
    notice = tk.StringVar(value="首次安装将下载项目依赖。完成后自动进入图形配置引导。")
    ttk.Label(body, textvariable=notice, wraplength=560).pack(anchor="w", pady=18)
    progress = ttk.Progressbar(body, mode="indeterminate")
    progress.pack(fill="x")
    progress.start()
    events = queue.Queue()
    work = project / "work"
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", TEMP=str(work / "tmp"),
               TMP=str(work / "tmp"), PIP_CACHE_DIR=str(work / "pip-cache"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    venv_python = project / ".venv/Scripts/python.exe"
    venv_pythonw = project / ".venv/Scripts/pythonw.exe"

    def command(args):
        # No chat, tokens, keys, or subprocess output are written to install logs.
        result = subprocess.run(args, cwd=project, env=env, capture_output=True, creationflags=flags)
        if result.returncode:
            raise RuntimeError("依赖安装未完成。请检查网络、Python 版本和目录写入权限，再双击重试。")

    def install():
        try:
            if sys.version_info < (3, 11):
                raise RuntimeError("需要 Python 3.11 或更新版本，并包含 Tk 图形组件。")
            if not venv_python.exists():
                events.put("正在建立项目专属运行环境…")
                command([sys.executable, "-m", "venv", str(project / ".venv")])
            ready = subprocess.run([str(venv_python), "-c",
                "import opendate.wechat_gui, PIL, psutil, win32gui"], cwd=project,
                env=env, capture_output=True, creationflags=flags).returncode == 0
            if not ready:
                events.put("正在下载并安装图形窗口与模型接入组件，请稍候…")
                command([str(venv_python), "-m", "pip", "install", "-e", ".[desktop]"])
            subprocess.Popen([str(venv_pythonw), "-m", "opendate.wechat_gui", str(project)],
                             cwd=project, env=env, creationflags=flags)
            events.put(None)
        except (OSError, RuntimeError) as exc:
            events.put(exc)

    def poll():
        try:
            while True:
                event = events.get_nowait()
                if event is None:
                    root.destroy()
                    return
                if isinstance(event, Exception):
                    progress.stop()
                    messagebox.showerror("安装未完成", str(event), parent=root)
                    root.destroy()
                    return
                notice.set(event)
        except queue.Empty:
            pass
        root.after(150, poll)

    # Closing the progress dialog does not terminate pip halfway through a write.
    root.protocol("WM_DELETE_WINDOW", lambda: messagebox.showinfo(
        "正在准备", "请等待当前安装步骤结束；不会读取微信或调用模型。", parent=root))
    threading.Thread(target=install, daemon=True).start()
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
