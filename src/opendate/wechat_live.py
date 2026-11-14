"""Read-only connection to the selected visible Windows WeChat conversation."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .connectors.wechat import WeChatError
from .connectors.wechat_ocr import capture_wechat_window, recognize_local


def open_wechat(contact: str, *, window_handle=None) -> dict:
    """Restore a running main window; never search contacts, log in, or send."""
    import psutil
    import win32con
    import win32gui
    import win32process

    def windows():
        found = []
        def visit(hwnd, _):
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if psutil.Process(pid).name().lower() not in {"weixin.exe", "wechat.exe"}:
                    return
                title = win32gui.GetWindowText(hwnd).strip()
                if title in {"微信", "Weixin", "WeChat", contact} and win32gui.IsWindowVisible(hwnd):
                    found.append({"handle": hwnd, "title": title})
            except (psutil.Error, OSError):
                pass
        win32gui.EnumWindows(visit, None)
        return found

    found = windows()
    if window_handle is not None and window_handle not in {w["handle"] for w in found}:
        raise WeChatError("所选微信窗口已关闭或不属于当前对象，请重新选择窗口。")
    if not found:
        # A running executable or registered application path, never a guessed download.
        executable = next((p.info["exe"] for p in psutil.process_iter(["name", "exe"])
                           if (p.info["name"] or "").lower() in {"weixin.exe", "wechat.exe"}
                           and p.info["exe"]), None)
        if not executable:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for name in ("Weixin.exe", "WeChat.exe"):
                    try:
                        with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}") as key:
                            executable = winreg.QueryValue(key, None).strip('"')
                    except OSError:
                        continue
                    if executable and Path(executable).is_file():
                        break
                if executable and Path(executable).is_file():
                    break
        if not executable or not Path(executable).is_file():
            raise WeChatError("没有找到微信窗口。请先打开并登录电脑微信，再点“重新连接”。")
        subprocess.Popen([executable], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(20):
            time.sleep(0.25)
            found = windows()
            if found:
                break
    main = [w for w in found if w["title"] in {"微信", "Weixin", "WeChat"}]
    target = [w for w in found if w["title"] == contact]
    choices = ([w for w in found if w["handle"] == window_handle] if window_handle is not None
               else target if len(target) == 1 else main)
    if len(choices) != 1:
        raise WeChatError("微信窗口不唯一或尚未登录。请保留一个明确的窗口，再点“重新连接”。")
    selected = choices[0]
    win32gui.ShowWindow(selected["handle"], win32con.SW_RESTORE)
    # Give a restored Qt window time to paint, without taking keyboard focus.
    time.sleep(0.4)
    selected["layout"] = "detached" if selected["title"] == contact else "main"
    return selected


def bubble_regions(image, scale=1.0):
    """Conservative light-theme bubble geometry; unsupported themes stop locally.

    White left-hand bubbles are incoming candidates, green right-hand bubbles
    are outgoing. OCR candidates always require human review before generation.
    """
    width, height = image.size
    pixels = image.convert("RGB").load()
    step = max(1, round(2*scale))
    min_width = max(24, round(28*scale))
    regions = []
    active = {}
    for y in range(0, height, step):
        spans = []
        start, kind = None, None
        for x in range(0, width+step, step):
            current = None
            if x < width:
                r, g, b = pixels[x, y]
                if min(r, g, b) >= 248 and max(r, g, b)-min(r, g, b) <= 10:
                    current = "peer"
                elif g-r >= 25 and g-b >= 25 and g >= 150:
                    current = "me"
            if current != kind:
                if kind and start is not None and x-start >= min_width:
                    if (kind == "peer" and 10*scale <= start <= width*0.32 and x < width-40*scale
                            or kind == "me" and x >= width*0.65):
                        spans.append((kind, start, min(x, width)))
                start, kind = (x if current else None), current
        next_active = {}
        for kind, left, right in spans:
            match = next((key for key, box in active.items()
                          if key not in next_active and box[0] == kind
                          and abs(box[1]-left) <= 8*scale and abs(box[3]-right) <= 8*scale), None)
            if match is None:
                key = len(regions)
                regions.append([kind, left, y, right, y+step])
            else:
                key = match
                regions[key][4] = y+step
            next_active[key] = regions[key]
        # Text can interrupt a scanline; hold matching edges across short gaps.
        for key, box in active.items():
            if key not in next_active and y-box[4] < 22*scale:
                next_active[key] = box
        active = next_active
    return [box for box in regions if box[4]-box[2] >= 22*scale]


def extract_incoming(image, lines, scale=1.0) -> str:
    regions = bubble_regions(image, scale)
    outgoing_bottom = max((b[4] for b in regions if b[0] == "me"), default=-1)
    messages = []
    for kind, left, top, right, bottom in sorted(regions, key=lambda box: box[2]):
        if kind != "peer" or bottom <= outgoing_bottom:
            continue
        text = []
        for line in lines:
            words = line.get("words", [])
            selected = [w for w in words if left <= w["x"]+w["width"]/2 <= right
                        and top <= w["y"]+w["height"]/2 <= bottom]
            if selected:
                if len(selected) != len(words):
                    raise WeChatError("消息区域有交叠，无法确定来信内容。请点“手动选消息”。")
                value = line["text"].strip()
                import re
                text.append(re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value))
        if text:
            messages.append("\n".join(text))
    if not regions:
        raise WeChatError("当前画面无法可靠区分双方消息，请用微信浅色主题，或点“手动选消息”。")
    value = "\n\n".join(messages)
    if len(value) > 12000:
        raise WeChatError("画面文字过多，请手动选择这次要回复的消息。")
    return value


def read_incoming(contact: str, work_dir: Path, *, window_handle=None, layout="main") -> str:
    """Verify the selected title before recognizing only its visible message area."""
    import re
    if not contact.strip() or layout not in {"main", "detached"}:
        raise WeChatError("请先确认聊天对象和微信窗口。")
    image, scale = capture_wechat_window(window_handle)
    width, height = image.size
    header_x = round((230 if layout == "main" else 20)*scale)
    header_y, header_bottom = round(20*scale), round(90*scale)
    if width <= header_x+100*scale or height <= 300*scale:
        raise WeChatError("微信窗口太小，请放大微信后重新连接。")
    header = recognize_local(image.crop((header_x, header_y, width-round(30*scale), header_bottom)), work_dir)
    matches = [line for line in header if re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "",
                                                line["text"].strip()) == contact]
    if len(matches) != 1 or not matches[0].get("words"):
        raise WeChatError("微信当前私聊与所选对象不一致。请打开该对象私聊，再点“重新连接”。")
    left = header_x+min(w["x"] for w in matches[0]["words"]) if layout == "main" else round(20*scale)
    bottom = height-round(160*scale)
    if left >= width-200*scale or bottom <= header_bottom:
        raise WeChatError("无法确认聊天区域，请放大微信窗口后重新连接。")
    body = image.crop((int(left), header_bottom, width-round(15*scale), bottom))
    lines = recognize_local(body, work_dir)
    return extract_incoming(body, lines, scale)
