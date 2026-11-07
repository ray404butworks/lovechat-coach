"""OpenDate's actual skill, stage, voice, critique and guard pipeline for WeChat.

All inputs are the previewable, contact-scoped packet. Drafts and copies never
become sent messages or advance a real-world milestone. No connector can send.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from .connectors.base import Match, Message
from .llm.router import extract_json
from .orchestrator.loop import build_situation
from .orchestrator.quality import critique_message
from .orchestrator.safety import SafetyGuard
from .orchestrator.state import ConversationStage, ConversationState, compute_stage, normalize_text
from .persona.analyze import analyze_persona
from .persona.ingest import IngestResult
from .persona.style import StyleTransfer
from .skills.engine import SkillsEngine, SkillSelection
from .wechat_memory import ChatMemory, HistoryError, REPLY_SYSTEM
from .wechat_judgment import INVITATION_LABELS, invitation_state, romantic_signal

SKILL_LABELS = {
    "auto": "自动选择", "opener": "开场破冰", "approaching": "初步接近",
    "rapport-building": "建立熟悉感", "banter": "轻松调侃", "flirting": "自然暧昧",
    "storytelling": "分享故事", "proposing-a-date": "提出邀约",
    "number-exchange": "交换联系方式", "conversation-recovery": "修复冷场",
    "re-engagement": "重新开启聊天", "relationship-intent-matching": "关系意图匹配",
    "persona-style-transfer": "本人口吻改写", "consent-and-safety": "意愿与安全边界",
    "profile-screening": "对象资料筛选（已有微信对象不自动筛选）",
}
MODIFIERS = {"relationship-intent-matching", "persona-style-transfer", "consent-and-safety"}
PRIMARY_SKILLS = tuple(k for k in SKILL_LABELS if k not in MODIFIERS | {"profile-screening"})
INTENTS = {"dating": "约会发展", "long-term": "长期关系", "casual": "轻松相处"}
ACTIONS = {"reply": "回复来信", "proactive": "主动开启 / 跟进"}
STAGES = {"auto": "自动判断", "matched": "尚未开场", "opened": "初步交流",
          "rapport": "建立熟悉感", "flirting": "互动升温", "proposing": "讨论见面",
          "number_exchanged": "已交换联系方式", "stalled": "等待回应",
          "ghosted": "较长时间未回应", "recovering": "修复与恢复", "closed": "停止推进"}


@dataclass(frozen=True)
class StrategySettings:
    intent: str = "dating"
    skill: str = "auto"
    action: str = "reply"
    stage: str = "auto"

    def validate(self):
        if (self.intent not in INTENTS or self.skill not in PRIMARY_SKILLS
                or self.action not in ACTIONS or self.stage not in STAGES):
            raise HistoryError("聊天策略设置无效，请重新选择并保存。")


def _tables(memory):
    with memory.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS strategy_settings(contact TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strategy_state(contact TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strategy_runs(
                id TEXT PRIMARY KEY, contact TEXT NOT NULL, timestamp TEXT NOT NULL, report TEXT NOT NULL);
        """)


def load_settings(memory: ChatMemory, contact: str) -> StrategySettings:
    _tables(memory)
    with memory.connect() as db:
        row = db.execute("SELECT data FROM strategy_settings WHERE contact=?", (contact,)).fetchone()
    try:
        settings = StrategySettings(**json.loads(row[0])) if row else StrategySettings()
        settings.validate()
        return settings
    except (TypeError, ValueError) as exc:
        raise HistoryError("无法读取该对象的聊天策略，请重新保存设置。") from exc


def save_settings(memory: ChatMemory, contact: str, settings: StrategySettings):
    settings.validate()
    _tables(memory)
    with memory.connect() as db:
        db.execute("INSERT OR REPLACE INTO strategy_settings VALUES (?, ?)",
                   (contact, json.dumps(asdict(settings), ensure_ascii=False)))


