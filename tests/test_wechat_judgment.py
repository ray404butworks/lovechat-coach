"""Behavioral regressions for tone, stage and invitation lifecycle."""
from datetime import datetime, timedelta, timezone

import pytest

from opendate.connectors.base import Message
from opendate.wechat_judgment import invitation_state, romantic_signal

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone(timedelta(hours=8)))  # Friday


def messages(*rows):
    return [Message(id=str(i), match_id="甲", sender=sender, text=text,
                    sent_at=NOW - timedelta(hours=hours))
            for i, (sender, text, hours) in enumerate(rows)]


@pytest.mark.parametrize("text", ["哈哈", "好呀", "哈哈跟你聊天好开心", "😊", "你就吹吧",
                                  "我喜欢你家的猫", "我不喜欢你", "你喜欢我吗？",
                                  "如果我喜欢你呢", "朋友说我喜欢你", "没有对你有好感"])
def test_tone_negation_and_hypotheticals_are_not_romantic_evidence(text):
    assert not romantic_signal(text)


@pytest.mark.parametrize("text", ["我喜欢你", "我对你有好感", "想和你谈恋爱"])
def test_explicit_positive_statements_remain_available(text):
    assert romantic_signal(text)


@pytest.mark.parametrize("reply,status", [
    ("好呀", "accepted"), ("周六没空", "declined"), ("不方便见面", "declined"),
    ("改天再说", "deferred"), ("最近忙，下次吧", "deferred"),
    ("周六不行，周日吧", "rescheduled"), ("后天怎么样？", "rescheduled"),
    ("明天怎么样？", "pending"),
    ("取消吧", "cancelled"), ("哈哈", "pending"), ("我喜欢看展", "pending"),
])
def test_response_updates_only_the_observed_invitation(reply, status):
    result = invitation_state(messages(("me", "周六一起看展？", 2), ("them", reply, 1)), now=NOW)
    assert result.status == status
    assert result.evidence[0]["text"] == "周六一起看展？"
    if reply == "周六不行，周日吧":
        assert result.target_date == "2026-11-08"


def test_reschedule_requires_the_other_person_to_confirm():
    rows = messages(("me", "周六一起看展？", 3), ("them", "周六不行，周日吧", 2))
    result = invitation_state(rows, now=NOW)
    assert result.status == "rescheduled" and result.proposer == "them"
    rows += messages(("me", "好呀", 1))
    result = invitation_state(rows, now=NOW)
    assert result.status == "accepted" and result.target_date == "2026-11-08"


@pytest.mark.parametrize("reply", ["好，周六见面", "周六见", "周六见面吧"])
def test_confirmation_of_the_same_date_is_not_a_new_invitation(reply):
    result = invitation_state(messages(("me", "周六一起看展？", 2), ("them", reply, 1)), now=NOW)
    assert result.status == "accepted" and result.target_date == "2026-11-07"


@pytest.mark.parametrize("ending", ["好呀", "哈哈", "今天天气不错"])
def test_refusal_is_not_undone_by_an_unrelated_friendly_reply(ending):
    result = invitation_state(messages(("me", "周六一起吃饭？", 4), ("them", "去不了", 3),
                                       ("me", "今天降温了", 2), ("them", ending, 1)), now=NOW)
    assert result.status == "declined" and not result.active


def test_acceptance_must_answer_the_proposal_rather_than_another_topic():
    result = invitation_state(messages(("me", "周六一起吃饭？", 4), ("them", "今天很忙", 3),
                                       ("me", "先忙工作吧", 2), ("them", "好呀", 1)), now=NOW)
    assert result.status == "pending"


@pytest.mark.parametrize("text", ["周六一起吃饭？", "一起喝咖啡？", "周末有空吗？"])
def test_proposals_expire_without_an_observed_new_invitation(text):
    result = invitation_state(messages(("me", text, 24 * 15), ("them", "好呀", 1)), now=NOW)
    assert result.status == "expired" and not result.active


def test_acceptance_does_not_prove_a_meeting_happened():
    rows = messages(("me", "2026-11-05一起看电影？", 48), ("them", "好呀", 47))
    result = invitation_state(rows, now=NOW)
    assert result.status == "expired"
    result = invitation_state([*rows, *messages(("them", "昨天约会很开心", 1))], now=NOW)
    assert result.status == "completed"


def test_repeated_outgoing_proposal_does_not_reset_timeout():
    rows = messages(("me", "一起喝咖啡？", 160), ("me", "一起喝咖啡？", 10))
    result = invitation_state(rows, now=NOW + timedelta(hours=9))
    assert result.status == "expired"


@pytest.mark.parametrize("text", ["喜欢看电影", "和朋友一起吃饭", "他约我周六见面",
                                  "如果一起吃饭会怎样", "她说“周六一起吃饭？”", "我不想约会",
                                  "我喜欢约会", "之前一起吃饭", "昨天见面取消了"])
def test_preferences_third_party_and_hypothetical_meetings_do_not_open_invites(text):
    assert invitation_state(messages(("them", text, 1)), now=NOW).status == "none"


def test_completed_meeting_without_a_visible_proposal_is_not_a_new_invite():
    result = invitation_state(messages(("them", "今天见面很开心", 1)), now=NOW)
    assert result.status == "completed" and not result.active


def test_unrelated_rejection_does_not_close_an_invite():
    result = invitation_state(messages(("me", "周六一起吃饭？", 4), ("them", "我先工作", 3),
                                       ("me", "代码调好了吗", 2), ("them", "这个代码不行", 1)), now=NOW)
    assert result.status == "pending"


def test_timezone_and_past_explicit_dates_do_not_roll_forward():
    # It is Thursday UTC but Friday locally. '明天' must mean local Saturday.
    rows = [Message(id="1", match_id="甲", sender="them", text="明天一起吃饭？",
                    sent_at=datetime(2026, 11, 5, 17, tzinfo=timezone.utc))]
    assert invitation_state(rows, now=NOW).target_date == "2026-11-07"
    result = invitation_state(messages(("me", "这周四一起吃饭？", 1)), now=NOW)
    assert result.status == "expired"


def test_same_party_cannot_accept_their_own_invitation():
    result = invitation_state(messages(("me", "周六一起吃饭？", 2), ("me", "好呀", 1)), now=NOW)
    assert result.status == "pending"
