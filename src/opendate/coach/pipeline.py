"""Desktop coaching workflow. Private evidence never enters the knowledge library."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import uuid

from ..orchestrator.safety import SafetyDecision, SafetyGuard
from ..orchestrator.state import ConversationStage, ConversationState
from ..wechat_memory import HistoryError
from . import engine
from .knowledge import references, route
from .memory import CoachMemory


def run_coach(config, memory, packet, router_factory, progress, cancelled, report):
    from ..wechat_strategy import (
        StrategyResult, StrategySettings, _messages, _save_report, _tables, judgment_details, plan,
    )
    settings = StrategySettings(**packet["strategy_settings"])
    settings.validate()
    _tables(memory)
    match, ctx, stage = plan(config, packet, settings)
    question = packet.get("user_question", settings.question)
    scene = route(question, packet.get("message_to_reply", ""), settings.scene, mode=settings.mode)
    report.update(contact=packet["contact"], method="goutoujunshi", settings=asdict(settings),
                  run_id=uuid.uuid4().hex,
                  scene=scene, stage=stage.value, status="blocked", steps=[], reason="",
                  modifiers=[], quality=None, skill="auto", answer={},
                  timestamp=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
    report.update(judgment_details(match, ctx))
    if settings.stage != "auto":
        report["stage_reason"] = "交流阶段由本人指定；不构成双方关系确认。"

    def check():
        if cancelled():
            raise HistoryError("已停止后续军师步骤；未保存本轮档案")

    def step(message):
        check()
        report["steps"].append(message)
        progress(message)

    def finish(text="", notice=""):
        check()
        report["notice"] = notice
        # Drafts never become messages or real-world relationship milestones.
        state = ConversationState(match_id=packet["contact"], stage=stage, message_count=len(match.messages))
        _save_report(memory, report)
        with memory.connect() as db:
            db.execute("INSERT OR REPLACE INTO strategy_state VALUES (?,?)",
                       (packet["contact"], state.model_dump_json()))
        return StrategyResult(text, report)

    guard = SafetyGuard(config.safety)
    boundary = guard.check_message("", ctx)
    if stage == ConversationStage.CLOSED and settings.mode in {"reply", "rehearsal"}:
        return finish(notice="此对象已设为停止推进；可切换为分析局面或退出复盘。")
    if stage == ConversationStage.CLOSED:
        boundary = SafetyDecision(allowed=False, severity="hard", reasons=["本人已停止推进"], category="closed")
    # A refusal must prevent outgoing contact, while internal support remains useful.
    if boundary.blocked and settings.mode in {"reply", "rehearsal"}:
        return finish(notice="对方表达拒绝、不适或触发边界检查。可用“分析局面”获得退出和自我照顾建议。")
    if settings.action == "proactive" and settings.mode == "reply":
        trailing = 0
        for message in reversed(match.messages):
            if not message.from_me:
                break
            trailing += 1
        mine = [m for m in _messages(packet, current=False) if m.from_me]
        now = datetime.now(timezone.utc)
        last_sent = mine[-1].sent_at if mine else None
        cooldown = max(0.0, config.pacing.cooldown_hours - (now - last_sent).total_seconds() / 3600) if last_sent else 0.0
        daily = sum(1 for m in mine if m.sent_at and 0 <= (now - m.sent_at).total_seconds() < 86400)
        pacing = guard.check_pacing(cooldown_remaining=cooldown,
                                   followups_without_reply=trailing,
                                   daily_budget_left=max(0, config.pacing.max_daily_actions - daily))
        if pacing.blocked or (match.last_message and match.last_message.from_me
                              and config.pacing.never_double_text
                              and (ctx.days_since_last or 0) < config.pacing.reengage_after_days):
            return finish(notice="可见记录显示仍在等待回应或追发过多，先不追加消息。")
    step("按当前问题分场景，只读取必要的关系知识")
    docs = references(scene)
    report["references"] = [item["path"] for item in docs]
    report["persona_samples"] = sum(m.from_me for m in match.messages)
    packet = {**packet, "user_question": question,
              "conversation_judgment": {k: report[k] for k in ("tone", "invitation", "stage_reason")},
              "contact_boundary": {"outgoing_allowed": not boundary.blocked,
                                   "reason": boundary.category},
              "legacy_skill_preference": settings.skill,
              "legacy_skill_notice": "旧技能设置仅作表达偏好，不能覆盖事实、场景或边界。"}
    prepared = engine.prepare(packet, docs)
    report["evidence_sources"] = prepared["evidence_sources"]
    router = router_factory()
    feedback = ""
    for attempt in range(config.quality.max_regenerations + 1):
        step("情绪承接、事实与推测分离，编排一个主动作" if not attempt else "依据复审意见修正本轮方案")
        try:
            answer = engine.call(router, "coach", prepared, feedback=feedback, check=check)
        except HistoryError as exc:
            if cancelled():
                raise
            feedback = str(exc)
            continue
        # The local gate and model review both apply; neither can undo a refusal.
        outgoing = guard.check_message(answer.reply, ctx) if answer.reply else None
        if (answer.reply and (boundary.blocked or (outgoing and outgoing.blocked))):
            feedback = "不要生成越界的对外回复。对方已拒绝或不适时reply为空，只提供内部支持与停止条件。"
            continue
        step("复审原文归属、单主动作、反馈分支、停止条件与安全")
        review = engine.call(router, "review", prepared, candidate=answer.model_dump(), check=check)
        if not review.allowed or review.issues:
            feedback = "；".join(review.issues) or "未通过独立复审，请修正方案"
            continue
        check()
        if settings.mode == "rehearsal":
            answer.reply = "【演练模拟】" + answer.reply
        report["answer"] = answer.model_dump()
        report["status"] = "passed"
        report["reason"] = answer.recommendation
        report["memory_notice"] = "本次未自动保存精简档案；可在军师方案中核对候选。"
        # Model suggestions are intentionally a pending draft. A user's separate
        # confirmation applies only eligible event/hypothesis entries through the
        # provenance-checked memory API; producing an answer is never consent.
        report["memory_candidates"] = []
        if settings.mode != "rehearsal":
            for item in answer.memory_suggestions:
                source = prepared["evidence_sources"][item.source_id]
                if source["kind"] in {"hypothesis", "event"} or item.source_id.startswith("memory:"):
                    continue  # recalled material must not self-reinforce as a new event
                report["memory_candidates"].append({
                    "scope": item.scope, "field": item.field, "value": item.value,
                    "source_type": "assistant_inference" if item.scope == "hypothesis" else "chat",
                    "source_ref": f"run:{report['run_id']}:{item.source_id}", "occurred_at": source.get("time", ""),
                    "confidence": item.confidence,
                })
        step("方案已通过复审，等待本人核对；没有发送消息或推进真实状态")
        return finish(answer.reply)
    return finish(notice="本轮方案未通过结构、证据或安全复审，已隐藏；请补充关键信息或重新生成。")


def save_candidates(memory, contact, report):
    """Called only from the explicit GUI save action after a visible preview."""
    if report.get("contact") != contact or report.get("status") != "passed":
        raise HistoryError("候选档案与当前对象不一致或方案尚未通过复审")
    candidates = report.get("memory_candidates", [])
    if not candidates:
        raise HistoryError("本轮没有待保存的精简档案")
    store = CoachMemory(memory.path.with_name("coach-memory.sqlite3"))
    return store.apply(contact, candidates)
