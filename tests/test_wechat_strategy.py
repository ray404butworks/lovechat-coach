import json
import tkinter as tk
from datetime import datetime, timedelta, timezone

import pytest

from coach_support import CoachRouter
from opendate.config import AppConfig
from opendate.wechat_desktop import build_reply_context, generate_reply_with_strategy
from opendate.wechat_memory import ChatMemory, ChatRecord, ConversationBrief, HistoryBatch, HistoryError
from opendate.wechat_profile import MyPersona, save_settings as save_profile
from opendate.wechat_strategy import StrategySettings, latest_report, load_settings, plan, report_text, save_settings


def setup(tmp_path, monkeypatch, *, router=None, settings=None, incoming="周末有什么计划？"):
    config = AppConfig(source="wechat", llm={"provider": "codex"},
                       wechat={"contact": "对象甲", "my_name": "本人", "allowed_contacts": ["对象甲"]})
    config = save_profile(tmp_path, config, MyPersona(voice="简短，以行嘞开头", avoid="不用感叹号"), "对象甲", "本人")
    memory = ChatMemory(tmp_path / config.data_dir / "wechat-memory.sqlite3")
    memory.save_brief("对象甲", ConversationBrief("我不吃香菜", "对方喜欢画展", "周日下午一起看展"))
    memory.save_brief("对象乙", ConversationBrief("乙的秘密", "乙的资料", "乙的目标"))
    save_settings(memory, "对象甲", settings or StrategySettings())
    router = router or CoachRouter()
    monkeypatch.setattr("opendate.wechat_desktop.make_router", lambda *_: router)
    return config, memory, router, build_reply_context(config, tmp_path, memory, incoming)


