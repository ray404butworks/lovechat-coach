"""Synthetic transport; real routing, evidence checks and storage still run."""
import json


def answer(packet, reply="行嘞，周日下午一起去看展？"):
    sources = packet.get("evidence_sources", {})
    key = "current" if "current" in sources else next(iter(sources), None)
    return {
        "emotion": "先把这轮沟通说清楚，不急着猜对方的内心。",
        "facts": [{"source_id": key, "quote": sources[key]["text"][:80]}] if key else [],
        "hypotheses": [], "unknowns": ["对方的真实期待还需要交流确认"],
        "recommendation": "先自然回应对方这次说的内容。", "reasons": ["以当前可见原文为依据，不把目标当成事实"],
        "main_action": "澄清", "reply": reply, "timing": "本人核对后决定是否发送",
        "tradeoff": "一次回应不能证明关系会如何发展", "next_action": "只确认一个具体安排",
        "observation_window": "看对方下一次是否给出明确安排", "stop_condition": "对方拒绝或不适时停止当前方向",
        "branches": {"positive": "确认安排，不额外加码", "ambiguous": "不追加追问",
                     "negative": "尊重拒绝，停止推进", "no_response": "先不追发"},
        "questions": [], "memory_suggestions": [],
    }


class CoachRouter:
    def __init__(self, *, reviews=None, reply=None, mutate=None):
        self.calls = []
        self.reviews = iter(reviews or [True] * 20)
        self.reply = reply
        self.mutate = mutate

    def chat(self, system, user, **kwargs):
        phase = system.split("WORKFLOW_PHASE: ")[1].splitlines()[0]
        packet = json.loads(user)
        self.calls.append((phase, system, packet))
        if phase == "review":
            ok = next(self.reviews)
            return json.dumps({"allowed": ok, "issues": [] if ok else ["需要修正事实或表达"]})
        value = answer(packet, self.reply if self.reply is not None else "行嘞，周日下午一起去看展？")
        if self.mutate:
            self.mutate(value, packet)
        return json.dumps(value, ensure_ascii=False)
