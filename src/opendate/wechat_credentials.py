"""Selected-provider credentials, encrypted for this Windows user with DPAPI."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .config import Secrets
from .llm.providers import PROVIDER_REGISTRY
from .utils.logging import register_secret
from .wechat_memory import HistoryError


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise HistoryError("本地密钥保险箱仅支持 Windows；其他系统请使用环境变量配置 API Key。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        call = crypt.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = [ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)]
    else:
        call = crypt.CryptProtectData
        call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = [ctypes.byref(source), "LoveChat Coach", None, None, None, 1, ctypes.byref(target)]
    call.restype = wintypes.BOOL
    if not call(*args):
        raise HistoryError("Windows 密钥保险箱无法访问，请在当前 Windows 账号下重新配置 API Key。")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def save_api_key(project: Path, data_dir: str, provider: str, key: str) -> None:
    spec = PROVIDER_REGISTRY[provider]
    if not spec.api_key_env or not key.strip() or any(c in key for c in "\r\n\x00"):
        raise HistoryError("请填写所选服务的有效 API Key，不要包含换行。")
    register_secret(key)
    path = project / data_dir / "provider-secrets.json"
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        existing[provider] = base64.b64encode(_dpapi(key.strip().encode())).decode("ascii")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(existing), encoding="utf-8")
        temp.replace(path)
    except (OSError, ValueError, TypeError):
        raise HistoryError("API Key 未能保存，请检查本地私有数据目录权限。") from None


def provider_secrets(project: Path, data_dir: str, provider: str) -> dict:
    """Load only the selected vault entry; never mutate process environment."""
    env_file = project / ".env"
    secrets = Secrets(_env_file=str(env_file) if env_file.exists() else None)
    secrets.register_for_redaction()
    result = secrets.env_map()
    path = project / data_dir / "provider-secrets.json"
    if path.exists() and provider != "codex":
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if provider in saved:
                key = _dpapi(base64.b64decode(saved[provider], validate=True), decrypt=True).decode()
                register_secret(key)
                result[PROVIDER_REGISTRY[provider].api_key_env] = key
        except (OSError, ValueError, TypeError, UnicodeError):
            raise HistoryError("本地 API Key 无法读取，请重新打开配置引导填写。") from None
    return result