def archive(memory, rows):
    records = [ChatRecord((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(),
                         sender, text, str(i)) for i, (hours, sender, text) in enumerate(rows)]
    memory.import_batch("对象甲", HistoryBatch("private", "甲", records), "test")


def test_actual_coach_pipeline_keeps_owners_sources_and_one_contact(tmp_path, monkeypatch):
    config, memory, router, _ = setup(tmp_path, monkeypatch)
    archive(memory, [(2, "me", "好呀，周末出去走走"), (1, "peer", "我喜欢看展")])
    result = generate_reply_with_strategy(config, tmp_path, build_reply_context(config, tmp_path, memory, "周末有什么计划？"))
    assert [p for p, _, _ in router.calls] == ["coach", "review"]
    assert not result.blocked and result.text.startswith("行嘞")
    for _, system, actual in router.calls:
        assert "不可信" in system and "目标不是对方的意愿或承诺" in system
        assert actual["participants"]["me"]["confirmed_information"] == "我不吃香菜"
        assert actual["conversation_goal"]["desired_outcome"] == "周日下午一起看展"
        assert "乙的" not in json.dumps(actual, ensure_ascii=False)
        assert len(actual["reference_documents"]) == 3
    assert result.report["method"] == "goutoujunshi" and result.report["persona_samples"] == 1
    assert "停止条件" in report_text(result.report)
    assert latest_report(memory, "对象甲")["status"] == "passed" and latest_report(memory, "对象乙") == {}
    assert memory.stats("对象甲")[0] == 2
    with memory.connect() as db:
        state = json.loads(db.execute("SELECT data FROM strategy_state WHERE contact='对象甲'").fetchone()[0])
    assert state["sent_count"] == 0 and not state["outgoing_history"]


@pytest.mark.parametrize("incoming", ["别再联系我", "你这样让我不舒服", "我今年16岁"])
def test_boundary_preflight_stops_reply_before_model_request(tmp_path, monkeypatch, incoming):
    config, memory, router, packet = setup(tmp_path, monkeypatch, incoming=incoming,
                                          settings=StrategySettings(skill="flirting", stage="flirting"))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.blocked and not result.text and not router.calls
    assert latest_report(memory, "对象甲")["status"] == "blocked"


def test_review_rejection_really_rewrites_before_exposing_candidate(tmp_path, monkeypatch):
    config, _, router, packet = setup(tmp_path, monkeypatch, router=CoachRouter(reviews=[False, True]))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert not result.blocked
    assert [p for p, _, _ in router.calls] == ["coach", "review", "coach", "review"]
    assert router.calls[2][2]["revision_feedback"]


def test_exhausted_review_never_exposes_rejected_analysis_or_reply(tmp_path, monkeypatch):
    config, memory, _, packet = setup(tmp_path, monkeypatch, router=CoachRouter(reviews=[False] * 10))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.blocked and not result.text and result.report["answer"] == {}
    assert latest_report(memory, "对象甲")["status"] == "blocked"


@pytest.mark.parametrize("bad", ['{"allowed":"true","issues":[]}', "无法复审", '{"allowed":true,"issues":"ok"}'])
def test_malformed_review_is_not_success(tmp_path, monkeypatch, bad):
    router = CoachRouter()
    original = router.chat
    def malformed(system, user, **kw):
        return bad if "WORKFLOW_PHASE: review" in system else original(system, user, **kw)
    router.chat = malformed
    config, memory, _, packet = setup(tmp_path, monkeypatch, router=router)
    with pytest.raises(HistoryError):
        generate_reply_with_strategy(config, tmp_path, packet)
    assert latest_report(memory, "对象甲")["status"] == "error"


@pytest.mark.parametrize("incoming", ["哈哈", "好呀", "哈哈跟你聊天好开心", "😊", "哈哈你就吹吧"])
def test_friendly_tone_does_not_advance_stage(tmp_path, monkeypatch, incoming):
    config, _, _, packet = setup(tmp_path, monkeypatch, incoming=incoming)
    _, ctx, stage = plan(config, packet, StrategySettings())
    assert stage.value not in {"flirting", "proposing"} and not ctx.playful


def test_explicit_romantic_expression_is_distinct_from_friendly_tone(tmp_path, monkeypatch):
    config, _, _, packet = setup(tmp_path, monkeypatch, incoming="我对你有好感")
    _, ctx, stage = plan(config, packet, StrategySettings())
    assert stage.value == "flirting" and ctx.interest == "unknown"


@pytest.mark.parametrize("old_reply,status", [("去不了", "declined"), ("改天再说", "deferred"), ("取消吧", "cancelled")])
def test_closed_invitation_does_not_keep_proposing(tmp_path, monkeypatch, old_reply, status):
    config, memory, router, _ = setup(tmp_path, monkeypatch)
    archive(memory, [(5, "me", "一起去看电影？"), (4, "peer", old_reply), (3, "me", "今天降温了")])
    result = generate_reply_with_strategy(config, tmp_path, build_reply_context(config, tmp_path, memory, "好呀"))
    assert result.report["stage"] != "proposing" and result.report["invitation"]["status"] == status
    assert all(p["conversation_judgment"]["invitation"]["status"] == status for _, _, p in router.calls)


def test_current_proposal_requires_no_fabricated_rapport_threshold(tmp_path, monkeypatch):
    config, _, _, packet = setup(tmp_path, monkeypatch, incoming="周末一起喝咖啡？")
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.report["stage"] == "proposing" and result.report["scene"] == "invitation"


def test_proactive_mode_respects_waiting_without_counting_drafts(tmp_path, monkeypatch):
    config, memory, router, packet = setup(tmp_path, monkeypatch,
                                          settings=StrategySettings(action="proactive"), incoming="")
    assert not generate_reply_with_strategy(config, tmp_path, packet).blocked
    assert not generate_reply_with_strategy(config, tmp_path, packet).blocked
    archive(memory, [(1, "me", "周末去走走吗？")])
    before = len(router.calls)
    result = generate_reply_with_strategy(config, tmp_path, build_reply_context(config, tmp_path, memory, ""))
    assert result.blocked and len(router.calls) == before


def test_reusing_archived_input_does_not_renew_relative_date(tmp_path, monkeypatch):
    config, memory, _, _ = setup(tmp_path, monkeypatch)
    archive(memory, [(400, "peer", "明天一起吃饭？")])
    packet = build_reply_context(config, tmp_path, memory, "明天一起吃饭？")
    results = [generate_reply_with_strategy(config, tmp_path, packet) for _ in range(2)]
    assert all(r.report["invitation"]["status"] == "expired" for r in results)
    assert results[0].report["invitation"] == results[1].report["invitation"]


def test_cancel_stops_before_call_or_persisting_success(tmp_path, monkeypatch):
    config, memory, router, packet = setup(tmp_path, monkeypatch)
    with pytest.raises(HistoryError, match="停止"):
        generate_reply_with_strategy(config, tmp_path, packet, cancelled=lambda: True)
    assert not router.calls and latest_report(memory, "对象甲")["status"] == "stopped"


def test_latest_saved_mode_and_question_replace_stale_preview(tmp_path, monkeypatch):
    config, memory, router, packet = setup(tmp_path, monkeypatch)
    save_settings(memory, "对象甲", StrategySettings(mode="coach", scene="conflict", question="我想处理冷战"))
    result = generate_reply_with_strategy(config, tmp_path, packet)
    assert result.report["scene"] == "conflict" and router.calls[0][2]["user_question"] == "我想处理冷战"
    assert load_settings(memory, "对象乙") == StrategySettings()


def test_old_settings_migrate_without_losing_user_choices(tmp_path, monkeypatch):
    _, memory, _, _ = setup(tmp_path, monkeypatch)
    with memory.connect() as db:
        db.execute("UPDATE strategy_settings SET data=? WHERE contact=?", (
            json.dumps({"intent": "long-term", "skill": "banter", "action": "proactive", "stage": "rapport"}), "对象甲"))
    migrated = load_settings(memory, "对象甲")
    assert migrated.skill == "banter" and migrated.intent == "long-term" and migrated.mode == "reply"


def test_gui_mode_question_and_advice_stay_per_contact(tmp_path, monkeypatch):
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
        window.strategy_vars["mode"].set("分析局面")
        window.strategy_vars["scene"].set("冲突与修复")
        window.put(window.coach_question, "我想处理冷战")
        window.save_strategy()
        assert window.build_packet()["strategy_settings"]["question"] == "我想处理冷战"
        window.target_var.set("对象乙")
        window.apply_contact()
        assert window.editing_strategy().mode == "reply"
        assert "对象甲" not in window.coach_advice.get("1.0", "end")
        window.target_var.set("对象甲")
        window.apply_contact()
        assert window.editing_strategy().question == "我想处理冷战"
    finally:
        window.closed = True
        root.destroy()
