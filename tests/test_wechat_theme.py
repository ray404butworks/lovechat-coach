import json
import tkinter as tk
from types import SimpleNamespace

import pytest

from opendate.config import AppConfig
from opendate.wechat_gui import WeChatWindow
from opendate.wechat_profile import MyPersona, save_settings
from opendate.wechat_theme import ReplySplit, enable_dpi_awareness
from opendate.wechat_memory import ConversationBrief
from opendate.wechat_strategy import load_settings


@pytest.fixture
def root():
    enable_dpi_awareness()
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("No native display")
    window.geometry("1200x700")
    yield window
    window.destroy()


def test_split_drag_dead_zone_and_escape_restore_the_actual_panes(root):
    split = ReplySplit(root)
    split.pack(fill="both", expand=True)
    root.update()
    initial = split.ratio
    width_before = split.left.winfo_width()
    split.press(SimpleNamespace(x_root=100))
    split.drag(SimpleNamespace(x_root=103))
    root.update()
    assert split.left.winfo_width() == width_before
    split.drag(SimpleNamespace(x_root=180))
    root.update()
    assert split.left.winfo_width() > width_before
    assert split.handle.grab_current() == split.handle
    split.cancel()
    root.update()
    assert split.ratio == initial
    assert split.left.winfo_width() == width_before
    assert split.handle.grab_current() is None


def test_split_keeps_both_editors_reachable_and_resets_to_equal_width(root):
    split = ReplySplit(root)
    split.pack(fill="both", expand=True)
    root.update()
    split.press(SimpleNamespace(x_root=100))
    split.drag(SimpleNamespace(x_root=10000))
    split.release()
    root.update()
    assert split.right.winfo_width() >= 400
    split.reset()
    root.update()
    assert abs(split.left.winfo_width()-split.right.winfo_width()) <= 2
    split.move(-0.025)
    root.update()
    assert split.left.winfo_width() < split.right.winfo_width()


def test_theme_switch_keeps_drafts_consent_and_saved_information(root, tmp_path):
    config = AppConfig(source="wechat", llm={"provider":"codex"}, wechat={
        "contact":"示例对象", "my_name":"示例本人", "allowed_contacts":["示例对象"]})
    save_settings(tmp_path, config, MyPersona(), "示例对象", "示例本人")
    app = WeChatWindow(root, tmp_path)
    try:
        app.put(app.goal_box, "尚未保存的目标草稿")
        app.put(app.incoming, "今天想去哪？")
        app.consent.set(True)
        app.theme_choice.set("夜间")
        app.change_theme()
        root.update()
        assert app.goal_box.get("1.0", "end").strip() == "尚未保存的目标草稿"
        assert app.incoming.get("1.0", "end").strip() == "今天想去哪？"
        assert app.consent.get()
        assert app.memory.brief("示例对象").goal == ""
        assert json.loads(app.theme_path.read_text())["mode"] == "dark"
        assert len(app.tabs.tabs()) == 6
        assert app.incoming.cget("background") == app.theme.colors["field"]
        app.theme_choice.set("暖纸")
        app.change_theme()
        assert json.loads(app.theme_path.read_text())["mode"] == "light"
    finally:
        app.closed = True


def make_app(root, tmp_path):
    config = AppConfig(source="wechat", llm={"provider":"codex"}, wechat={
        "contact":"示例对象", "my_name":"本人", "allowed_contacts":["示例对象"]})
    save_settings(tmp_path, config, MyPersona(), "示例对象", "本人")
    return WeChatWindow(root, tmp_path)


def test_home_starts_from_own_idea_without_fabricating_peer_message(root, tmp_path, monkeypatch):
    app = make_app(root, tmp_path)
    captured = []
    monkeypatch.setattr("opendate.wechat_gui.generate_reply_with_strategy",
                        lambda config, project, packet, *_: captured.append(packet))
    monkeypatch.setattr(app, "run", lambda work, done, **_: work())
    try:
        app.generate_from_home()
        assert "还缺内容" in app.strategy_summary.get()
        app.put(app.reply_request, "想和她聊聊今天的趣事，轻松一点")
        app.consent.set(True)
        app.generate_button.invoke()
        assert len(captured) == 1
        assert captured[0]["message_to_reply"] == ""
        assert "今天的趣事" in captured[0]["user_question"]
        assert captured[0]["strategy_settings"]["action"] == "proactive"
        assert app.memory.stats(app.contact)[0] == 0
        assert load_settings(app.memory, app.contact).question == ""
    finally:
        app.closed = True


