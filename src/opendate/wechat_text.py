"""Local TXT transcript parsing. Chat text is data, never executable instructions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .wechat_memory import HistoryError

_DATE = re.compile(
    r"^(\d{4})(?:年|[-/])(\d{1,2})(?:月|[-/])(\d{1,2})(?:日)?"
    r"[ \t]+(\d{1,2}):(\d{2})(?::(\d{2}))?$"
)
_QUOTE = re.compile(r"^(?:回复|引用|Reply(?:ing)?(?: to)?)[：:]|^>", re.IGNORECASE)


class TxtIdentityError(HistoryError):
    """Two source names are known, but their ownership requires confirmation."""
    def __init__(self, participants):
        self.participants = tuple(participants)
        super().__init__("TXT 中的昵称与当前对象或本人显示名不一致，请先核对双方身份。")


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    encodings = (["utf-16"] if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
                 else ["utf-8-sig", "gb18030"])
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        if "\x00" not in text and "\ufffd" not in text:
            return text
    raise HistoryError("无法识别 TXT 编码，请另存为 UTF-8 或带 BOM 的 UTF-16 后重新选择。")


def load_txt(path: Path, contact: str, my_name: str,
             sender_names: tuple[str, str] | None = None) -> tuple[dict, tuple[str, str]]:
    """Read sender / date-time / multiline-body records, preserving quote blocks.

    Supported time lines: YYYY年M月D日 HH:MM[:SS], YYYY-MM-DD HH:MM[:SS]
    and YYYY/MM/DD HH:MM[:SS]. Naive TXT dates explicitly use Beijing time.
    A standalone sender line followed by a valid date starts a message, with
    or without blank separators. Prefixed quote attribution stays in the body.
    """
    lines = _decode(path).splitlines()
    headers = []
    for i in range(len(lines)-1):
        name = lines[i].strip()
        date = _DATE.fullmatch(lines[i+1].strip())
        if name and len(name) <= 128 and date and not _QUOTE.search(name):
            headers.append((i, name, date))
    if not headers:
        raise HistoryError("未识别出 TXT 消息。当前支持：昵称一行、日期时间一行，后面为消息正文。")
    if any(line.strip() for line in lines[:headers[0][0]]):
        raise HistoryError("TXT 开头存在无法识别的内容，请确认文件从第一条消息的昵称开始，避免漏读。")
    participants = tuple(dict.fromkeys(name for _, name, _ in headers))
    expected = (my_name, contact)
    if not my_name.strip() or not contact.strip() or my_name == contact:
        raise HistoryError("请先设置不同的本人显示名和聊天对象，再导入 TXT。")
    if len(participants) > 2:
        raise HistoryError("TXT 中识别到超过两位发送人，可能含群聊或混合记录，请先整理为单个对象的私聊。")
    if sender_names is None:
        if not all(name in expected for name in participants):
            if len(participants) != 2:
                raise HistoryError("TXT 仅识别到一个未匹配昵称，无法核对双方身份，请先补齐或核对本人及对象显示名。")
            raise TxtIdentityError(participants)
        sender_names = expected
    if (len(sender_names) != 2 or sender_names[0] == sender_names[1]
            or not all(name in sender_names for name in participants)):
        raise HistoryError("TXT 身份对应关系已变化或不完整，请重新选择文件并核对双方昵称。")
    roles = {sender_names[0]: "me", sender_names[1]: "peer"}
    messages = []
    for index, (start, speaker, date) in enumerate(headers):
        end = headers[index+1][0] if index+1 < len(headers) else len(lines)
        body = "\n".join(lines[start+2:end]).strip("\n")
        if not body.strip():
            raise HistoryError(f"TXT 第 {start+1} 行开始的消息没有正文；请核对原文，避免漏掉记录。")
        try:
            stamp = datetime(*(int(value or 0) for value in date.groups()),
                             tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            raise HistoryError(f"TXT 第 {start+2} 行日期时间无效，请核对原文。") from None
        # Detect a broken, exact-name header inside a body instead of quietly
        # treating a real next message with a missing timestamp as quote text.
        for i in range(start+2, end-1):
            if (lines[i].strip() in roles
                    and re.match(r"^\d{4}(?:年|[-/])", lines[i+1].strip())
                    and not _DATE.fullmatch(lines[i+1].strip())):
                raise HistoryError(f"TXT 第 {i+2} 行的消息时间格式无效，请核对原文。")
        messages.append({"timestamp": stamp.isoformat(), "sender": roles[speaker], "text": body})
    return {"contact":contact, "messages":messages}, sender_names
