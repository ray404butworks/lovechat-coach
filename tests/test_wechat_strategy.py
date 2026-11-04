import json
import tkinter as tk
from datetime import datetime, timedelta, timezone

import pytest

from opendate.config import AppConfig
from opendate.wechat_desktop import build_reply_context, generate_reply_with_strategy
from opendate.wechat_memory import ChatMemory, ChatRecord, ConversationBrief, HistoryBatch, HistoryError
from opendate.wechat_profile import MyPersona, save_settings as save_profile
from opendate.wechat_strategy import (
    SKILL_LABELS, StrategySettings, latest_report, load_settings, report_text, save_settings,
)


class Router:
    """Fake transport; real OpenDate components still perform every step."""
    def __init__(self, *, quality=None, safety=None, style=None):
        self.calls = []
        self.quality = iter(quality or [0.92] * 5)
        self.safety = safety or '{"allowed":true,"reason":"尊重对方"}'
        self.style = style or "行嘞，周日下午一起去看展？"

    def chat(self, system, user, **kwargs):
        phase = system.split("WORKFLOW_PHASE: ")[1].splitlines()[0]
        packet = json.loads(user)
        self.calls.append((phase, system, packet))
        if phase == "selection":
            return '{"skill":"rapport-building"}'
        if phase == "quality":
            return json.dumps({"score": next(self.quality), "issues": []})
        if phase == "safety":
            return self.safety
        return self.style if phase == "style" else "周日下午一起去看展吧？"


def setup(tmp_path, monkeypatch, *, router=None, settings=None, incoming="周末有什么计划？"):
    config = AppConfig(source="wechat", llm={"provider": "codex"},
                       wechat={"contact": "对象甲", "my_name": "本人", "allowed_contacts": ["对象甲"]})
    config = save_profile(tmp_path, config, MyPersona(voice="简短，以行嘞开头", avoid="不用感叹号"), "对象甲", "本人")
    memory = ChatMemory(tmp_path / config.data_dir / "wechat-memory.sqlite3")
    memory.save_brief("对象甲", ConversationBrief("我不吃香菜", "对方喜欢画展", "周日下午一起看展"))
    memory.save_brief("对象乙", ConversationBrief("乙的秘密", "乙的资料", "乙的目标"))
    save_settings(memory, "对象甲", settings or StrategySettings())
    router = router or Router()
    monkeypatch.setattr("opendate.wechat_desktop.make_router", lambda *_: router)
    return config, memory, router, build_reply_context(config, tmp_path, memory, incoming)


