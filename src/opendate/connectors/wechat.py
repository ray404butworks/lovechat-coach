"""Personal WeChat connector. Live UI is lazy, replaceable, and never retried.

The user opens one explicitly allowlisted private chat. No contact enumeration,
search, login, hooks, polling, or background receiver is used by this driver.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..config import AppConfig, WeChatConfig
from .base import Candidate, Match, Message


class WeChatError(RuntimeError):
    """Stop this run; a person must inspect the UI before trying again."""


class WeChatDriver(Protocol):
    def read(self, contact: str, my_name: str, limit: int) -> list[dict[str, str]]: ...

    def send(self, contact: str, my_name: str, text: str) -> None: ...


def fingerprint(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def check_environment(scripts: Path, *, inspect_ui: bool = False) -> dict[str, Any]:
    """Dependency checks do not import the third-party UI automation package."""
    requirements = {
        "bs4": "bs4",
        "emoji": "emoji",
        "markdownify": "markdownify",
        "psutil": "psutil",
        "pyautogui": "pyautogui",
        "pycaw": "pycaw",
        "win32gui": "pywin32",
        "win32ctypes": "pywin32-ctypes",
        "pywinauto": "pywinauto",
        "PIL": "pillow",
        "sounddevice": "sounddevice",
        "soundfile": "soundfile",
        "packaging": "packaging",
    }
    missing = [
        pkg for module, pkg in requirements.items() if importlib.util.find_spec(module) is None
    ]
    result: dict[str, Any] = {
        "windows": platform.system() == "Windows",
        "source_found": (scripts / "pyweixin" / "WeChatAuto.py").is_file(),
        "missing_packages": missing,
        "ui_checked": False,
        "ready": False,
    }
    if not inspect_ui or not result["windows"] or missing or not result["source_found"]:
        return result
    # Attach only. Do not start WeChat, enable accessibility, or touch settings.
    import pythoncom
    import win32gui
    from pywinauto import Desktop

    pythoncom.CoInitialize()
    try:
        hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "微信") or win32gui.FindWindow(
            "Qt51514QWindowIcon", "Weixin"
        )
        result["ui_checked"] = True
        result["window_found"] = bool(hwnd)
        result["ready"] = bool(
            hwnd and Desktop(backend="uia").window(handle=hwnd).class_name() == "mmui::MainWindow"
        )
    finally:
        pythoncom.CoUninitialize()
    return result


class PyWeixinDriver:
    """Bounded reads of the *already open* chat, using the reviewed local skill.

    Unlike Messages.pull_messages, this does not navigate/search a contact, so
    title/group checks happen before any message text is read. The skill's
    traversal selects messages and may scroll within this one chat.
    """

    def __init__(self, scripts: str) -> None:
        self.scripts = Path(scripts).expanduser().resolve()
        self._api: tuple[Any, ...] | None = None

    def _load(self) -> tuple[Any, ...]:
        if self._api is None:
            status = check_environment(self.scripts, inspect_ui=True)
            if not status["ready"]:
                raise WeChatError(
                    "微信依赖、登录状态或 UI 树检查未通过；请运行 wechat check。"
                    "不会自动安装、登录或重试。"
                )
            loaded = sys.modules.get("pyweixin")
            if loaded and Path(loaded.__file__).resolve().parent != self.scripts / "pyweixin":
                raise WeChatError("检测到同名 pyweixin 包，请使用干净进程及本地技能源码")
            sys.path.insert(0, str(self.scripts))
            try:
                api = importlib.import_module("pyweixin")
                ui = importlib.import_module("pyweixin.Uielements")
                utils = importlib.import_module("pyweixin.utils")
                if Path(api.__file__).resolve().parent != self.scripts / "pyweixin":
                    raise WeChatError("pyweixin 来源目录不符")
                self._api = (ui, utils, api.Tools)
            finally:
                sys.path.remove(str(self.scripts))
        return self._api

    def _window(self, contact: str) -> Any:
        import win32gui
        from pywinauto import Desktop

        ui, _, tools = self._load()
        hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "微信") or win32gui.FindWindow(
            "Qt51514QWindowIcon", "Weixin"
        )
        if not hwnd:
            raise WeChatError("微信未启动；请自行启动、登录并打开指定好友私聊")
        window = Desktop(backend="uia").window(handle=hwnd)
        if window.class_name() != "mmui::MainWindow":
            raise WeChatError("微信未登录或 UI 树不可见；请人工检查，不自动重试")
        if tools.is_group_chat(window):
            raise WeChatError("当前是群聊，本连接器只支持指定好友私聊")
        if window.child_window(**ui.Texts.CurrentChatNameText).window_text() != contact:
            raise WeChatError("当前聊天标题与白名单目标不完全一致；请手动打开正确私聊")
        if not window.child_window(**ui.Edits.CurrentChatEdit).exists(timeout=0.1):
            raise WeChatError("指定聊天的输入框不可用")
        return window

    def read(self, contact: str, my_name: str, limit: int) -> list[dict[str, str]]:
        ui, utils, _ = self._load()
        window = self._window(contact)
        items = window.child_window(**ui.Lists.FriendChatList).children(control_type="ListItem")
        if not items:
            return []
        details = utils.traverse_message(window, select=True, number=limit)
        # This skill traverses newest -> oldest. Never guess a sender or time.
        rows = []
        for text, class_name in reversed(details):
            sender = next(
                (
                    name
                    for name in (contact, my_name)
                    if text.startswith(name) and text[len(name) : len(name) + 1].isspace()
                ),
                None,
            )
            if sender is None:
                raise WeChatError("无法确定消息发送人，请检查自己的显示名和好友备注")
            rows.append(
                {
                    "sender": sender,
                    "text": text[len(sender) :].strip()
                    if class_name == "mmui::ChatTextItemView"
                    else "",
                    "type": "text" if class_name == "mmui::ChatTextItemView" else "unsupported",
                }
            )
        self._window(contact)  # a user switching the chat invalidates the read
        return rows

    def send(self, contact: str, my_name: str, text: str) -> None:
        ui, utils, _ = self._load()
        window = self._window(contact)
        edit = window.child_window(**ui.Edits.CurrentChatEdit)
        if edit.window_text().strip():
            raise WeChatError("微信输入框已有草稿，请先人工处理；不会覆盖")
        # Lower-level helper sends in the verified window, without re-searching
        # a contact. Length was bounded by the connector, so no file conversion.
        utils.send_messages_to_friend(window, messages=[text], send_delay=0.5)
        latest = self.read(contact, my_name, 1)
        if not latest or latest[-1] != {"sender": my_name, "text": text, "type": "text"}:
            raise WeChatError("已尝试发送但无法确认结果；请在微信核对，禁止自动重试")


class DemoWeChatDriver:
    """Synthetic data only. Demo cannot reach a desktop or send anything."""

    def read(self, contact: str, my_name: str, limit: int) -> list[dict[str, str]]:
        return [
            {"sender": my_name, "text": "你周末一般怎么放松？", "type": "text"},
            {
                "sender": contact,
                "text": "最近想找个安静的地方喝咖啡，你有什么想法吗？",
                "type": "text",
            },
        ][-limit:]

    def send(self, contact: str, my_name: str, text: str) -> None:
        raise WeChatError("离线演示永远不能发送消息")


class WeChatConnector:
    requires_confirmation = True
    stop_on_error = True

    def __init__(
        self,
        config: WeChatConfig,
        driver: WeChatDriver,
        *,
        send_enabled: bool = False,
        pending_path: Path | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        config.validate_identity()
        self.driver = driver
        self.send_enabled = send_enabled
        self.pending_path = pending_path
        self._snapshot: list[dict[str, str]] | None = None
        self._approval: tuple[str, str, str] | None = None
        self._failed = False

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        demo: bool = False,
        send_enabled: bool = False,
    ) -> WeChatConnector:
        if config.auto_send:
            raise WeChatError("个人微信禁止自动发送，必须逐条确认")
        if demo:
            return cls(
                WeChatConfig(allowed_contacts=["演示好友"], contact="演示好友", my_name="演示用户"),
                DemoWeChatDriver(),
            )
        config.wechat.validate_live()
        return cls(
            config.wechat,
            PyWeixinDriver(config.wechat.pyweixin_scripts),
            send_enabled=send_enabled,
            pending_path=Path(config.data_dir) / "wechat-send-pending.json",
        )

    def _target(self, match_id: str) -> None:
        if self._failed:
            raise WeChatError("本次微信操作已终止；请人工检查后重新运行")
        if match_id != self.config.contact or match_id not in self.config.allowed_contacts:
            raise WeChatError("目标不在本次指定的微信好友范围内")

    def _read(self) -> list[dict[str, str]]:
        self._target(self.config.contact)
        try:
            rows = self.driver.read(
                self.config.contact, self.config.my_name, self.config.history_limit
            )
            for row in rows:
                if row["sender"] not in {self.config.contact, self.config.my_name}:
                    raise WeChatError("检测到非指定好友消息，停止处理")
                if row["type"] not in {"text", "unsupported"}:
                    raise WeChatError("无法识别消息类型")
            return rows
        except Exception as exc:
            self._failed = True
            raise WeChatError("微信读取失败，本次操作已停止；请检查界面和配置，不自动重试") from exc

    async def get_recommendations(self, limit: int = 10) -> list[Candidate]:
        return []

    async def like(self, candidate_id: str) -> dict[str, Any]:
        raise WeChatError("个人微信不支持筛选或添加陌生人")

    async def pass_(self, candidate_id: str) -> dict[str, Any]:
        raise WeChatError("个人微信不支持滑动筛选")

    async def get_matches(self, count: int = 60) -> list[Match]:
        if count <= 0:
            return []
        messages = await self.get_messages(self.config.contact)
        return [
            Match(
                id=self.config.contact,
                name=self.config.contact,
                messages=messages,
                raw={"messages_loaded": True},
            )
        ]

    async def get_messages(self, match_id: str, count: int = 100) -> list[Message]:
        self._target(match_id)
        self._approval = None
        self._snapshot = self._read()
        rows = self._snapshot[-count:] if count > 0 else []
        return [
            Message(
                id=f"wechat:{fingerprint([row])}:{i}",
                match_id=match_id,
                sender="me" if row["sender"] == self.config.my_name else "them",
                text=row["text"] if row["type"] == "text" else "[暂不支持的非文本消息]",
                raw={"unsupported": row["type"] != "text"},
            )
            for i, row in enumerate(rows)
        ]

    def approve_message(self, match_id: str, text: str) -> None:
        self._target(match_id)
        if not self.send_enabled or self._snapshot is None:
            raise WeChatError("发送未启用或尚未读取本次上下文")
        self._approval = (match_id, text, fingerprint(self._snapshot))

    async def send_message(self, match_id: str, text: str) -> Message:
        self._target(match_id)
        approval, self._approval = self._approval, None  # consume even if the send fails
        if not self.send_enabled or not approval or approval[:2] != (match_id, text):
            raise WeChatError("每条消息都需要本次人工确认，默认只生成建议")
        if not text.strip() or len(text) > 500 or any(ord(c) < 32 and c != "\n" for c in text):
            raise WeChatError("仅支持 1–500 字纯文本回复")
        current = self._read()
        if fingerprint(current) != approval[2]:
            raise WeChatError("确认期间聊天已变化，请重新生成并审阅建议")
        if (
            not current
            or current[-1]["sender"] == self.config.my_name
            or current[-1]["type"] != "text"
        ):
            raise WeChatError("仅能回复好友的最新文本，禁止主动开场或连续追发")
        if self.pending_path is not None:
            self.pending_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.pending_path.open("x", encoding="utf-8") as handle:
                    json.dump(
                        {"target": match_id, "text_hash": fingerprint([{"text": text}])}, handle
                    )
            except FileExistsError as exc:
                raise WeChatError(
                    "有上次未确认发送的记录；请人工核对后处理 wechat-send-pending.json"
                ) from exc
        try:
            self.driver.send(match_id, self.config.my_name, text)
        except Exception as exc:
            self._failed = True
            raise WeChatError("发送结果未确认，已停止且不会重试；请在微信人工核对") from exc
        self._snapshot = None
        return Message(
            id=f"wechat:sent:{fingerprint([{'text': text}])}",
            match_id=match_id,
            sender="me",
            text=text,
            sent_at=datetime.now(timezone.utc),
        )

    def acknowledge_send(self) -> None:
        """Clear the recovery marker only after the orchestrator saved pacing state."""
        if self.pending_path is not None:
            self.pending_path.unlink()

    async def close(self) -> None:
        self._approval = None
        self._snapshot = None
