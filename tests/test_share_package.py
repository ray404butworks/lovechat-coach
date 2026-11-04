import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("package_share", Path(__file__).parents[1] / "scripts/package_share.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


def test_share_snapshot_excludes_runtime_secrets_transcripts_and_git_history(tmp_path):
    project = tmp_path / "private"
    project.mkdir()
    fixtures = {"README.md":"公开说明", "src/module.py":"print('hello')", "tests/test_sample.py":"assert True",
                "config.wechat.yaml":"wechat:\n  contact: 私人好友\n  my_name: 私人本人\n",
                ".env":"SECRET=private-value", "src/.env":"SECRET=nested-private-value",
                "src/provider-secrets.json":"encrypted-private", "data/history.txt":"private-chat",
                "src/opendate.egg-info/PKG-INFO":"installation-cache",
                "work/log.txt":"private-log", ".git/config":"private-history",
                "deliverables/private-chat-imports/chat.json":"private-chat"}
    for name, value in fixtures.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    output, archive = tmp_path / "share", tmp_path / "share.zip"
    manifest = pack.build(project, output, archive)
    assert manifest["source_files"] == 3
    with zipfile.ZipFile(archive) as zipped:
        assert sorted(zipped.namelist()) == ["lovechat-coach/README.md", "lovechat-coach/SHARE_MANIFEST.json",
                                             "lovechat-coach/src/module.py", "lovechat-coach/tests/test_sample.py"]
        for secret in [b"private-value", b"nested-private-value", b"private-chat", b"encrypted-private", b"private-history", b"private-log"]:
            assert not any(secret in zipped.read(name) for name in zipped.namelist())
    assert json.loads((output/"SHARE_MANIFEST.json").read_text())["privacy_scan"] == "passed"


def test_privacy_findings_stop_before_any_share_files_are_written(tmp_path):
    project = tmp_path / "private"
    project.mkdir()
    (project/"config.wechat.yaml").write_text("wechat:\n  contact: 私人好友\n", encoding="utf-8")
    (project/"README.md").write_text("包含私人好友的个人说明", encoding="utf-8")
    with pytest.raises(ValueError, match="configured personal identity"):
        pack.build(project, tmp_path/"share", tmp_path/"share.zip")
    assert not (tmp_path/"share").exists()
    assert not (tmp_path/"share.zip").exists()