def archive(memory, rows):
    records = [ChatRecord((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(),
                         sender, text, str(i)) for i, (hours, sender, text) in enumerate(rows)]
    memory.import_batch("对象甲", HistoryBatch("private", "甲", records), "test")


def test_real_components_run_and_all_phases_keep_facts_goals_and_skill_playbooks(tmp_path, monkeypatch):
    config, memory, router, packet = setup(tmp_path, monkeypatch)
    archive(memory, [(2, "me", "好呀，周末出去走走"), (1, "peer", "我喜欢看展")])
    packet = build_reply_context(config, tmp_path, memory, "周末有什么计划？")
    result = generate_reply_with_strategy(config, tmp_path, packet)
    phases = [p for p, _, _ in router.calls]
    assert phases == ["selection", "draft", "style", "quality", "safety"]
    assert not result.blocked
    assert result.text.startswith("行嘞")
    for _, system, actual in router.calls:
        assert "不可信" in system
        assert actual["participants"]["me"]["confirmed_information"] == "我不吃香菜"
        assert actual["conversation_goal"]["desired_outcome"] == "周日下午一起看展"
        assert "乙的" not in json.dumps(actual, ensure_ascii=False)
    draft_system = next(s for p, s, _ in router.calls if p == "draft")
    for skill in ["rapport-building", "relationship-intent-matching", "persona-style-transfer", "consent-and-safety"]:
        assert "## Skill: " + skill in draft_system
    assert result.report["persona_samples"] == 1
    assert "口吻" in report_text(result.report)
    assert latest_report(memory, "对象甲")["status"] == "passed"
    assert latest_report(memory, "对象乙") == {}
    assert memory.stats("对象甲")[0] == 2
    with memory.connect() as db:
        state = json.loads(db.execute("SELECT data FROM strategy_state WHERE contact='对象甲'").fetchone()[0])
    assert state["sent_count"] == 0 and state["last_sent_at"] is None
    assert result.text not in state["outgoing_history"]


@pytest.mark.parametrize("incoming", ["别再联系我", "你这样让我不舒服", "我今年16岁"])
def test_boundary_preflight_stops_before_any_model_request(tmp_path, monkeypatch, incoming):
    config, memory, router, packet = setup(tmp_path, monkeypatch, incoming=incoming,
                                          settings=StrategySettings(skill="flirting", stage="flirting"))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.blocked and result.text == ""
    assert router.calls == []
    assert latest_report(memory, "对象甲")["status"] == "blocked"


def test_quality_rejection_actually_rewrites_and_reviews_new_draft(tmp_path, monkeypatch):
    router = Router(quality=[0.2, 0.9])
    config, memory, _, packet = setup(tmp_path, monkeypatch, router=router)
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert not result.blocked
    assert [p for p, _, _ in router.calls].count("draft") == 2
    assert [p for p, _, _ in router.calls].count("style") == 2
    assert [p for p, _, _ in router.calls].count("quality") == 2
    assert any("重新生成" in s for s in result.report["steps"])
    assert memory.stats("对象甲")[0] == 0


def test_exhausted_reviews_never_expose_a_failed_draft(tmp_path, monkeypatch):
    config, memory, _, packet = setup(tmp_path, monkeypatch, router=Router(quality=[0.1] * 5))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.blocked and not result.text
    assert "用尽" in result.report["steps"][-1]


def test_model_safety_rejection_is_enforced_through_final_retry(tmp_path, monkeypatch):
    router = Router(safety='{"allowed":false,"reason":"在替对方做未经确认的承诺"}')
    config, memory, _, packet = setup(tmp_path, monkeypatch, router=router)
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.blocked and not result.text
    assert len([p for p, _, _ in router.calls if p == "safety"]) == 2
    assert latest_report(memory, "对象甲")["status"] == "blocked"


@pytest.mark.parametrize("incoming,skill", [("哈哈你就吹吧，算你赢", "banter"),
                                            ("哈哈跟你聊天好开心", "flirting")])
def test_chinese_tone_reaches_original_automatic_skills(tmp_path, monkeypatch, incoming, skill):
    config, _, _, packet = setup(tmp_path, monkeypatch, incoming=incoming)
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.report["skill"] == skill


def test_automatic_invitation_requires_actual_exchange_and_meeting_cue(tmp_path, monkeypatch):
    config, memory, _, _ = setup(tmp_path, monkeypatch)
    rows = [(20-i, "me" if i % 2 == 0 else "peer", f"这是关于画展和近况的第{i}条聊天") for i in range(10)]
    archive(memory, rows)
    packet = build_reply_context(config, tmp_path, memory, "周末有空吗？一起喝咖啡？")
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.report["skill"] == "proposing-a-date"
    assert result.report["stage"] == "proposing"


@pytest.mark.parametrize("phase,bad", [("quality", '{"score":"很好","issues":[]}'),
                                       ("quality", '{"score":NaN,"issues":[]}'),
                                       ("safety", '{"allowed":"true","reason":"ok"}'),
                                       ("safety", "暂时无法复审"), ("style", "")])
def test_missing_or_malformed_reviews_fail_closed(tmp_path, monkeypatch, phase, bad):
    router = Router()
    original = router.chat
    def chat(system, user, **kw):
        if f"WORKFLOW_PHASE: {phase}\n" in system:
            return bad
        return original(system, user, **kw)
    router.chat = chat
    config, memory, _, packet = setup(tmp_path, monkeypatch, router=router)
    with pytest.raises(HistoryError):
        generate_reply_with_strategy(config, tmp_path, packet)
    assert latest_report(memory, "对象甲")["status"] == "error"


@pytest.mark.parametrize("skill", ["opener", "banter", "flirting", "storytelling", "proposing-a-date",
                                   "number-exchange", "conversation-recovery", "re-engagement",
                                   "approaching", "rapport-building"])
def test_every_original_conversation_skill_can_be_selected_and_used(tmp_path, monkeypatch, skill):
    config, _, router, packet = setup(tmp_path, monkeypatch, settings=StrategySettings(skill=skill))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.report["skill"] == skill
    assert "selection" not in [p for p, _, _ in router.calls]
    assert any("## Skill: " + skill in system for p, system, _ in router.calls if p == "draft")


def test_proactive_opening_supports_no_incoming_but_respects_waiting_and_no_fake_sends(tmp_path, monkeypatch):
    config, memory, router, packet = setup(tmp_path, monkeypatch,
                                          settings=StrategySettings(action="proactive"), incoming="")
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert not result.blocked and result.report["skill"] == "opener"
    # Generating repeatedly is not sending and cannot itself trigger a cooldown.
    assert not generate_reply_with_strategy(config, tmp_path, packet).blocked
    archive(memory, [(1, "me", "周末去走走吗？")])
    before = len(router.calls)
    packet = build_reply_context(config, tmp_path, memory, "")
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.blocked and "节奏" in "".join(result.report["steps"])
    assert len(router.calls) == before


def test_reengagement_after_wait_uses_real_archive_and_original_skill(tmp_path, monkeypatch):
    config, memory, _, _ = setup(tmp_path, monkeypatch, settings=StrategySettings(action="proactive"), incoming="")
    archive(memory, [(110, "peer", "最近在看画展"), (100, "me", "那周末看看？")])
    result = generate_reply_with_strategy(config, tmp_path, build_reply_context(config, tmp_path, memory, ""))
    assert not result.blocked and result.report["skill"] == "re-engagement"
    again = generate_reply_with_strategy(config, tmp_path, build_reply_context(config, tmp_path, memory, ""))
    assert again.report["stage"] == result.report["stage"]


def test_cancel_stops_before_next_model_call_without_persisting_success(tmp_path, monkeypatch):
    config, memory, router, packet = setup(tmp_path, monkeypatch)
    with pytest.raises(HistoryError, match="停止"):
        generate_reply_with_strategy(config, tmp_path, packet, cancelled=lambda: True)
    assert router.calls == []
    assert latest_report(memory, "对象甲")["status"] == "stopped"


def test_latest_saved_strategy_overrides_old_preview_and_is_contact_scoped(tmp_path, monkeypatch):
    config, memory, _, packet = setup(tmp_path, monkeypatch)
    save_settings(memory, "对象甲", StrategySettings(skill="banter", intent="long-term"))
    assert load_settings(ChatMemory(memory.path), "对象甲").intent == "long-term"
    assert load_settings(memory, "对象乙") == StrategySettings()
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.report["skill"] == "banter"
    assert result.report["settings"]["intent"] == "long-term"
    with pytest.raises(HistoryError):
        save_settings(memory, "对象甲", StrategySettings(skill="profile-screening"))


def test_gui_strategy_editors_persist_per_peer_and_preview_real_settings(tmp_path, monkeypatch):
    from opendate.wechat_gui import WeChatWindow
    setup(tmp_path, monkeypatch)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No GUI display")
    root.withdraw()
    window = WeChatWindow(root, tmp_path)
    monkeypatch.setattr("opendate.wechat_gui.messagebox.showerror", lambda *a, **k: pytest.fail(str(a)))
    try:
        assert len(window.tabs.tabs()) == 7
        window.strategy_vars["skill"].set(SKILL_LABELS["flirting"])
        window.save_strategy()
        window.put(window.incoming, "你好呀")
        assert window.build_packet()["strategy_settings"]["skill"] == "flirting"
        window.target_var.set("对象乙")
        window.apply_contact()
        assert window.editing_strategy().skill == "auto"
        window.target_var.set("对象甲")
        window.apply_contact()
        assert window.editing_strategy().skill == "flirting"
    finally:
        window.closed = True
        root.destroy()
