"""Conservative, replayable judgments over the visible WeChat conversation.

These are workflow hints, never a measurement of attraction or confirmation of
a relationship. Only actual messages can change an invitation's status.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from .connectors.base import Message

INVITATION_LABELS = {
    "none": "未见明确邀约", "pending": "已提出，待对方回应", "accepted": "已接受，核对安排",
    "declined": "本次邀约被拒绝", "deferred": "暂缓，尚未约定", "rescheduled": "提出改期，待确认",
    "cancelled": "本次安排已取消", "expired": "已过期，是否见面待核实", "completed": "已有见面完成记录",
}
ACTIVE_INVITATIONS = {"pending", "accepted", "rescheduled"}
_ACTIVITY = r"吃饭|吃个饭|喝咖啡|喝杯咖啡|看展|看电影|看场电影|散步|出去走走"
_INVITE = re.compile(rf"一起[^。！？!?\n]{{0,16}}(?:{_ACTIVITY})|见(?:个)?面|约个时间|(?:和你|我们|咱俩)约会|周[末六日天]有空吗")
_DECLINE = re.compile(r"不去|不想去|去不了|没空|没时间|不方便|不行|不想见|不见面|不想约|没法去")
_DEFER = re.compile(r"改天|再说|看情况|到时候再看|以后再约|下次吧|最近忙")
_CANCEL = re.compile(r"取消|不约了|不见了|别来了|不用来了|不去了|去不成了")
_COMPLETE = re.compile(rf"(?:今天|昨天)(?:见面|约会|一起(?:{_ACTIVITY}))[^。！？!?]{{0,8}}(?:很开心|很愉快)|谢谢你今天(?:陪我|请我|带我)(?:{_ACTIVITY})")
_PAST = re.compile(r"上次|昨天|之前|曾经|已经|约过|见过")
_INVITE_REFERENCE = re.compile(r"见面|约|去不了|不想去|不去了|去不成|没法去|别来|不用来|那天|明天|后天|周[一二三四五六日天末]")
_ACCEPT = re.compile(r"^(?:嗯[，, ]*|哈哈[，, ]*)?(?:好呀|好啊|好的|好|行呀|行啊|行|可以呀|可以|没问题|就这么定了|不见不散)[呀啊吧呢，,。.!！~～ ]*$")
_SCHEDULE = re.compile(r"改到|改成|换到|换成|要不|不如|(?:周[一二三四五六日天]|明天|后天)[^。！？!?]{0,8}(?:吧|怎么样|可以吗|行吗)")
_THIRD_PARTY = re.compile(r"(?:和|跟)(?:朋友|同事|客户|闺蜜|他|她)|(?:朋友|同事|客户|他|她)(?:说|约|邀请)|如果|假如|比如|[“「\"]")
_ROMANTIC = re.compile(r"(?:我)?(?:很|真的|有点)?喜欢你|(?:我)?对你有好感|想和你谈恋爱|想做你(?:男|女)朋友")
_NOT_ROMANTIC = re.compile(r"不喜欢|不想|没有|没什么|谈不上|别|开玩笑|如果|假如|比如|[“「\"]|你喜欢我|喜欢你(?:的|家)|(?:朋友|他|她)说")


def romantic_signal(text: str) -> bool:
    """Require an explicit positive statement; laughter/emoji are only tone."""
    return bool(_ROMANTIC.search(text) and not _NOT_ROMANTIC.search(text))


@dataclass(frozen=True)
class Invitation:
    status: str = "none"
    proposer: str = ""
    updated_at: str = ""
    target_date: str = ""
    expires_at: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_INVITATIONS


def _target_date(text: str, sent: datetime) -> date | None:
    """Resolve only unambiguous dates; never invent a time for vague '改天'."""
    absolute = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
    if absolute:
        try:
            return date(*map(int, absolute.groups()))
        except ValueError:
            return None
    for word, days in (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0)):
        if word in text:
            return sent.date() + timedelta(days=days)
    weekdays = list(re.finditer(r"(下周|这周|本周|周|星期)([一二三四五六日天])", text))
    if weekdays:
        weekday = weekdays[-1]
        index = "一二三四五六日".index(weekday[2].replace("天", "日"))
        delta = index - sent.weekday()
        if weekday[1] == "下周":
            delta += 7
        elif weekday[1] in {"周", "星期"} and delta < 0:
            delta += 7
        return sent.date() + timedelta(days=delta)
    return None


def _expiry(sent: datetime, target: date | None) -> str:
    # A scheduled day ending is expiry, never proof that the meeting happened.
    end = (datetime.combine(target + timedelta(days=1), datetime.min.time(), sent.tzinfo)
           if target else sent + timedelta(days=7))
    return end.isoformat()


def _expire(invite: Invitation, at: datetime) -> Invitation:
    if invite.active and invite.expires_at and at >= datetime.fromisoformat(invite.expires_at):
        return replace(invite, status="expired")
    return invite


def invitation_state(messages: list[Message], *, now: datetime | None = None) -> Invitation:
    """Replay chronological, visible messages; goals/drafts never enter here.

    A bare '好' only accepts the other person's immediately preceding proposal.
    Refusals close the current attempt; ordinary later replies cannot reopen it.
    Relative dates use the caller's timezone. Unrecognized dates stay unknown.
    """
    now = now or datetime.now().astimezone()
    invite = Invitation()
    for index, msg in enumerate(messages):
        text = msg.text.strip()
        if not text or not msg.sent_at or _THIRD_PARTY.search(text):
            continue
        sent = msg.sent_at.astimezone(now.tzinfo)
        if sent > now:
            continue
        invite = _expire(invite, sent)
        evidence = {"message_id": msg.id, "sender": msg.sender,
                    "timestamp": msg.sent_at.isoformat(), "text": text}

        def update(status, *, proposal=False, target=None):
            nonlocal invite
            invite = Invitation(
                status=status, proposer=msg.sender if proposal else invite.proposer,
                updated_at=sent.isoformat(),
                target_date=target.isoformat() if target else ("" if proposal else invite.target_date),
                expires_at=_expiry(sent, target) if proposal else invite.expires_at,
                evidence=([evidence] if proposal and not invite.active
                          else [*invite.evidence[-2:], evidence]),
            )

        adjacent = bool(invite.evidence and index > 0
                        and messages[index - 1].id == invite.evidence[-1]["message_id"])
        responds = adjacent or bool(_INVITE_REFERENCE.search(text))
        if _COMPLETE.search(text) and not (_DECLINE.search(text) or _CANCEL.search(text)):
            update("completed")
        elif invite.active and responds and _CANCEL.search(text):
            update("cancelled")
        else:
            target = _target_date(text, sent)
            counter = invite.active and msg.sender != invite.proposer
            if (counter and responds and target and _SCHEDULE.search(text)
                    and target.isoformat() != invite.target_date):
                update("rescheduled", proposal=True, target=target)
            elif invite.active and responds and _DECLINE.search(text):
                update("declined" if counter else "cancelled")
            elif invite.active and responds and _DEFER.search(text):
                update("deferred")
            elif counter and adjacent and (
                    _ACCEPT.fullmatch(text)
                    or (target and re.search(r"那就|就定|见(?:面)?吧?[！!。.]?$|不见不散", text))):
                if target and target.isoformat() != invite.target_date:
                    # A newly supplied or changed date needs acknowledgement.
                    update("rescheduled", proposal=True, target=target)
                else:
                    update("accepted")
            elif _INVITE.search(text) and not (_DECLINE.search(text) or _DEFER.search(text)
                                             or _CANCEL.search(text) or _PAST.search(text)):
                # Repeating our own unanswered invitation is not a new attempt.
                if not invite.active or counter:
                    update("pending", proposal=True, target=target)
    return _expire(invite, now)
