"""Strict final-answer contracts; references must resolve to visible evidence."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..wechat_memory import HistoryError


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Fact(Contract):
    source_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=1000)


class Hypothesis(Contract):
    explanation: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=5)
    alternative: str = Field(min_length=1, max_length=600)


class Branches(Contract):
    positive: str = Field(min_length=1, max_length=600)
    ambiguous: str = Field(min_length=1, max_length=600)
    negative: str = Field(min_length=1, max_length=600)
    no_response: str = Field(min_length=1, max_length=600)


class MemorySuggestion(Fact):
    scope: Literal["event", "hypothesis"]
    field: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=200)
    confidence: Literal["low", "medium", "high"]


class CoachAnswer(Contract):
    emotion: str = Field(max_length=800)
    facts: list[Fact] = Field(max_length=8)
    hypotheses: list[Hypothesis] = Field(max_length=4)
    unknowns: list[str] = Field(max_length=5)
    recommendation: str = Field(min_length=1, max_length=1000)
    reasons: list[str] = Field(min_length=1, max_length=4)
    main_action: Literal["承接", "降压", "调侃", "轻推", "约见", "澄清", "收线", "安全支持"]
    reply: str = Field(max_length=1600)
    timing: str = Field(min_length=1, max_length=500)
    tradeoff: str = Field(min_length=1, max_length=500)
    next_action: str = Field(min_length=1, max_length=700)
    observation_window: str = Field(min_length=1, max_length=500)
    stop_condition: str = Field(min_length=1, max_length=500)
    branches: Branches
    questions: list[str] = Field(max_length=3)
    memory_suggestions: list[MemorySuggestion] = Field(max_length=3)


class Review(Contract):
    allowed: bool
    issues: list[str] = Field(max_length=10)


def evidence_sources(packet: dict) -> dict[str, dict]:
    result = {}
    for group in ("related_older_messages", "recent_messages"):
        for row in packet.get(group, []):
            result["message:" + row["key"]] = {"text": row["text"], "speaker": row["sender"],
                                                "time": row["timestamp"], "kind": "chat"}
    for side in ("me", "peer"):
        text = packet.get("participants", {}).get(side, {}).get("confirmed_information", "")
        if text:
            result["profile:" + side] = {"text": text, "speaker": side, "kind": "user_confirmed"}
    if packet.get("message_to_reply"):
        result["current"] = {"text": packet["message_to_reply"], "speaker": "peer", "kind": "current_input"}
    if packet.get("user_question"):
        result["question"] = {"text": packet["user_question"], "speaker": "me", "kind": "user_report"}
    if packet.get("user_confirmed_memory"):
        result["legacy_notes"] = {"text": packet["user_confirmed_memory"], "speaker": "unclassified",
                                   "kind": "legacy_user_confirmed"}
    for row in packet.get("coach_memory", []):
        result[f"memory:{row['scope']}:{row['field']}"] = {
            "text": row["value"], "speaker": "unclassified", "kind": row["scope"],
            "source_ref": row["source_ref"]}
    return result


def validate_evidence(answer: CoachAnswer, sources: dict):
    for fact in [*answer.facts, *answer.memory_suggestions]:
        source = sources.get(fact.source_id)
        if not source or fact.quote not in source["text"]:
            raise HistoryError("军师引用了不存在的来源或原文，本次不展示，请重试")
        if fact in answer.facts and source["kind"] == "hypothesis":
            raise HistoryError("旧假设不能当作新的已知事实")
    for hypothesis in answer.hypotheses:
        if any(key not in sources for key in hypothesis.evidence_ids):
            raise HistoryError("军师推测缺少可追溯依据")
    for text in [*answer.unknowns, *answer.reasons, *answer.questions]:
        if not text.strip() or len(text) > 700:
            raise HistoryError("军师返回了空白或超长的分析项")


def render(answer: dict, sources: dict | None = None) -> str:
    if not answer:
        return "生成后显示事实、推测、首选行动、反馈分支与停止条件。"
    lines = [answer["recommendation"]]
    if answer["emotion"]:
        lines.extend(("", answer["emotion"]))
    lines.extend(("", "判断理由：", *["• " + r for r in answer["reasons"]]))
    lines.extend(("", "已知事实（原文）："))
    for fact in answer["facts"]:
        source = (sources or {}).get(fact["source_id"], {})
        lines.append(f"• [{fact['source_id']}] {source.get('speaker', '')}：{fact['quote']}")
    for item in answer["hypotheses"]:
        lines.extend(("", "暂定解释：" + item["explanation"], "另一种可能：" + item["alternative"]))
    if answer["unknowns"]:
        lines.extend(("", "仍未知：" + "；".join(answer["unknowns"])))
    lines.extend(("", "本轮主动作：" + answer["main_action"], "发送时机：" + answer["timing"],
                  "主要代价：" + answer["tradeoff"], "下一步：" + answer["next_action"],
                  "观察窗口：" + answer["observation_window"], "停止条件：" + answer["stop_condition"],
                  "", "对方积极：" + answer["branches"]["positive"],
                  "对方含糊：" + answer["branches"]["ambiguous"],
                  "对方拒绝：" + answer["branches"]["negative"],
                  "没有回应：" + answer["branches"]["no_response"]))
    if answer["questions"]:
        lines.extend(("", "影响建议的关键问题：", *["• " + q for q in answer["questions"]]))
    return "\n".join(lines)
