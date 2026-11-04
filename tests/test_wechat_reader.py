import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendate.wechat_memory import HistoryError, read_history
from opendate.wechat_reader import LocalHistoryReader


def test_reader_exact_scope_and_private_selection_over_stdin(tmp_path, monkeypatch):
    reader = LocalHistoryReader(tmp_path)
    seen = []
    def run(args, *, selection=None):
        seen.append((args, selection))
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "wechat_messages.json").write_text("[]", encoding="utf-8")
        return {"available": True}
    monkeypatch.setattr(reader, "run", run)
    path = reader.export({"account_id": "wx_me", "conversation_id": "wx_peer",
                          "conversation_type": "direct"})
    args, selection = seen[0]
    assert "wx_me" not in args and "wx_peer" not in args
    assert "--selection-stdin" in args
    assert selection["conversation_scopes"] == [{"account_id": "wx_me",
           "conversation_id": "wx_peer", "conversation_type": "direct"}]
    assert path.is_relative_to(tmp_path)
    assert "extract-key" not in args


def test_no_cached_access_is_actionable_and_never_auto_extracts(tmp_path, monkeypatch):
    reader = LocalHistoryReader(tmp_path)
    seen = []
    def run(args):
        seen.append(args)
        return {"available": False, "accounts": [], "conversations": []}
    monkeypatch.setattr(reader, "run", run)
    with pytest.raises(HistoryError, match="尚无可用"):
        reader.directory("测试好友", "G:/local-data")
    assert seen == [["directory", "--source", "wechat", "--data-root", "G:/local-data"]]


def test_directory_only_returns_exact_contact_private_sessions(tmp_path, monkeypatch):
    reader = LocalHistoryReader(tmp_path)
    monkeypatch.setattr(reader, "run", lambda _: {"available": True, "conversations": [
        {"label": "测试好友", "conversation_type": "direct", "conversation_id": "peer"},
        {"label": "测试好友", "conversation_type": "group", "conversation_id": "group"},
        {"label": "测试好友甲", "conversation_type": "direct", "conversation_id": "other"}]})
    assert [r["conversation_id"] for r in reader.directory("测试好友")] == ["peer"]


def test_reader_binary_integrity_is_checked_before_execution(tmp_path, monkeypatch):
    reader = LocalHistoryReader(tmp_path)
    reader.exe.parent.mkdir(parents=True)
    reader.exe.write_bytes(b"tampered binary")
    monkeypatch.setattr("opendate.wechat_reader.subprocess.run", lambda *a, **k: pytest.fail("executed"))
    with pytest.raises(HistoryError, match="完整性"):
        reader.run(["directory", "--source", "wechat"])


def test_directory_unavailable_exit_code_is_not_misread_as_bad_protocol(tmp_path, monkeypatch):
    reader = LocalHistoryReader(tmp_path)
    reader.exe.parent.mkdir(parents=True)
    reader.exe.write_bytes(b"fixture")
    monkeypatch.setattr("opendate.wechat_reader.hashlib.file_digest",
                        lambda *_: SimpleNamespace(hexdigest=lambda: __import__(
                            "opendate.wechat_reader", fromlist=["READER_SHA256"]).READER_SHA256))
    monkeypatch.setattr("opendate.wechat_reader.subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout='{"available":false,"source":"wechat"}'))
    assert reader.run(["directory", "--source", "wechat"])["available"] is False


def test_accounts_with_same_contact_id_do_not_mix(tmp_path):
    rows = [{"ts": 1780000000, "sender": "好友", "chat_room": "好友",
             "conversation_id": "wx_peer", "conversation_type": "direct",
             "is_self": False, "content": "各自账户的记录", "msg_type": 1,
             "account_id": account} for account in ["wx_me_1", "wx_me_2"]]
    path = tmp_path / "history.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    batches = read_history(path, "好友", "本人")
    assert len(batches) == 2
    assert batches[0].records[0].key != batches[1].records[0].key


def test_authorization_keeps_closed_client_protocol_and_cleans_terminal_lease(tmp_path, monkeypatch):
    reader = LocalHistoryReader(tmp_path)
    seen = []
    def run(args, **kwargs):
        seen.append((args, kwargs))
        if args[0] == "extract-key":
            return {"ok": False, "terminal": True, "error_code": "client_running",
                    "operation_id": kwargs["selection"]["operation_id"]}
        return {"ok": True}
    monkeypatch.setattr(reader, "run", run)
    result = reader.authorize("G:/own-wechat", timeout=60)
    assert result["error_code"] == "client_running"
    assert "--key-recovery-v1-stdin" in seen[0][0]
    assert seen[0][1]["source_authorization"] is True
    assert seen[1][0] == ["key-recovery-v1", "--action", "cleanup", "--request-stdin"]
    assert seen[1][1]["selection"]["operation_id"] == seen[0][1]["selection"]["operation_id"]


def test_patched_windows_bootstrap_preserves_named_parameters(tmp_path):
    import os
    import hashlib
    import base64
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows PowerShell binding regression")
    active = pytest.importorskip("chatlog_keeper.active_key")
    fixture = tmp_path / "safe fixture.ps1"
    fixture.write_text('param([string]$DbPath,[int]$TimeoutSeconds,[switch]$NoDebugForKey, '
                       '[switch]$RequireClosedClient)\n'
                       'Write-Host ("FIXTURE:" + $DbPath + ":" + $TimeoutSeconds + ":" + '
                       '$NoDebugForKey.IsPresent + ":" + $RequireClosedClient.IsPresent)', encoding="utf-8-sig")
    log = tmp_path / "fixture.txt"
    bootstrap = active._active_bootstrap(script=fixture,
        script_digest=hashlib.sha256(fixture.read_bytes()).hexdigest(),
        args=["-DbPath", "sample path with spaces", "-TimeoutSeconds", "7",
              "-NoDebugForKey", "-RequireClosedClient"], out_path=log)
    encoded = base64.b64encode(bootstrap.encode("utf-16le")).decode("ascii")
    env = active._windows_active_environment()
    process = subprocess.run([active._trusted_windows_powershell_path(), "-NoProfile",
                              "-EncodedCommand", encoded], capture_output=True, timeout=25,
                             creationflags=subprocess.CREATE_NO_WINDOW, env=env)
    assert process.returncode == 0
    assert "FIXTURE:sample path with spaces:7:True:True" in log.read_text(encoding="utf-8-sig")


def test_authorization_does_not_pick_old_account_from_shared_root(tmp_path, monkeypatch):
    root = tmp_path / "xwechat_files"
    for name in ["wx_old", "wx_current"]:
        (root / name / "db_storage/message").mkdir(parents=True)
    reader = LocalHistoryReader(tmp_path)
    monkeypatch.setattr(reader, "run", lambda *a, **k: pytest.fail("must choose account first"))
    with pytest.raises(HistoryError, match="多个微信账号"):
        reader.authorize(str(root))
