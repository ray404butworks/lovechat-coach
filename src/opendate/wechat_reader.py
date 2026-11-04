"""Pinned upstream reader adapter. Key capture is intentionally not an action here.

Directory listing uses upstream's cache-only metadata API. Export requires an
explicit account + conversation choice in the GUI, never an all-account export.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import secrets
import threading
from typing import Callable
from pathlib import Path

from .wechat_memory import HistoryError

READER_SHA256 = "11cc1b6916c265ffb5621a05e8909f39aa4f47222101444e03c02c9bbf00246c"
READER_VERSION = "v0.3.5"
AUTH_SOURCE_SHA256 = "e0f484f7f55b7d581f26428d6df13c87302f7d5d52da39329d1ba0cfacbc4558"


class LocalHistoryReader:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.exe = self.project / "work/chatlog-reader/chatlog-keeper.exe"

    def run(self, args: list[str], *, selection: dict | None = None,
            timeout: int = 180, accept_failure: bool = False, source_authorization: bool = False) -> dict:
        if not self.exe.is_file():
            raise HistoryError("本地历史读取器尚未安装。可先使用导入 JSON 或当前画面。")
        with self.exe.open("rb") as reader_file:
            actual_hash = hashlib.file_digest(reader_file, "sha256").hexdigest()
        if actual_hash != READER_SHA256:
            raise HistoryError("读取器版本或完整性已变化，已停止；请重新核对官方版本。")
        scratch = self.project / "work/tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "USERPROFILE",
                                  "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "COMSPEC",
                                  "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
                                  "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
                                  "COMMONPROGRAMW6432", "SYSTEMDRIVE", "HOMEDRIVE", "HOMEPATH",
                                  "USERNAME", "USERDOMAIN", "COMPUTERNAME", "PUBLIC", "OS",
                                  "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}}
        env.update(TEMP=str(scratch), TMP=str(scratch), PYTHONUTF8="1")
        env["CHATLOG_KEEPER_DATA_DIR"] = str(self.project / "data/wechat/reader-private")
        command = [str(self.exe), *args]
        if source_authorization:
            python = self.project / ".venv/Scripts/python.exe"
            module = self.project / ".venv/Lib/site-packages/chatlog_keeper/active_key.py"
            if not python.is_file() or not module.is_file():
                raise HistoryError("缺少已适配的本机授权组件，请先完成读取器安装。")
            if hashlib.sha256(module.read_bytes()).hexdigest() != AUTH_SOURCE_SHA256:
                raise HistoryError("授权组件完整性已变化，请核对已适配版本后再继续。")
            command = [str(python), "-I", "-m", "chatlog_keeper.cli", *args]
        try:
            result = subprocess.run(
                command, input=json.dumps(selection) if selection else None,
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                cwd=self.project, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise HistoryError("本地历史读取未完成或超时；没有启动密钥提取或重启微信。") from None
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise HistoryError("读取器没有返回可验证结果，请使用 JSON 导入；未显示原始诊断数据。") from None
        if not isinstance(data, dict) or (
            result.returncode and data.get("available") is not False and not accept_failure
        ):
            raise HistoryError("本地读取器未成功。请检查现有本机授权，或使用已导出的 JSON。")
        return data

    def authorize(self, data_root: str, *, timeout: int = 300,
                  cancelled: Callable[[], bool] = lambda: False) -> dict:
        """Explicit GUI action only. Private protocol refuses a running client.

        Unlike legacy extract-key defaults this can never force-close the user's
        ordinary WeChat. It owns and cleans only its new authentication process.
        """
        root = Path(data_root)
        config_file = self.project / "config.wechat.yaml"
        if config_file.is_file():
            from .config import load_config
            settings = load_config(config_file).wechat
            if (settings.history_data_root and settings.history_authorization_root
                    and root.resolve() == Path(settings.history_data_root).resolve()):
                account_root = Path(settings.history_authorization_root)
                if account_root.parent.resolve() != root.resolve():
                    raise HistoryError("授权账号目录不属于已配置的微信数据目录，请重新核对。")
                root = account_root
        if root.is_dir() and not (root / "db_storage/message").is_dir():
            accounts = [p for p in root.iterdir() if p.is_dir()
                        and (p / "db_storage/message").is_dir()]
            if len(accounts) > 1:
                raise HistoryError("存在多个微信账号。请在授权窗口填入当前账号的完整目录，避免用旧账号校验。")
            if len(accounts) == 1:
                root = accounts[0]
        data_root = str(root)
        request = {"schema": "chatlog-keeper.key-recovery-request.v1",
                   "operation_id": secrets.token_hex(32), "timeout_seconds": timeout,
                   "confirmed": True}
        finished = threading.Event()
        def monitor():
            while not finished.wait(0.5):
                if cancelled():
                    try:
                        self.run(["key-recovery-v1", "--action", "cancel", "--request-stdin"],
                                 selection={"schema": "chatlog-keeper.key-recovery-control-request.v1",
                                            "operation_id": request["operation_id"]},
                                 timeout=20, accept_failure=True)
                    except HistoryError:
                        pass
                    return
        threading.Thread(target=monitor, daemon=True).start()
        try:
            result = self.run(["extract-key", "--source", "wechat", "--method", "active",
                               "--data-root", data_root, "--key-recovery-v1-stdin"],
                              selection=request, timeout=timeout + 30, accept_failure=True,
                              source_authorization=True)
            if (result.get("terminal") and result.get("operation_id") == request["operation_id"]
                    and result.get("error_code") not in {"active_operation_exists", "operation_exists"}):
                cleaned = self.run(["key-recovery-v1", "--action", "cleanup", "--request-stdin"],
                                   selection={"schema": "chatlog-keeper.key-recovery-control-request.v1",
                                              "operation_id": request["operation_id"]},
                                   accept_failure=True)
                if not cleaned.get("ok"):
                    return {"ok": False, "terminal": True, "error_code": "cleanup_failed"}
            return result
        finally:
            finished.set()

    def directory(self, contact: str, data_root: str = "") -> list[dict]:
        args = ["directory", "--source", "wechat"]
        if data_root:
            args += ["--data-root", data_root]
        data = self.run(args)
        if not data.get("available"):
            raise HistoryError(
                "尚无可用的本机微信历史读取授权（或未定位到数据目录）。\n"
                "读取器没有读取聊天正文，也没有自动提取密钥或重启微信。\n"
                "可选择正确的 xwechat_files 文件夹重试；若仍失败，需要先完成首次密钥读取适配。\n"
                "目前仍可使用当前画面或导入已有 JSON。"
            )
        choices = [item for item in data.get("conversations", [])
                   if item.get("conversation_type") == "direct" and item.get("label") == contact]
        if not choices:
            raise HistoryError("历史读取已就绪，但未找到与好友完整备注一致的私聊；没有导出其他会话。")
        return choices

    def export(self, choice: dict, data_root: str = "") -> Path:
        account = choice.get("account_id")
        conversation = choice.get("conversation_id")
        if not account or not conversation or choice.get("conversation_type") != "direct":
            raise HistoryError("必须先明确选择本机账号和一位私聊对象。")
        scope = {"account_id": account, "conversation_id": conversation, "conversation_type": "direct"}
        selection = {"account_ids": [account], "conversation_ids": [], "conversation_scopes": [scope]}
        key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:20]
        out = self.project / "data/wechat/reader-export" / key
        args = ["wechat", "--days", "36500", "--out", str(out), "--selection-stdin"]
        if data_root:
            args += ["--data-root", data_root]
        data = self.run(args, selection=selection)
        path = out / "wechat_messages.json"
        if not data.get("available") or not path.is_file():
            raise HistoryError("指定好友的历史导出未完成，尚未导入；没有改为导出全部联系人。")
        return path
