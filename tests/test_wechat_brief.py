import json
import tkinter as tk

import pytest

from opendate.config import AppConfig
from opendate.wechat_desktop import build_reply_context, context_preview, generate_reply
from opendate.wechat_memory import ChatMemory, ConversationBrief, HistoryError
from opendate.wechat_profile import MyPersona, save_settings


def setup_context(tmp_path):
    config = AppConfig(source="wechat", llm={"provider": "codex"},
                      wechat={"contact": "测试对象甲", "my_name": "测试本人",
                              "allowed_contacts": ["测试对象甲"]})
    config = save_settings(tmp_path, config, MyPersona(voice="一两句，温和直接"),
                           "测试对象甲", "测试本人")
    memory = ChatMemory(tmp_path / config.data_dir / "wechat-memory.sqlite3")
    return config, memory


def test_facts_and_goal_persist_with_their_owner_and_peer(tmp_path):
    config, memory = setup_context(tmp_path)
    memory.save_brief("测试对象甲", ConversationBrief("我不吃香菜", "对方喜欢辣", "周末一起去看展"))
    memory.save_brief("测试对象乙", ConversationBrief("乙的我方信息", "乙的对方信息", "乙的不同目标"))
    reopened = ChatMemory(memory.path)
    assert reopened.brief("测试对象甲").goal == "周末一起去看展"
    packet = build_reply_context(config, tmp_path, reopened, "晚饭吃什么？")
    assert packet["participants"]["me"]["confirmed_information"] == "我不吃香菜"
    assert packet["participants"]["peer"]["confirmed_information"] == "对方喜欢辣"
    assert packet["participants"]["peer"]["display_name"] == "测试对象甲"
    assert packet["conversation_goal"]["owner"] == "me"
    assert packet["conversation_goal"]["desired_outcome"] == "周末一起去看展"
    assert "乙的" not in json.dumps(packet, ensure_ascii=False)
    assert {"测试对象甲", "测试对象乙"} <= set(reopened.contacts())


def test_actual_model_call_receives_latest_saved_facts_goal_and_style(tmp_path, monkeypatch):
    config, memory = setup_context(tmp_path)
    old_packet = build_reply_context(config, tmp_path, memory, "周末要不要出去？")
    memory.save_brief(config.wechat.contact, ConversationBrief("我周六上午有空", "对方喜欢画展", "邀请周六上午看展"))
    calls = []
    class FakeRouter:
        def chat(self, system, user, **kwargs):
            calls.append((system, json.loads(user)))
            if "WORKFLOW_PHASE: selection" in system:
                return '{"skill":"rapport-building"}'
            if "WORKFLOW_PHASE: quality" in system:
                return '{"score":0.9,"issues":[]}'
            if "WORKFLOW_PHASE: safety" in system:
                return '{"allowed":true,"reason":"符合上下文"}'
            return "周六上午一起去看展？"
    monkeypatch.setattr("opendate.wechat_desktop.make_router", lambda *_: FakeRouter())
    assert generate_reply(config, tmp_path, old_packet) == "周六上午一起去看展？"
    system, actual = calls[0]
    assert actual["participants"]["me"]["confirmed_information"] == "我周六上午有空"
    assert actual["participants"]["peer"]["confirmed_information"] == "对方喜欢画展"
    assert actual["conversation_goal"]["desired_outcome"] == "邀请周六上午看展"
    assert actual["my_persona"]["voice"] == "一两句，温和直接"
    assert "目标不是对方的意愿或承诺" in system
    assert "无依据" in system
    assert memory.stats(config.wechat.contact)[0] == 0


def test_preview_shows_all_saved_material_with_plain_language_roles(tmp_path):
    config, memory = setup_context(tmp_path)
    memory.save_brief(config.wechat.contact, ConversationBrief("我方标记", "对方标记", "目标标记"))
    memory.save_notes(config.wechat.contact, "共同记忆标记")
    packet = build_reply_context(config, tmp_path, memory, "来信标记")
    preview = context_preview(packet)
    for text in ["我的信息", "对方的信息", "最终目标", "我方标记", "对方标记", "目标标记",
                 "共同记忆标记", "来信标记", "一两句，温和直接"]:
        assert text in preview


def test_blank_goal_stays_blank_and_oversized_information_is_not_saved(tmp_path):
    config, memory = setup_context(tmp_path)
    assert build_reply_context(config, tmp_path, memory, "你好")["conversation_goal"]["desired_outcome"] == ""
    with pytest.raises(HistoryError):
        memory.save_brief(config.wechat.contact, ConversationBrief(goal="长" * 3001))
    assert memory.brief(config.wechat.contact) == ConversationBrief()
    with pytest.raises(HistoryError, match="不一致"):
        generate_reply(config, tmp_path, {"contact": "别的对象"})


def test_gui_editors_save_and_switch_without_committing_drafts(tmp_path, monkeypatch):
    from opendate.wechat_gui import WeChatWindow
    _, memory = setup_context(tmp_path)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No GUI display")
    root.withdraw()
    window = WeChatWindow(root, tmp_path)
    monkeypatch.setattr("opendate.wechat_gui.messagebox.showerror", lambda *a, **k: pytest.fail(str(a)))
    try:
        window.put(window.incoming, "本次来信")
        window.put(window.my_info_box, "我方已确认事实")
        window.put(window.peer_info_box, "对方已确认事实")
        window.put(window.goal_box, "甲的已保存目标")
        window.save_brief()
        assert window.build_packet()["conversation_goal"]["desired_outcome"] == "甲的已保存目标"
        previews = []
        monkeypatch.setattr(window, "show_text", lambda title, text: previews.append(text))
        window.put(window.incoming, "")
        window.preview_brief()
        assert "我方已确认事实" in previews[0] and "对方已确认事实" in previews[0]
        assert "甲的已保存目标" in previews[0]
        window.put(window.incoming, "本次来信")
        window.put(window.goal_box, "甲的未保存草稿")
        window.target_var.set("测试对象乙")
        window.apply_contact()
        assert window.goal_box.get("1.0", "end").strip() == ""
        assert memory.brief("测试对象甲").goal == "甲的已保存目标"
        window.put(window.incoming, "乙的来信")
        packet = window.build_packet()
        assert packet["participants"]["me"]["confirmed_information"] == ""
        assert packet["conversation_goal"]["desired_outcome"] == ""
        window.target_var.set("测试对象甲")
        window.apply_contact()
        assert window.goal_box.get("1.0", "end").strip() == "甲的未保存草稿"
        assert "甲的已保存目标" in window.goal_text.get()
        assert window.build_packet()["conversation_goal"]["desired_outcome"] == "甲的已保存目标"
        assert "测试对象甲" in window.peer_info_title.get()
    finally:
        window.closed = True
        root.destroy()