@dataclass
class StrategyResult:
    text: str = ""
    report: dict = field(default_factory=dict)

    @property
    def blocked(self):
        return self.report.get("status") != "passed"


def report_text(report: dict) -> str:
    if not report:
        return "生成后显示实际采用的技能、判断依据、改写和检查结果。"
    lines = [f"对象：{report['contact']}", "时间：" + report.get("timestamp", ""),
             f"结果：{'通过，可核对后使用' if report['status'] == 'passed' else '暂停生成'}",
             f"关系方向：{INTENTS[report['settings']['intent']]}",
             f"任务：{ACTIONS[report['settings']['action']]}",
             f"交流阶段：{STAGES.get(report['stage'], report['stage'])}"
             + ("（规则推测，可手动指定）" if report['settings']['stage'] == 'auto' else "（你手动指定）"),
             f"主要技能：{SKILL_LABELS.get(report.get('skill'), '未选择')}",
             "叠加技能：" + "、".join(SKILL_LABELS[s] for s in report.get("modifiers", [])),
             "选择依据：" + report.get("reason", ""),
             "可见来信语气：" + report.get("tone", "未判断"),
             "邀约状态：" + INVITATION_LABELS.get(report.get("invitation", {}).get("status"), "未判断"),
             "阶段依据：" + report.get("stage_reason", "旧报告未记录"),
             "资料范围：仅本次预览中的文字与已保存设定；没有读取未接通的微信历史。",
             f"本人口吻样本：{report.get('persona_samples', 0)} 条本次上下文中的真实我方消息",
             "\n实际执行："]
    lines.extend(f"• {step}" for step in report.get("steps", []))
    invite = report.get("invitation", {})
    if invite.get("target_date"):
        lines.append("邀约日期：" + invite["target_date"] + "（按消息日期解析，请核对）")
    for evidence in invite.get("evidence", []):
        who = "我" if evidence["sender"] == "me" else "对方"
        lines.append(f"邀约依据：{evidence['timestamp']} {who}：{evidence['text']}")
    if report.get("quality") is not None:
        lines.append(f"质量评分：{report['quality']:.2f}（启发式和模型复审，不是关系成功率）")
    if report.get("notice"):
        lines.append("\n说明：" + report["notice"])
    lines.append("\n生成与复制均不代表已发送，不会计入追发次数或已达成邀约。")
    return "\n".join(lines)


def latest_report(memory: ChatMemory, contact: str) -> dict:
    _tables(memory)
    with memory.connect() as db:
        row = db.execute("SELECT report FROM strategy_runs WHERE contact=? "
                         "ORDER BY timestamp DESC, rowid DESC LIMIT 1", (contact,)).fetchone()
    return json.loads(row[0]) if row else {}


def _messages(packet, *, current=True):
    rows = packet.get("recent_messages", [])
    messages = [Message(id=r["key"], match_id=packet["contact"],
                        sender="me" if r["sender"] == "me" else "them", text=r["text"],
                        sent_at=datetime.fromisoformat(r["timestamp"])) for r in rows]
    incoming = packet.get("message_to_reply", "").strip()
    # A duplicate of the latest archived peer message retains its real time.
    # Otherwise every regeneration would renew relative dates and expiry.
    # Identical text elsewhere in history is not enough to deduplicate input.
    if current and incoming:
        if not (messages and not messages[-1].from_me and messages[-1].text.strip() == incoming):
            messages.append(Message(id="current-input", match_id=packet["contact"], sender="them",
                                    text=incoming, sent_at=datetime.now(timezone.utc)))
    return messages


