"""Bounded, contact-scoped snapshots with consent, provenance and undo.

Adapted design: goutoujunshi/scripts/memory_store.py (MIT, see UPSTREAM_LICENSE).
The existing imported chat archive is separate and is never modified here.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..wechat_memory import HistoryError

LIMITS = {"user": 30, "object": 15, "relationship": 10, "event": 20, "hypothesis": 5}
SOURCES = {"user_explicit", "user_report", "chat", "assistant_inference"}


class CoachMemory:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS policy(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entries(
                contact TEXT, scope TEXT, field TEXT, value TEXT, source_type TEXT,
                source_ref TEXT, occurred_at TEXT, updated_at TEXT,
                PRIMARY KEY(contact, scope, field));
            CREATE TABLE IF NOT EXISTS changes(
                id INTEGER PRIMARY KEY AUTOINCREMENT, contact TEXT, operation TEXT UNIQUE,
                snapshot TEXT, created_at TEXT);
        """)
        return db

    def status(self):
        if not self.path.exists():
            return {"enabled": False, "paused": False}
        with self.connect() as db:
            flags = {r["key"]: r["value"] for r in db.execute("SELECT * FROM policy")}
        return {"enabled": flags.get("enabled") == "1", "paused": flags.get("paused") == "1"}

    def control(self, action: str, *, confirmed=False):
        if action not in {"enable", "pause", "resume", "revoke", "clear"}:
            raise HistoryError("未知档案操作")
        if action in {"enable", "clear"} and not confirmed:
            raise HistoryError("此操作需要本人明确确认")
        with self.connect() as db:
            if action == "clear":
                db.execute("DELETE FROM entries")
                db.execute("DELETE FROM changes")
                db.execute("DELETE FROM policy")
            elif action in {"enable", "revoke"}:
                db.execute("INSERT OR REPLACE INTO policy VALUES ('enabled', ?)",
                           ("1" if action == "enable" else "0",))
                db.execute("INSERT OR REPLACE INTO policy VALUES ('paused', '0')")
            elif not self.status()["enabled"]:
                raise HistoryError("请先明确启用精简档案")
            else:
                db.execute("INSERT OR REPLACE INTO policy VALUES ('paused', ?)",
                           ("1" if action == "pause" else "0",))

    @staticmethod
    def _validate(delta):
        if not isinstance(delta, dict):
            raise HistoryError("档案条目格式错误")
        for key in ("scope", "field", "value", "source_type", "source_ref"):
            if not isinstance(delta.get(key), str) or not delta[key].strip():
                raise HistoryError("档案条目缺少内容或来源")
        scope, source = delta["scope"], delta["source_type"]
        if scope not in LIMITS or source not in SOURCES:
            raise HistoryError("档案分类或来源无效")
        if len(delta["field"]) > 64 or len(delta["value"]) > 200 or len(delta["source_ref"]) > 200:
            raise HistoryError("单条精简档案最多 200 字，字段最多 64 字")
        if scope == "user" and source != "user_explicit":
            raise HistoryError("本人稳定资料只接受本人明确陈述")
        if scope in {"object", "relationship"} and source not in {"user_explicit", "user_report"}:
            raise HistoryError("稳定档案不能由聊天工具或模型推断升级而来")
        if source == "assistant_inference" and scope != "hypothesis":
            raise HistoryError("模型推断只能保存为可纠正的假设")
        if scope == "hypothesis" and delta.get("confidence") not in {"low", "medium", "high"}:
            raise HistoryError("假设必须注明置信程度")

    def rows(self, contact):
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM entries WHERE contact=? ORDER BY updated_at, rowid", (contact,))]

    def context(self, contact, budget=4000):
        if self.status() != {"enabled": True, "paused": False}:
            return []
        rows = self.rows(contact)
        stable = [r for r in reversed(rows) if r["scope"] in {"user", "object", "relationship"}]
        hypotheses = [r for r in reversed(rows) if r["scope"] == "hypothesis"][:3]
        events = [r for r in reversed(rows) if r["scope"] == "event"][:8]
        result = []
        for item in [*stable, *hypotheses, *events]:
            if len(json.dumps([*result, item], ensure_ascii=False)) > budget:
                continue
            result.append(item)
        return result

    def apply(self, contact, deltas):
        if not isinstance(contact, str) or not contact.strip() or len(contact) > 100:
            raise HistoryError("请指定唯一对象")
        if not isinstance(deltas, list) or len(deltas) > 10:
            raise HistoryError("单次最多更新 10 条档案")
        for delta in deltas:
            self._validate(delta)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            flags = {r["key"]: r["value"] for r in db.execute("SELECT * FROM policy")}
            if flags.get("enabled") != "1" or flags.get("paused") == "1":
                raise HistoryError("精简档案未启用或已暂停，本次未保存")
            before = [dict(r) for r in db.execute("SELECT * FROM entries WHERE contact=?", (contact,))]
            now = datetime.now(timezone.utc).isoformat()
            for delta in deltas:
                value = delta["value"]
                if delta["scope"] == "hypothesis":
                    value = json.dumps({"text": value, "confidence": delta["confidence"]}, ensure_ascii=False)
                db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?) "
                           "ON CONFLICT(contact,scope,field) DO UPDATE SET value=excluded.value, "
                           "source_type=excluded.source_type, source_ref=excluded.source_ref, "
                           "occurred_at=excluded.occurred_at, updated_at=excluded.updated_at",
                           (contact, delta["scope"], delta["field"], value, delta["source_type"],
                            delta["source_ref"], delta.get("occurred_at", ""), now))
            for scope, cap in LIMITS.items():
                count = db.execute("SELECT COUNT(*) FROM entries WHERE contact=? AND scope=?",
                                   (contact, scope)).fetchone()[0]
                if count > cap:
                    if scope not in {"event", "hypothesis"}:
                        raise HistoryError("稳定档案已达上限，请先整理字段")
                    db.execute("DELETE FROM entries WHERE rowid IN (SELECT rowid FROM entries "
                               "WHERE contact=? AND scope=? ORDER BY updated_at, rowid LIMIT ?)",
                               (contact, scope, count - cap))
            if db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] > 200:
                raise HistoryError("精简档案已达总量 200 条上限，请先整理")
            after = [dict(r) for r in db.execute("SELECT * FROM entries WHERE contact=?", (contact,))]
            # Compare semantic content, not update timestamps, to avoid duplicate writes.
            def semantic(rows):
                return [{k: v for k, v in r.items() if k != "updated_at"} for r in rows]
            if semantic(before) == semantic(after):
                db.rollback()
                return ""
            op = uuid.uuid4().hex
            db.execute("INSERT INTO changes(contact,operation,snapshot,created_at) VALUES (?,?,?,?)",
                       (contact, op, json.dumps(before, ensure_ascii=False), now))
            db.execute("DELETE FROM changes WHERE id NOT IN (SELECT id FROM changes ORDER BY id DESC LIMIT 20)")
        return op

    def undo(self, contact):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM changes WHERE contact=? ORDER BY id DESC LIMIT 1", (contact,)).fetchone()
            if not row:
                raise HistoryError("当前对象没有可撤销的更新")
            snapshot = json.loads(row["snapshot"])
            others = db.execute("SELECT COUNT(*) FROM entries WHERE contact<>?", (contact,)).fetchone()[0]
            if others + len(snapshot) > 200:
                raise HistoryError("撤销会超过档案总量上限，请先整理")
            db.execute("DELETE FROM entries WHERE contact=?", (contact,))
            db.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)", [tuple(r.values()) for r in snapshot])
            db.execute("DELETE FROM changes WHERE id=?", (row["id"],))

    def forget(self, contact, *, confirmed=False):
        if not confirmed:
            raise HistoryError("请确认准确对象后再删除")
        with self.connect() as db:
            db.execute("DELETE FROM entries WHERE contact=?", (contact,))
            db.execute("DELETE FROM changes WHERE contact=?", (contact,))
