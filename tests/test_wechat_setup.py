import json
import os
import tkinter as tk

import pytest

from opendate.config import AppConfig, load_config
from opendate.llm.router import LLMResult
from opendate.wechat_credentials import provider_secrets, save_api_key
from opendate.wechat_desktop import make_router
from opendate.wechat_memory import ChatMemory, HistoryError
from opendate.wechat_profile import load_persona
from opendate.wechat_setup import SetupDraft, SetupWizard, finish_setup


def fake_history(tmp_path):
    path = tmp_path / "chat.txt"
    path.write_text("好友\n2026年10月19日 21:22\n一起散步？\n本人\n2026年10月19日 21:23\n好呀", encoding="utf-8")
    return path


def test_setup_imports_history_persona_and_separate_goal_into_real_context(tmp_path):
    draft = SetupDraft(contact="好友", my_name="本人", history_path=str(fake_history(tmp_path)),
                       personality="慢热", voice="自然简短", my_info="我喜欢咖啡", peer_info="对方喜欢散步",
                       goal="自然熟悉后约见面", allow_upload=True)
    config = finish_setup(tmp_path, draft)
    memory = ChatMemory(tmp_path / "data/wechat/wechat-memory.sqlite3")
    assert memory.stats("好友")[0] == 2
    assert memory.stats("其他人")[0] == 0
    assert load_config(tmp_path / "config.wechat.yaml").wechat.allow_llm_upload
    assert load_persona(tmp_path, config).personality == "慢热"
    from opendate.wechat_desktop import build_reply_context
    actual = build_reply_context(config, tmp_path, memory, "下周有空吗？")
    assert actual["conversation_goal"]["desired_outcome"] == "自然熟悉后约见面"
    assert actual["participants"]["me"]["confirmed_information"] == "我喜欢咖啡"
    assert actual["participants"]["peer"]["confirmed_information"] == "对方喜欢散步"
    assert memory.source("好友")[0].endswith("chat.txt")


def test_invalid_history_never_writes_config_or_partial_archive(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("未识别文本", encoding="utf-8")
    draft = SetupDraft(contact="好友", my_name="本人", history_path=str(bad))
    with pytest.raises(HistoryError):
        finish_setup(tmp_path, draft)
    assert not (tmp_path / "config.wechat.yaml").exists()
    assert not (tmp_path / "data/wechat/wechat-memory.sqlite3").exists()


def test_resume_draft_contains_no_key_and_cannot_silently_enable_upload(tmp_path):
    draft = SetupDraft(step=4, contact="好友", my_name="本人", skip_history=True)
    draft.save(tmp_path)
    assert SetupDraft.load(tmp_path) == draft
    assert "api_key" not in json.loads((tmp_path / "data/wechat/setup-draft.json").read_text(encoding="utf-8"))
    config = finish_setup(tmp_path, draft)
    assert not config.wechat.allow_llm_upload
    assert not (tmp_path / "data/wechat/setup-draft.json").exists()
    with pytest.raises(HistoryError, match="未开启"):
        make_router(config, tmp_path)


def test_api_mode_requires_local_key_before_any_configuration_write(tmp_path, monkeypatch):
    monkeypatch.setattr("opendate.wechat_setup.provider_secrets", lambda *_: {})
    with pytest.raises(HistoryError, match="API Key"):
        finish_setup(tmp_path, SetupDraft(provider="deepseek", model="fixture-model", contact="好友", my_name="本人", skip_history=True))
    assert not (tmp_path / "config.wechat.yaml").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows user-level DPAPI")
def test_dpapi_key_is_encrypted_and_selected_provider_reaches_actual_router(tmp_path, monkeypatch):
    key = "fixture-secret-value-only-for-this-test"
    save_api_key(tmp_path, "data/wechat", "deepseek", key)
    stored = (tmp_path / "data/wechat/provider-secrets.json").read_text()
    assert key not in stored
    secrets = provider_secrets(tmp_path, "data/wechat", "deepseek")
    assert secrets["DEEPSEEK_API_KEY"] == key
    assert not (tmp_path / ".env").exists()
    calls = []
    def complete(_self, resolved, messages, **kwargs):
        calls.append((resolved.provider, resolved.api_key, messages))
        return LLMResult("测试草稿", resolved.provider, resolved.model)
    monkeypatch.setattr("opendate.llm.router.LiteLLMBackend.complete", complete)
    config = AppConfig(source="wechat", llm={"provider":"deepseek", "model":"deepseek-chat"},
                       data_dir="data/wechat", wechat={"contact":"好友", "my_name":"本人",
                       "allowed_contacts":["好友"], "allow_llm_upload":True})
    router = make_router(config, tmp_path)
    assert router.chat("只生成测试草稿", "虚构来信") == "测试草稿"
    assert calls[0][0:2] == ("deepseek", key)


@pytest.fixture
def root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No native display")
    root.withdraw()
    yield root
    if root.winfo_exists():
        root.destroy()


def test_all_seven_gui_steps_require_confirmation_and_save_actual_inputs(root, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr("opendate.wechat_setup.messagebox.showerror", lambda *args, **kw: errors.append(args))
    completed = []
    wizard = SetupWizard(root, tmp_path, lambda: completed.append(True))
    wizard.next()  # Codex; no model request.
    wizard.next()  # Existing login may be checked manually later.
    wizard.fields["my_name"].set("本人")
    wizard.fields["contact"].set("好友")
    wizard.next()
    wizard.fields["history_path"].set(str(fake_history(tmp_path)))
    wizard.preview_history()
    assert "2 条文字" in wizard.history_note.get()
    wizard.next()
    wizard.fields["personality"].insert("1.0", "慢热但坦诚")
    wizard.next()
    wizard.fields["goal"].insert("1.0", "互相了解")
    wizard.next()
    assert wizard.draft.step == 6
    assert not (tmp_path / "config.wechat.yaml").exists()
    wizard.next()
    assert errors and not completed
    assert not (tmp_path / "config.wechat.yaml").exists()
    wizard.confirm.set(True)
    wizard.next()
    assert completed == [True]
    assert load_config(tmp_path / "config.wechat.yaml").wechat.contact == "好友"
    assert ChatMemory(tmp_path / "data/wechat/wechat-memory.sqlite3").stats("好友")[0] == 2


def test_switching_providers_clears_unsubmitted_key_and_model(root, tmp_path):
    wizard = SetupWizard(root, tmp_path, lambda: None)
    wizard.api_key.set("unsaved-synthetic-key")
    wizard.draft.model = "other-provider-model"
    wizard.fields["provider"].set("deepseek")
    wizard.next()
    assert wizard.api_key.get() == ""
    assert wizard.draft.model == ""


def test_resuming_api_draft_returns_to_local_key_question(root, tmp_path, monkeypatch):
    SetupDraft(step=5, provider="deepseek", model="fixture-model", contact="好友", my_name="本人", skip_history=True).save(tmp_path)
    monkeypatch.setattr("opendate.wechat_setup.provider_secrets", lambda *_: {})
    wizard = SetupWizard(root, tmp_path, lambda: None)
    assert wizard.draft.step == 1
    assert wizard.api_key.get() == ""
    assert wizard.draft.contact == "好友"


def test_resuming_missing_history_returns_to_file_question(root, tmp_path):
    SetupDraft(step=6, contact="好友", my_name="本人", history_path=str(tmp_path / "moved.txt")).save(tmp_path)
    wizard = SetupWizard(root, tmp_path, lambda: None)
    assert wizard.draft.step == 3
