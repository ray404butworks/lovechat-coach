import json
import tkinter as tk
from types import SimpleNamespace

import pytest

from opendate.config import AppConfig
from opendate.wechat_gui import WeChatWindow
from opendate.wechat_profile import MyPersona, save_settings
from opendate.wechat_theme import ReplySplit


@pytest.fixture
def root():
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
        assert len(app.tabs.tabs()) == 7
        assert app.incoming.cget("background") == app.theme.colors["field"]
        app.theme_choice.set("暖纸")
        app.change_theme()
        assert json.loads(app.theme_path.read_text())["mode"] == "light"
    finally:
        app.closed = True
