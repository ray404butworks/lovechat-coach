"""Vendor pinned MIT references; keep the Agent skill and wheel copies identical."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/shengjidaguai-china/goutoujunshi"
REVISION = "6db7354a4002dc7c448a9c87ffdad8132570c9d3"
SKILL = ROOT / "skills/lovechat-coach"
RUNTIME = ROOT / "src/opendate/coach"


def sync(source: Path):
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError("Source revision does not match the reviewed revision")
    hashes = {}
    for path in sorted((source / "references").rglob("*.md")):
        relative = path.relative_to(source)
        raw = path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        for destination in (SKILL / relative, RUNTIME / relative):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        hashes[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    license_text = (source / "LICENSE").read_text(encoding="utf-8")
    manifest = {"repository": UPSTREAM, "revision": REVISION, "license": "MIT", "sha256": hashes}
    for destination in (SKILL, RUNTIME):
        (destination / "UPSTREAM_LICENSE.txt").write_text(license_text, encoding="utf-8", newline="\n")
        (destination / "upstream.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Synced {len(hashes)} references at {REVISION}")


def check():
    manifest = json.loads((SKILL / "upstream.json").read_text(encoding="utf-8"))
    for relative, digest in manifest["sha256"].items():
        raw = (SKILL / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest or raw != (RUNTIME / relative).read_bytes():
            raise ValueError(f"Reference drift: {relative}")
    print(f"Verified {len(manifest['sha256'])} pinned references in Agent and runtime")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    elif args.source:
        sync(args.source.resolve())
    else:
        parser.error("Provide --source or --check")
