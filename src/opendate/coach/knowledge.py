"""Small, explicit scene routes over the pinned goutoujunshi knowledge library."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCENES = {"auto": "自动识别", "reply": "接话与表达", "emotion": "情绪支持",
          "invitation": "主动表达与邀约", "conflict": "冲突与修复", "reciprocity": "互惠与投入",
          "clarify": "关系确认", "exit": "降级与退出", "family": "婚姻与家庭",
          "personality": "人格标签校准", "attachment": "依恋与焦虑", "safety": "安全优先"}
MODES = {"reply": "帮我回复", "coach": "分析局面", "review": "复盘我的表达", "rehearsal": "陪我演练"}
ROUTES = {
    "reply": ("practical/实战话术编排器", "practical/场景感、松弛感"),
    "emotion": ("practical/为他人提供情绪价值", "knowledge/03-"),
    "invitation": ("practical/实战话术编排器", "practical/主动表达、第一次见面"),
    "conflict": ("knowledge/07-", "practical/万能吵架技巧"),
    "reciprocity": ("practical/关系投入失衡", "knowledge/02-"),
    "clarify": ("practical/实战话术编排器", "knowledge/08-"),
    "exit": ("practical/关系投入失衡", "knowledge/15-"),
    "safety": ("knowledge/05-", "knowledge/17-"),
    "family": ("knowledge/11-", "knowledge/12-"),
    "personality": ("knowledge/04-", "knowledge/16-"),
    "attachment": ("knowledge/03-", "practical/为他人提供情绪价值"),
}
SIGNALS = (
    ("safety", r"跟踪|家暴|威胁|勒索|下药|偷拍|自杀|自残|不想活|伤害自己|杀了|拿捏|操控|报复"),
    ("exit", r"退出|分手|不想继续|结束关系|别再联系|停止联系"),
    ("conflict", r"吵架|冷战|争吵|道歉|冲突|和好|修复"),
    ("reciprocity", r"只有我主动|单方面|投入|失衡|一直不回|总是取消|互惠"),
    ("family", r"婚姻|结婚|育儿|家务|公婆|岳父|双方家庭|彩礼"),
    ("personality", r"MBTI|mbti|人格|INFP|ENFP|INTJ|INFJ|星座"),
    ("attachment", r"依恋|回避型|焦虑型|患得患失"),
    ("clarify", r"确认关系|什么关系|认真交往|排他|在一起|关系期待"),
    ("invitation", r"邀约|约会|见面|一起.*(?:吃饭|咖啡|看展)|表达喜欢|表白"),
    ("emotion", r"难过|焦虑|崩溃|委屈|失眠|好累|很累|心情不好"),
)


def route(question: str, incoming: str, requested="auto", *, mode="reply") -> str:
    # Safety evidence always takes precedence over a manual topic selection.
    combined = question + "\n" + incoming
    if re.search(SIGNALS[0][1], combined):
        return "safety"
    if requested != "auto":
        if requested not in ROUTES:
            raise ValueError("未知军师场景")
        return requested
    # The user's current question wins over the peer's incidental vocabulary.
    for text in (question, incoming):
        for scene, pattern in SIGNALS[1:]:
            if re.search(pattern, text):
                return scene
    return "reply"


def references(scene: str) -> list[dict[str, str]]:
    selected = ["knowledge/01-", *ROUTES[scene]]
    output = []
    for prefix in selected:
        paths = sorted((ROOT / "references").glob(prefix + "*.md"))
        if len(paths) != 1:
            raise ValueError(f"军师知识文件缺失或不唯一：{prefix}")
        path = paths[0]
        output.append({"path": path.relative_to(ROOT).as_posix(),
                       "text": path.read_text(encoding="utf-8")})
    if sum(len(item["text"]) for item in output) > 26000:
        raise ValueError("军师知识上下文超出预算")
    return output