def plan(config, packet, settings, previous=None):
    match = Match(id=packet["contact"], messages=_messages(packet, current=settings.action == "reply"))
    ctx = build_situation(match, reengage_after_days=config.pacing.reengage_after_days)
    text = ctx.their_last_text or ""
    # Original English signals supplemented for actual Chinese WeChat dialogue.
    banter = ctx.banter or bool(re.search(r"你就吹吧|算你赢|不服来战|嘴硬|就你皮|逗你", text))
    positive = bool(re.search(r"哈哈|嘿嘿|好呀|好啊|期待|开心|有意思|可以呀|喜欢和你聊", text))
    negative = bool(re.search(r"不太想|没心情|先不聊|改天再说|有点难过|今天很累|不开心", text))
    # A short answer or an emoji alone is not consent, rejection or flirting evidence.
    disinterest = bool(re.search(r"不想继续|不合适|没兴趣|不想发展|别再追|不想聊了", text))
    # Tone is independent of romantic intent. Neither laughing nor banter is
    # sufficient evidence for the flirting stage or automatic flirting skill.
    playful = romantic_signal(text)
    rapport = ctx.rapport_score
    invitation = invitation_state(match.messages)
    ctx = replace(ctx, banter=banter, playful=playful, disinterest=disinterest,
                  sentiment="negative" if negative else "positive" if positive else "neutral",
                  ready_for_date=invitation.active and (invitation.proposer == "them"
                                                       or invitation.status == "accepted"),
                  interest="unknown", rapport_score=rapport)
    stage = compute_stage(ctx, previous=previous, proposed_date=invitation.active)
    if stage == ConversationStage.FLIRTING and not playful:
        stage = ConversationStage.RAPPORT
    if settings.stage != "auto":
        stage = ConversationStage(settings.stage)
    ctx = replace(ctx, stage=stage.value)
    return match, ctx, stage


def judgment_details(match, ctx):
    invitation = invitation_state(match.messages)
    tone = ("情绪低落或暂不想聊" if ctx.sentiment == "negative" else
            "轻松调侃" if ctx.banter else "轻松积极" if ctx.sentiment == "positive" else "普通交流")
    if invitation.active:
        reason = "可见真实消息中有仍有效的邀约；接受、改期与恋爱关系分别判断。"
    elif ctx.playful:
        reason = "对方本次有明确好感表达；不代表双方已确认恋爱关系。"
    else:
        reason = "按可见往返和等待状态判断；普通开心、表情与调侃不作为暧昧依据。"
    return {"tone": tone, "invitation": asdict(invitation), "stage_reason": reason}


class PhaseRouter:
    """Same locked Codex backend, with scoped multi-pass calls explicitly enabled.

    Original components' optional review failures are surfaced, never called a
    successful review. Full packet accompanies each stage to preserve ownership.
    """
    is_stub = False
    prefers_single_call = False

    def __init__(self, router, packet, phase, check):
        self.router, self.packet, self.phase, self.check = router, packet, phase, check
        self.failed = False

    def chat(self, system, user, **kwargs):
        self.check()
        instruction = (REPLY_SYSTEM + "\n当前执行约会回复流程的一个独立步骤。"
                       "本步骤要求 JSON 时只输出 JSON，其余情况下只输出草稿正文。"
                       "下列约会技能是行动建议；本人已保存的人设、双方事实、最终目标和对方边界优先。"
                       "当前任务为主动跟进且没有来信时，不得假装对方刚发了消息。"
                       "不得把调侃、回复字数或规则阶段推测当成恋爱意愿；不得虚构故事、经历或约会进展。"
                       "conversation_judgment 只描述可见消息中的语气与邀约状态，不是双方关系的确认。"
                       "待回应的我方邀约不要重复催约；被拒绝、暂缓或取消的安排不要擅自重启。"
                       "改期须等另一方确认；接受邀约只表示愿意见面，回复应核对现有安排，不重复邀约。"
                       "过期安排是否完成未知，不得当成已见面；普通开心和调侃不要强行转成暧昧。"
                       "改写和复审必须保留双方信息归属及用户明确禁用表达。\n"
                       f"WORKFLOW_PHASE: {self.phase}\n" + system)
        if self.phase == "quality":
            instruction += ("\n复审时还要对照完整上下文检查双方事实归属、禁用表达、目标与草稿是否一致。"
                            "虚构经历、颠倒偏好、强行推进或违反明确风格要求时，score 必须低于 0.5 并给出问题。")
        payload = {**self.packet, "workflow_request": user}
        try:
            result = self.router.chat(instruction, json.dumps(payload, ensure_ascii=False), **kwargs)
            if self.phase in {"quality", "safety", "selection"}:
                data = extract_json(result)
                valid = isinstance(data, dict)
                if valid and self.phase == "quality":
                    score = data.get("score")
                    valid = (type(score) in (float, int) and math.isfinite(score) and 0 <= score <= 1
                             and isinstance(data.get("issues"), list)
                             and all(isinstance(i, str) for i in data["issues"]))
                elif valid and self.phase == "safety":
                    valid = type(data.get("allowed")) is bool and isinstance(data.get("reason"), str)
                elif valid:
                    valid = data.get("skill") in PRIMARY_SKILLS[1:]
                if not valid:
                    raise HistoryError("模型复审返回内容无效，本次未标记为通过，请重新生成。")
            elif not isinstance(result, str) or not result.strip():
                raise HistoryError("模型没有返回有效草稿，请重新生成。")
            self.check()
            return result
        except Exception:
            self.failed = True
            raise

    def chat_json(self, system, user, **kwargs):
        return extract_json(self.chat(system, user, **kwargs))

    def require_success(self):
        if self.failed:
            raise HistoryError("聊天策略流程有一步未完成，本次未标记为通过，请重新生成。")