def test_blocked_generation_explains_reason_on_home(root, tmp_path, monkeypatch):
    app = make_app(root, tmp_path)
    reason = "可见记录显示仍在等待回应或追发过多，先不追加消息。"
    report = dict(method="goutoujunshi", contact=app.contact, settings={"mode":"reply"},
                  scene="reply", stage="rapport", status="blocked", answer={}, notice=reason)
    monkeypatch.setattr("opendate.wechat_gui.generate_reply_with_strategy",
                        lambda *_: SimpleNamespace(text="", report=report, blocked=True))
    monkeypatch.setattr(app, "run", lambda work, done, **_: done(work()))
    notices = []
    monkeypatch.setattr("opendate.wechat_gui.messagebox.showinfo", lambda title, text, **_: notices.append(text))
    try:
        app.put(app.incoming, "你好")
        app.consent.set(True)
        app.generate_button.invoke()
        assert reason in app.strategy_summary.get()
        assert reason in app.coach_advice.get("1.0", "end")
        assert reason in app.status.get()
        assert reason in notices[0]
        assert not app.reply.get("1.0", "end").strip()
        assert not app.settings_window.winfo_ismapped()
    finally:
        app.closed = True


def test_auto_read_does_not_overwrite_drafts_or_accept_old_contact_results(root, tmp_path):
    app = make_app(root, tmp_path)
    try:
        app.auto_enabled.set(True)
        token = (app.auto_epoch, app.contact)
        selected = {"handle":123, "layout":"main"}
        app.consent.set(True)
        app.auto_read_done(token, (selected, "第一条来信", None))
        assert app.incoming.get("1.0", "end").strip() == "第一条来信"
        assert not app.consent.get()
        app.put(app.incoming, "正在手动编辑的来信")
        app.auto_read_done(token, (selected, "第二条来信", None))
        assert app.incoming.get("1.0", "end").strip() == "正在手动编辑的来信"
        assert app.pending_incoming == "第二条来信"
        app.use_pending_incoming()
        assert app.incoming.get("1.0", "end").strip() == "第二条来信"
        app.pending_incoming = "已过时的来信"
        app.auto_read_done(token, (selected, "", None))
        assert not app.pending_incoming
        assert not app.incoming.get("1.0", "end").strip()
        assert "disabled" in app.new_incoming_button.state()
        app.apply_auto_incoming("第二条来信")
        app.auto_epoch += 1
        app.auto_read_done(token, (selected, "旧对象的迟到结果", None))
        assert app.incoming.get("1.0", "end").strip() == "第二条来信"
        app.auto_read_done((app.auto_epoch, app.contact), (None, "", "窗口不明确"))
        assert not app.auto_enabled.get()
        assert "窗口不明确" in app.auto_status.get()
        assert app.memory.stats(app.contact)[0] == 0
    finally:
        app.closed = True


def test_auto_read_uses_explicit_window_choice(root, tmp_path, monkeypatch):
    app = make_app(root, tmp_path)
    calls = []
    app.window_choices = {"指定窗口": {"handle":456, "title":app.contact}}
    app.window_var.set("指定窗口")
    app.window_selected()
    monkeypatch.setattr("opendate.wechat_live.open_wechat",
                        lambda contact, **kw: calls.append(kw) or {"handle":kw["window_handle"], "layout":"main"})
    monkeypatch.setattr("opendate.wechat_live.read_incoming",
                        lambda contact, work, **kw: calls.append(kw) or "指定窗口的消息")
    class ImmediateThread:
        def __init__(self, target, **_):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr("opendate.wechat_gui.threading.Thread", ImmediateThread)
    try:
        app.start_auto_read(reconnect=True)
        assert calls[0] == {"window_handle":456}
        assert calls[1] == {"window_handle":456, "layout":"detached"}
    finally:
        app.closed = True


@pytest.mark.parametrize("scaling,width,height", [(4/3, 1200, 760), (2, 1800, 1140), (8/3, 2324, 1244)])
def test_long_settings_keep_editors_and_actions_accessible(root, tmp_path, scaling, width, height):
    root.tk.call("tk", "scaling", scaling)
    config = AppConfig(source="wechat", llm={"provider":"codex"}, wechat={
        "contact":"示例对象", "my_name":"本人", "allowed_contacts":["示例对象"]})
    save_settings(tmp_path, config, MyPersona(personality="自然直接。\n\n" * 30,
                                            voice="简洁幽默。\n\n" * 30), "示例对象", "本人")
    app = WeChatWindow(root, tmp_path)
    try:
        app.memory.save_brief("示例对象", ConversationBrief("", "", "约周末吃饭。\n\n" * 40))
        app.refresh()
        root.geometry(f"{width}x{height}")
        root.update()
        assert "\n" not in app.persona_text.get() + app.goal_text.get()
        assert app.incoming.winfo_height() >= 80
        assert app.reply_request.winfo_height() >= 60
        assert app.reply.winfo_height() >= 80
        for button in app.controls:
            if button.winfo_ismapped():
                assert button.winfo_height() >= button.winfo_reqheight()
        footer = app.reply_split.left.footer
        assert footer.winfo_rooty() + footer.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        checks = [w for w in footer.winfo_children() if w.winfo_class() == "TCheckbutton"]
        assert checks[0].winfo_height() >= checks[0].winfo_reqheight()
        app.put(app.incoming, "今天累了。")
        app.put(app.reply_request, "先关心她，简短一些。")
        assert "先关心她" in app.build_packet()["user_question"]
    finally:
        app.closed = True
