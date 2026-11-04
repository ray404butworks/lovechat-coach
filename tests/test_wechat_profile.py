import json
import tkinter as tk

import pytest

from opendate.config import AppConfig, load_config
from opendate.wechat_memory import ChatMemory, HistoryError
from opendate.wechat_profile import MyPersona, load_persona, save_settings


def config():
    return AppConfig(source="wechat", llm={"provider": "codex"},
        wechat={"contact": "测试好友一", "my_name": "测试本人", "allowed_contacts": ["测试好友一"]})


def test_custom_persona_and_target_survive_relaunch_without_merging_notes(tmp_path):
    original = config()
    memory = ChatMemory(tmp_path / original.data_dir / "wechat-memory.sqlite3")
    memory.save_notes("测试好友一", "一号的专属记忆")
    memory.save_notes("测试好友二", "二号的专属记忆")
    persona = MyPersona(personality="随和但有主见", voice="简短、温和，少用表情",
                        avoid="不要叫宝贝", examples="行，那周末再说")
    updated = save_settings(tmp_path, original, persona, "测试好友二", "测试本人")
    loaded = load_config(tmp_path / "config.wechat.yaml")
    assert loaded.wechat.contact == "测试好友二"
    assert loaded.wechat.allowed_contacts == ["测试好友二"]
    assert loaded.auto_send is False
    assert load_persona(tmp_path, loaded) == persona
    assert loaded.preferences.voice == persona.voice
    assert memory.notes("测试好友一") == "一号的专属记忆"
    assert memory.notes(updated.wechat.contact) == "二号的专属记忆"
    assert "专属记忆" not in json.dumps(persona.packet("测试本人"), ensure_ascii=False)


@pytest.mark.parametrize("target", ["", "测试本人", "测试本人 后缀"])
def test_bad_target_is_rejected_before_any_settings_are_written(tmp_path, target):
    with pytest.raises(ValueError):
        save_settings(tmp_path, config(), MyPersona(), target, "测试本人")
    assert not (tmp_path / "config.wechat.yaml").exists()


def test_persona_limits_and_corrupt_profile_do_not_silently_replace_user_data(tmp_path):
    with pytest.raises(HistoryError):
        MyPersona(voice="").validate()
    with pytest.raises(HistoryError):
        MyPersona(personality="长" * 7000).validate()
    profile = tmp_path / config().data_dir / "my-profile.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("broken", encoding="utf-8")
    with pytest.raises(HistoryError, match="无法读取"):
        load_persona(tmp_path, config())
    assert profile.read_text() == "broken"


def test_desktop_switch_preserves_unsaved_contact_drafts_and_applies_persona(tmp_path, monkeypatch):
    from opendate.wechat_gui import WeChatWindow
    save_settings(tmp_path, config(), MyPersona(), "测试好友一", "测试本人")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No native GUI display")
    root.withdraw()
    window = WeChatWindow(root, tmp_path)
    monkeypatch.setattr("opendate.wechat_gui.messagebox.showerror", lambda *a, **k: pytest.fail(str(a)))
    try:
        window.put(window.notes, "尚未确认的记忆草稿")
        window.put(window.incoming, "一号刚发来的话")
        window.target_var.set("测试好友二")
        window.apply_contact()
        assert window.contact == "测试好友二"
        assert window.notes.get("1.0", "end").strip() == ""
        assert window.memory.notes("测试好友一") == ""
        assert not window.consent.get()
        window.target_var.set("测试好友一")
        window.apply_contact()
        assert window.notes.get("1.0", "end").strip() == "尚未确认的记忆草稿"
        assert window.incoming.get("1.0", "end").strip() == "一号刚发来的话"
        window.put(window.personality_box, "冷静、重视边界")
        window.put(window.voice_box, "每次一两句，不用感叹号")
        window.save_persona()
        assert "冷静" in window.persona_text.get()
        assert window.build_packet()["my_persona"]["voice"] == "每次一两句，不用感叹号"
        assert len(json.dumps(window.build_packet(), ensure_ascii=False)) <= 22000
        assert len(window.tabs.tabs()) == 7
    finally:
        window.closed = True
        root.destroy()
