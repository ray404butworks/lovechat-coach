"""Build an inspected source snapshot, never a copy of a private working tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

import yaml

ROOT_FILES = {".gitignore", ".env.example", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md",
              "pyproject.toml", "requirements.txt", "Makefile", "CHANGELOG.md", "CONTRIBUTING.md",
              "CODE_OF_CONDUCT.md", "SECURITY.md", "config.example.yaml", "config.wechat.example.yaml",
              "恋聊教练.vbs"}
ROOT_DIRS = {"src", "tests", "scripts", "skills", "examples", "docs", ".github"}
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", "private-chat-imports", "node_modules", "data", "work", ".git", ".venv", "venv"}
SKIP_NAMES = {"config.wechat.yaml", "config.wechat.yaml.tmp", "provider-secrets.json", "provider-secrets.json.tmp", "setup-draft.json"}
SKIP_SUFFIXES = {".pyc", ".sqlite", ".sqlite3", ".db", ".png", ".jpg", ".jpeg", ".lnk", ".zip", ".exe", ".dll"}
PATTERNS = {
    "personal Windows path": re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!Public(?:[\\/]|\b)|EXAMPLE(?:[\\/]|\b))[^\s\"'<>]+", re.I),
    "personal workspace path": re.compile(r"G:[\\/]AI[\\/]OpenAI[\\/](?:Workspaces|CodexHome)", re.I),
    "WeChat account ID": re.compile(r"wxid_[a-zA-Z0-9]{6,}"),
    "possible API token": re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def collect(project: Path):
    """Return bytes only after every selected file passes local privacy checks."""
    private = project / "config.wechat.yaml"
    identities = []
    if private.exists():
        config = yaml.safe_load(private.read_text(encoding="utf-8")) or {}
        wechat = config.get("wechat", {})
        identities = [v for v in [wechat.get("my_name"), wechat.get("contact"),
                                  *wechat.get("allowed_contacts", [])] if isinstance(v, str) and v]
    result, findings = {}, []
    candidates = [project / name for name in ROOT_FILES if (project / name).is_file()]
    for name in ROOT_DIRS:
        directory = project / name
        if directory.is_dir():
            candidates.extend(directory.rglob("*"))
    for path in sorted(candidates):
        rel = path.relative_to(project)
        if rel.parts[0] not in ROOT_DIRS and rel.as_posix() not in ROOT_FILES:
            continue
        if (any(part in SKIP_PARTS or part.endswith((".egg-info", ".dist-info")) for part in rel.parts)
                or path.suffix.lower() in SKIP_SUFFIXES):
            continue
        if path.name in SKIP_NAMES or (path.name.startswith(".env") and path.name != ".env.example"):
            continue
        if not path.is_file():
            continue
        if path.is_symlink():
            findings.append(f"{rel.as_posix()}: unexpected symlink")
            continue
        if rel.as_posix() == "docs/release-readme.md":
            continue
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeError:
            findings.append(f"{rel.as_posix()}: unreviewed binary encoding")
            continue
        for category, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{rel.as_posix()}: {category}")
        if any(identity in content for identity in identities):
            findings.append(f"{rel.as_posix()}: configured personal identity")
        result[rel.as_posix()] = raw
    if findings:
        raise ValueError("Privacy scan failed:\n" + "\n".join(findings))
    return result


def build(project: Path, output: Path, archive: Path):
    if output.exists() or archive.exists():
        raise ValueError("Output already exists; choose a fresh output path to preserve previous files.")
    if output.resolve().is_relative_to(project.resolve()):
        raise ValueError("Share snapshot must be outside the private working project.")
    files = collect(project)
    output.mkdir(parents=True)
    for relative, content in files.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {"project":"LoveChat Coach", "version":"0.3.0", "privacy_scan":"passed",
                "source_files":len(files), "excluded":["private runtime data", "local configuration",
                "chat transcripts", "keys and account caches", "screenshots", "virtual environment",
                "local logs and machine-specific deliverables", "Git history"],
                "sha256":{name:hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    (output / "SHARE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                zipped.write(path, "lovechat-coach/" + path.relative_to(output).as_posix())
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.project.resolve(), args.output.resolve(), args.archive.resolve())
    print(json.dumps({"privacy_scan":"passed", "source_files":manifest["source_files"],
                      "archive":str(args.archive)}, ensure_ascii=False))
