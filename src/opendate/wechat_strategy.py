"""Compatibility settings and workflow hints for the evidence-led coach kernel.

All inputs are the previewable, contact-scoped packet. Drafts and copies never
become sent messages or advance a real-world milestone. No connector can send.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from .connectors.base import Match, Message
from .orchestrator.loop import build_situation
from .orchestrator.state import ConversationStage, compute_stage
from .wechat_memory import ChatMemory, HistoryError
from .wechat_judgment import INVITATION_LABELS, invitation_state, romantic_signal
from .coach.knowledge import MODES, SCENES
from .coach.models import render

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
    mode: str = "reply"
    scene: str = "auto"
    question: str = ""

    def validate(self):
        if (self.intent not in INTENTS or self.skill not in PRIMARY_SKILLS
                or self.action not in ACTIONS or self.stage not in STAGES
                or self.mode not in MODES or self.scene not in SCENES
                or not isinstance(self.question, str) or len(self.question) > 3000):
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
        return "生成后显示事实、推测、首选行动、反馈分支、观察窗口与停止条件。"
    if report.get("method") == "goutoujunshi":
        lines = [f"对象：{report['contact']} · {MODES[report['settings']['mode']]} · "
                 + SCENES[report['scene']],
                 "结果：" + ("已通过复审，仍需本人核对" if report["status"] == "passed" else "本次暂停"),
                 "", render(report.get("answer", {}), report.get("evidence_sources", {})),
                 "", "交流阶段：" + STAGES.get(report["stage"], report["stage"]),
                 "语气：" + report.get("tone", "未判断"),
                 "邀约：" + INVITATION_LABELS.get(report.get("invitation", {}).get("status"), "未判断"),
                 "依据范围：仅本次可见文字与本人保存的资料；参考文档不等于现实事实。",
                 "本次按需读取：" + "、".join(report.get("references", [])),
                 "记忆：" + report.get("memory_notice", "本次未自动保存精简档案。")]
        if report.get("notice"):
            lines.extend(("", report["notice"]))
        return "\n".join(lines)
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
                   (report.get("run_id", uuid.uuid4().hex), report["contact"], report["timestamp"],
                    json.dumps(report, ensure_ascii=False)))


def _run_pipeline(config, memory, packet, router_factory, progress, cancelled, report):
    from .coach.pipeline import run_coach
    return run_coach(config, memory, packet, router_factory, progress, cancelled, report)
