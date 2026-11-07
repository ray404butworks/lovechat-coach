"""Agent entry point for the full project's private, consent-gated memory API."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--contact", default="")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--change-file", type=Path)
    parser.add_argument("action", choices=["status", "show", "context", "enable", "pause", "resume", "revoke", "undo", "forget", "clear", "apply"])
    args = parser.parse_args()
    project = args.project.resolve()
    if not (project / "pyproject.toml").is_file():
        parser.error("请提供实际 LoveChat Coach 项目目录")
    sys.path.insert(0, str(project / "src"))
    from opendate.coach.memory import CoachMemory
    from opendate.config import load_config
    config = load_config(project / "config.wechat.yaml")
    store = CoachMemory(project / config.data_dir / "coach-memory.sqlite3")
    if args.action in {"show", "context", "undo", "forget", "apply"} and not args.contact.strip():
        parser.error("此操作需要准确对象代号或备注 --contact")
    try:
        if args.action == "status":
            result = store.status()
        elif args.action in {"show", "context"}:
            result = getattr(store, "rows" if args.action == "show" else "context")(args.contact)
        elif args.action == "undo":
            store.undo(args.contact)
            result = {"ok": True}
        elif args.action == "forget":
            store.forget(args.contact, confirmed=args.confirm)
            result = {"ok": True}
        elif args.action == "apply":
            if not args.confirm or not args.change_file:
                parser.error("请先核对变更文件，再用 --confirm --change-file 保存")
            changes = json.loads(args.change_file.read_text(encoding="utf-8"))
            result = {"operation": store.apply(args.contact, changes)}
        else:
            store.control(args.action, confirmed=args.confirm)
            result = {"ok": True}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