def run_pipeline(config, memory: ChatMemory, packet: dict, router_factory: Callable,
                 progress=lambda _: None, cancelled=lambda: False) -> StrategyResult:
    report = {}
    try:
        return _run_pipeline(config, memory, packet, router_factory, progress, cancelled, report)
    except Exception:
        if report:
            report.update(status="stopped" if cancelled() else "error",
                          notice="流程已停止，未产生通过全部检查的回复。" if cancelled()
                          else "流程有一步未完成，未产生通过全部检查的回复；可重新生成。")
            _save_report(memory, report)
        raise


def _save_report(memory, report):
    with memory.connect() as db:
        db.execute("INSERT INTO strategy_runs VALUES (?, ?, ?, ?)",
                   (uuid.uuid4().hex, report["contact"], report["timestamp"],
                    json.dumps(report, ensure_ascii=False)))


def _run_pipeline(config, memory, packet, router_factory, progress, cancelled, report):
    settings = StrategySettings(**packet["strategy_settings"])
    settings.validate()
    _tables(memory)
    with memory.connect() as db:
        row = db.execute("SELECT data FROM strategy_state WHERE contact=?", (packet["contact"],)).fetchone()
    state = ConversationState.model_validate_json(row[0]) if row else ConversationState(match_id=packet["contact"])
    # Repeated generations are not actual re-engagement. Recompute milestones
    # from observed messages rather than advancing on the previous draft run.
    match, ctx, stage = plan(config, packet, settings)
    state.stage, state.message_count = stage, len(match.messages)
    # Only archived real self messages feed repetition / pacing; never drafts.
    state.outgoing_history = [normalize_text(m.text) for m in _messages(packet, current=False) if m.from_me][-10:]
    report.update(contact=packet["contact"], settings=asdict(settings), stage=stage.value,
                  status="blocked", steps=[], reason="", modifiers=[], quality=None,
                  timestamp=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
    report.update(judgment_details(match, ctx))
    if settings.stage != "auto":
        report["stage_reason"] = "交流阶段由你手动指定；语气与邀约仍按真实消息单独判断。"
    # Selection, drafting and every review see the same evidence and lifecycle.
    # This does not persist predictions as facts or add them to chat history.
    packet = {**packet, "conversation_judgment": {
        **{k: report[k] for k in ("tone", "invitation", "stage_reason")},
        "relationship": "未通过本次自动规则确认；以双方已确认事实为准",
        "scope": "仅本次预览中的真实消息；目标和生成草稿不计入邀约",
    }}

    def check():
        if cancelled():
            raise HistoryError("已停止后续策略步骤；当前模型调用结束后不再继续。")

    def step(text):
        check()
        report["steps"].append(text)
        progress(text)

    def finish(text="", notice=""):
        check()
        report["notice"] = notice
        state.last_skill = report.get("skill")
        state.last_decision_reason = report["reason"]
        state.touch()
        with memory.connect() as db:
            db.execute("INSERT OR REPLACE INTO strategy_state VALUES (?, ?)",
                       (packet["contact"], state.model_dump_json()))
        _save_report(memory, report)
        return StrategyResult(text, report)

    step("读取已保存目标与双方资料，完成交流阶段判断")
    guard = SafetyGuard(config.safety)
    preflight = guard.check_message("", ctx)
    if preflight.blocked or stage == ConversationStage.CLOSED:
        step("对方边界检查：暂停推进")
        return finish(notice="对方表达拒绝、不适或触发边界检查，建议停止推进。" if preflight.blocked
                      else "你已将此对象设为停止推进。")
    if settings.action == "proactive":
        mine = [m for m in match.messages if m.from_me]
        last = match.last_message
        trailing = 0
        for msg in reversed(match.messages):
            if not msg.from_me:
                break
            trailing += 1
        state.last_sent_at = mine[-1].sent_at if mine else None
        # Local archive can be incomplete. An empty archive has no proven sent action.
        daily = sum(1 for m in mine if m.sent_at and
                    (datetime.now(timezone.utc) - m.sent_at).total_seconds() < 86400)
        pace = guard.check_pacing(
            cooldown_remaining=state.cooldown_remaining(config.pacing.cooldown_hours),
            followups_without_reply=trailing,
            daily_budget_left=max(0, config.pacing.max_daily_actions - daily))
        if pace.blocked or (last and last.from_me and config.pacing.never_double_text
                            and (ctx.days_since_last or 0) < config.pacing.reengage_after_days):
            step("主动跟进节奏检查：暂不追发")
            return finish(notice="已保存记录显示仍在等待回应、冷却中或追发过多，暂不建议再次主动联系。")
        step("主动跟进节奏检查通过（仅按已保存记录计算）")
    else:
        step("回复来信模式：不把草稿生成次数计入已发送或追发次数")
    engine = SkillsEngine()
    step(f"加载原版 {len(engine)} 个技能，开始选择本次约会策略")
    router = router_factory()
    selection_router = PhaseRouter(router, packet, "selection", check)
    selection = engine.select(ctx, router=selection_router if settings.skill == "auto" else None)
    selection_router.require_success()
    if settings.skill != "auto":
        selection = SkillSelection(engine.get(settings.skill), engine.modifiers(), "由用户手动指定技能。")
    elif ((selection.primary.name == "flirting" and not ctx.playful)
          or (selection.primary.name in {"proposing-a-date", "number-exchange"}
              and not ctx.ready_for_date)):
        selection = SkillSelection(engine.get("rapport-building"), engine.modifiers(),
                                   "没有足够的当前好感表达或有效见面线索，先自然回应。")
    # Existing WeChat contact already has a messaging channel. No gratuitous number request.
    if settings.skill == "auto" and selection.primary.name == "number-exchange":
        selection = SkillSelection(engine.get("proposing-a-date"), engine.modifiers(),
                                   "已在微信交流，优先讨论见面安排，不重复索取联系方式。")
    observations = ["当前有新来信" if settings.action == "reply" else "由你主动开启或跟进"]
    if ctx.banter:
        observations.append("来信有调侃表达")
    elif ctx.playful:
        observations.append("来信语气轻松积极")
    if ctx.sentiment == "negative":
        observations.append("来信表达低落或暂不想聊")
    if ctx.their_asked_question:
        observations.append("对方提出了问题")
    if ctx.ready_for_date:
        observations.append("可见消息中有对方提出或双方接受的有效见面安排")
    observations.append(f"本次可见对话 {ctx.num_messages} 条")
    if selection.decided_by == "llm-tiebreak":
        observations.append("模糊情况由模型在候选技能中择优")
    report.update(skill=selection.primary.name, modifiers=[s.name for s in selection.modifiers],
                  confidence=selection.confidence,
                  engine_reason=selection.reason, selected_by=selection.decided_by,
                  reason=("；".join(observations) + "。" if settings.skill == "auto"
                          else "由你手动指定；拒绝与边界检查仍优先。"))
    step("选用：" + SKILL_LABELS[selection.primary.name] + "；已叠加意图匹配、本人口吻和边界技能")
    own = [m.text for m in match.messages if m.from_me]
    custom = packet["my_persona"]
    persona = analyze_persona(IngestResult(chat_messages=own), voice=custom.get("voice", ""))
    persona.tone = custom.get("voice") or persona.tone
    persona.summary = custom.get("personality") or persona.summary
    # Explicit examples are voice examples, never asserted to be sent messages.
    guidance = selection.combined_playbook() + "\n用户手动设定优先：\n" + json.dumps(custom, ensure_ascii=False)
    report["persona_samples"] = len(own)
    packet = {**packet, "strategy_plan": {"stage": stage.value, "skill": selection.primary.name,
                                          "intent": settings.intent, "looking_for": settings.intent,
                                          "action": settings.action}}
    draft_router = PhaseRouter(router, packet, "draft", check)
    style_router = PhaseRouter(router, packet, "style", check)
    quality_router = PhaseRouter(router, packet, "quality", check)
    safety_router = PhaseRouter(router, packet, "safety", check)
    # Full reviews deliberately run for this UI even though Codex's raw router
    # normally requests one call. No backend permission or tool setting changes.
    feedback = ""
    for attempt in range(config.quality.max_regenerations + 1):
        step("生成初稿" if not attempt else f"按复审意见重新生成（第 {attempt} 次）")
        draft = draft_router.chat(guidance, "结合所选技能和当前明确事实生成一条约会聊天回复。\n" + feedback,
                                  max_tokens=600)
        # Run deterministic safety before another model handles a bad draft.
        local = guard.check_message(draft, ctx)
        if local.blocked and local.severity == "hard":
            step("草稿边界检查：未通过")
            return finish(notice="草稿触发内容边界检查，已隐藏；请调整当前信息后重试。")
        step("使用原版本人口吻改写模块")
        draft = StyleTransfer(style_router, guidance).transfer(draft, persona)
        style_router.require_success()
        step("质量复审：检查重复、贴合来信、语气与事实归属")
        quality = critique_message(draft, ctx=ctx, persona=persona, state=state,
                                   skill=selection.primary.name, reference_text=ctx.their_last_text or "",
                                   min_score=config.quality.min_score, router=quality_router)
        quality_router.require_success()
        report["quality"] = quality.score
        step("使用原版安全模块复查最终草稿")
        decision = SafetyGuard(config.safety, safety_router, guidance).check_message(draft, ctx)
        safety_router.require_success()
        # If deterministic quality failed, the original critic skips its model
        # pass. This is still a failed review; regeneration handles it below.
        if quality.passed and decision.allowed:
            report["status"] = "passed"
            step("质量与安全检查通过，等待人工核对")
            return finish(draft)
        if decision.blocked and decision.severity == "hard":
            return finish(notice="最终草稿触发内容边界检查，已隐藏。")
        feedback = "请修正：" + quality.feedback + "；" + "；".join(decision.reasons)
    step("重写次数用尽，仍未通过复审")
    return finish(notice="本次草稿未通过质量或边界复审，已隐藏；可调整资料、技能后重新生成。")
