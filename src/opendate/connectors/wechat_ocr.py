"""Local OCR connectivity check for WeChat builds without accessible controls.

Inspection only reports statistics. The draft workflow passes only locally
selected and approved incoming text to the reply loop. It can never send.
"""

from __future__ import annotations

import ctypes
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .wechat import WeChatError


def recognize_local(image: Any, work_dir: Path) -> list[dict[str, Any]]:
    helper = Path(__file__).resolve().parents[3] / "scripts" / "windows-ocr.ps1"
    if not helper.is_file():
        raise WeChatError("本地 OCR 辅助脚本不存在")
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=work_dir, suffix=".png", delete=False) as handle:
        image_path = Path(handle.name)
    try:
        image.save(image_path)
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", str(helper), "-ImagePath", str(image_path)],
            capture_output=True,
            encoding="utf-8",
            timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            # Do not print stdout/stderr: OCR results may contain private text.
            raise WeChatError("Windows 本地 OCR 执行失败；未上传或发送任何内容")
        return json.loads(completed.stdout).get("lines", [])
    finally:
        image_path.unlink(missing_ok=True)


def list_wechat_windows() -> list[dict[str, Any]]:
    """Local window picker metadata only; never reads message text."""
    import psutil
    import win32gui
    import win32process

    windows = []
    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        class_name = win32gui.GetClassName(hwnd)
        if not class_name.startswith("Qt"):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() not in {"weixin.exe", "wechat.exe"}:
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left < 400 or bottom - top < 300:
                return
            windows.append({"handle": hwnd, "title": title})
        except (psutil.Error, OSError):
            return
    win32gui.EnumWindows(visit, None)
    return windows


def capture_wechat_window(window_handle: int | None = None) -> tuple[Any, float]:
    import win32gui
    import win32ui
    from PIL import Image

    ctypes.windll.user32.SetProcessDPIAware()
    windows = list_wechat_windows()
    if window_handle is None:
        main = [window for window in windows if window["title"] in {"微信", "Weixin", "WeChat"}]
        if len(main) != 1:
            raise WeChatError("请在图形窗口选择要读取的微信窗口；需要一个明确、可见的微信主窗口。")
        hwnd = main[0]["handle"]
    else:
        if window_handle not in {window["handle"] for window in windows}:
            raise WeChatError("所选微信窗口已关闭或发生变化，请刷新窗口列表后重新选择。")
        hwnd = window_handle
    if not hwnd or not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        raise WeChatError("请先显示微信主窗口；本地识别不会自动恢复、激活或登录微信")
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width < 500 or height < 400:
        raise WeChatError("微信窗口尺寸不足，无法安全划定指定聊天区域")
    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    target_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    target_dc.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(hwnd, target_dc.GetSafeHdc(), 2):
            raise WeChatError("微信窗口截图失败，不会改为抓取整个桌面")
        image = Image.frombuffer(
            "RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1
        ).copy()
        if max(high - low for low, high in image.getextrema()) < 15:
            raise WeChatError("微信截图为空白，无法执行可靠的本地识别")
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) or 96
        return image, dpi / 96
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)


def read_local_ocr_lines(contact: str, work_dir: Path, *, window_handle: int | None = None,
                         layout: str = "main") -> list[str]:
    """Return scoped text to a *local* review UI; callers must not auto-upload."""
    if layout not in {"main", "detached"}:
        raise WeChatError("请选择微信主窗口或独立聊天窗口。")
    image, scale = (capture_wechat_window() if window_handle is None
                    else capture_wechat_window(window_handle))
    width, height = image.size
    # Only OCR the header first. Never OCR the contact list or whole window.
    header_x, header_y = round((230 if layout == "main" else 20) * scale), round(20 * scale)
    header_bottom = min(round(90 * scale), height)
    header = image.crop((header_x, header_y, width - round(30 * scale), header_bottom))
    lines = recognize_local(header, work_dir)
    matches = [line for line in lines if line["text"].strip() == contact or
               re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "",
                      line["text"].strip()) == contact]
    if len(matches) != 1 or not matches[0].get("words"):
        raise WeChatError("本地识别无法唯一核对指定好友标题；未识别聊天正文，请确认打开正确私聊")
    chat_left = (header_x + min(word["x"] for word in matches[0]["words"])
                 if layout == "main" else round(20 * scale))
    body_top = max(
        header_bottom,
        header_y
        + max(word["y"] + word["height"] for word in matches[0]["words"])
        + round(25 * scale),
    )
    body_bottom = height - round(160 * scale)
    if chat_left >= width - 200 * scale or body_bottom <= body_top:
        raise WeChatError("无法安全划定指定好友的聊天区域")
    body = image.crop((int(chat_left), int(body_top), width - round(15 * scale), int(body_bottom)))
    recognized = recognize_local(body, work_dir)
    return [line["text"] for line in recognized if line["text"].strip()]


def inspect_local_ocr(contact: str, work_dir: Path) -> dict[str, Any]:
    recognized = read_local_ocr_lines(contact, work_dir)
    return {
        "backend": "windows_local_ocr",
        "contact": contact,
        "target_title_verified": True,
        "visible_text_line_count": len(recognized),
        "visible_character_count": sum(len("".join(line.split())) for line in recognized),
        "parsed_messages": False,
        "model_called": False,
        "messages_sent": 0,
        "screenshots_saved": False,
        "chat_saved": False,
    }


def select_ocr_message(lines: list[str], selection: str) -> str:
    """A person identifies the incoming message; never guess roles from pixels."""
    try:
        indices = [int(part) for part in re.split(r"[,，\s]+", selection.strip()) if part]
    except ValueError:
        raise WeChatError("请填写消息行号，例如 2 或 2,3") from None
    if not indices or len(set(indices)) != len(indices) or indices != sorted(indices):
        raise WeChatError("行号需按顺序填写，不能重复；直接回车可取消")
    if any(index < 1 or index > len(lines) for index in indices):
        raise WeChatError("行号超出当前识别结果范围")
    selected = "\n".join(lines[index - 1] for index in indices)
    selected = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", selected)
    if not selected.strip() or len(selected) > 2000:
        raise WeChatError("请选择 1–2000 字的最后一条好友来信")
    return selected


class ReviewedOCRDriver:
    """Feed only the user-reviewed incoming line(s) into the suggestion pipeline."""

    def __init__(self, contact: str, my_name: str, incoming: str) -> None:
        self.contact, self.my_name, self.incoming = contact, my_name, incoming

    def read(self, contact: str, my_name: str, limit: int) -> list[dict[str, str]]:
        if (contact, my_name) != (self.contact, self.my_name):
            raise WeChatError("OCR 复核文本仅属于本次指定好友")
        return [{"sender": contact, "text": self.incoming, "type": "text"}]

    def send(self, contact: str, my_name: str, text: str) -> None:
        raise WeChatError("OCR 模式仅提供建议，请本人手动复制发送")
