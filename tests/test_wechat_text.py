import json
import os
import tkinter as tk

import pytest

from opendate.config import AppConfig
from opendate.wechat_memory import ChatMemory, HistoryError, read_history
from opendate.wechat_profile import MyPersona, save_settings
from opendate.wechat_text import TxtIdentityError


def transcript(my="本人", peer="好友", last="第三条"):
    return (f"{peer}\n2026年10月19日 21:22\n第一条\n\n\n"
            f"{my}\n2026年10月19日 21:23\n第二条\n第二行\n\n\n"
            f"{peer}\n2026年10月19日 21:23\n{last}")


def write(tmp_path, content, encoding="utf-8", name="chat.txt"):
    path = tmp_path / name
    path.write_text(content, encoding=encoding)
    return path


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_txt_decoding_roles_multiline_and_china_timezone(tmp_path, encoding):
    batch, = read_history(write(tmp_path, transcript(), encoding), "好友", "本人")
    assert batch.sender_names == ("本人", "好友")
    assert [r.sender for r in batch.records] == ["peer", "me", "peer"]
    assert [r.text for r in batch.records] == ["第一条", "第二条\n第二行", "第三条"]
    assert batch.records[0].timestamp == "2026-10-19T13:22:00.000000+00:00"
    assert batch.records[1].timestamp == batch.records[2].timestamp


def test_quoted_sender_and_date_are_kept_as_message_text(tmp_path):
    quote = "回复：           好友\n2026年10月19日 21:22\n引用的内容\n这是我的回复"
    batch, = read_history(write(tmp_path, transcript(last=quote)), "好友", "本人")
    assert len(batch.records) == 3
    assert batch.records[-1].text == quote


def test_message_headers_without_blank_separators_are_not_merged(tmp_path):
    content = "好友\n2026年10月19日 21:22\n正文\n本人\n2026年10月19日 21:23\n另条"
    batch, = read_history(write(tmp_path, content), "好友", "本人")
    assert [(r.sender, r.text) for r in batch.records] == [("peer", "正文"), ("me", "另条")]


def test_mixed_separators_and_prefixed_quotes_preserve_all_messages(tmp_path):
    quote = "回复：好友\n2026年10月19日 21:22\n引用正文"
    content = transcript(last=quote) + "\n本人\n2026年10月19日 21:24\n新消息"
    batch, = read_history(write(tmp_path, content), "好友", "本人")
    assert len(batch.records) == 4
    assert batch.records[2].text == quote
    assert batch.records[3].sender == "me"
    assert batch.records[3].text == "新消息"


def test_nickname_mapping_requires_explicit_ownership_and_survives_sync(tmp_path):
    path = write(tmp_path, transcript("源本人", "源好友"))
    with pytest.raises(TxtIdentityError) as exc:
        read_history(path, "对象备注", "本人设置")
    assert set(exc.value.participants) == {"源本人", "源好友"}
    batch, = read_history(path, "对象备注", "本人设置", txt_senders=("源本人", "源好友"))
    memory = ChatMemory(tmp_path / "memory.sqlite3")
    assert memory.import_batch("对象备注", batch, str(path)) == 3
    memory.bind("对象备注", path, batch.conversation, batch.sender_names)
    # New process / reopened memory must keep the human-confirmed sender mapping.
    reopened = ChatMemory(memory.path)
    path.write_text(transcript("源本人", "源好友")+"\n\n源本人\n2026-10-20 09:30:15\n新的一条", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns+1_000_000))
    assert reopened.sync("对象备注", "本人设置") == 1
    assert reopened.recent("对象备注")[-1].sender == "me"
    assert reopened.stats("另一个对象")[0] == 0
    assert reopened.sync("对象备注", "本人设置") == 0
    path.write_text(transcript("新昵称", "源好友"), encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns+1_000_000))
    with pytest.raises(HistoryError):
        reopened.sync("对象备注", "本人设置")
    assert reopened.stats("对象备注")[0] == 4


def test_txt_and_preconverted_json_share_dedup_keys_and_same_minute_order(tmp_path):
    content = transcript(last="第一条") + "\n\n好友\n2026年10月19日 21:23\n第一条"
    batch, = read_history(write(tmp_path, content), "好友", "本人")
    path = write(tmp_path, json.dumps({"contact":"好友", "messages":[
        {"timestamp":r.timestamp, "sender":r.sender, "text":r.text} for r in batch.records]},
        ensure_ascii=False), name="converted.json")
    json_batch, = read_history(path, "好友", "本人")
    assert batch.records == json_batch.records
    memory = ChatMemory(tmp_path / "messages.sqlite3")
    assert memory.import_batch("好友", json_batch, "converted") == 4
    assert memory.import_batch("好友", batch, "txt") == 0
    assert [(r.sender,r.text) for r in memory.recent("好友")] == [(r.sender,r.text) for r in batch.records]


@pytest.mark.parametrize("content,match", [
    ("只有正文没有时间和昵称", "未识别"),
    ("不能静默丢弃的开头\n"+transcript(), "开头"),
    ("好友\n2026年13月19日 21:22\n正文", "无效"),
    ("好友\n2026年10月19日 21:22\n\n本人\n2026年10月19日 21:23\n正文", "没有正文"),
    (transcript()+"\n\n本人\n2026年10月19日 21:2\n不应悄悄并入上一条", "格式无效"),
    (transcript()+"\n\n第三人\n2026年10月19日 21:24\n群聊", "超过两位"),
])
def test_invalid_or_mixed_txt_is_rejected_without_partial_import(tmp_path, content, match):
    with pytest.raises(HistoryError, match=match):
        read_history(write(tmp_path, content), "好友", "本人")


def test_invalid_mapping_and_binary_content_are_rejected(tmp_path):
    path = write(tmp_path, transcript())
    with pytest.raises(HistoryError, match="对应关系"):
        read_history(path, "备注", "本人", txt_senders=("本人", "本人"))
    path.write_bytes(b"\x00\x00\x00\x00")
    with pytest.raises(HistoryError, match="编码"):
        read_history(path, "好友", "本人")


def test_gui_import_filter_and_txt_dispatch_use_real_reader_without_touching_formal_data(tmp_path, monkeypatch):
    from opendate.wechat_gui import WeChatWindow
    config = AppConfig(source="wechat", llm={"provider":"codex"}, wechat={
        "contact":"好友", "my_name":"本人", "allowed_contacts":["好友"]})
    save_settings(tmp_path, config, MyPersona(), "好友", "本人")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No native display")
    root.withdraw()
    app = WeChatWindow(root, tmp_path)
    path = write(tmp_path, transcript())
    selected = []
    previews = []
    def choose(**kw):
        selected.append(kw)
        return str(path)
    monkeypatch.setattr("opendate.wechat_gui.filedialog.askopenfilename", choose)
    monkeypatch.setattr(app, "run", lambda work, done: done(work()))
    monkeypatch.setattr(app, "confirm_import", lambda p, batches: previews.append((p,batches)))
    try:
        app.import_history()
        assert "*.txt" in str(selected[0]["filetypes"])
        assert previews[0][0] == path and len(previews[0][1][0].records) == 3
        assert app.memory.stats("好友")[0] == 0  # User must still confirm the preview.
        mapped = []
        monkeypatch.setattr(app, "map_txt_identity", lambda p, names: mapped.append((p,names)))
        path.write_text(transcript("源本人", "源好友"),encoding="utf-8")
        app.import_history()
        assert mapped[0][0] == path
        assert set(mapped[0][1]) == {"源本人", "源好友"}
    finally:
        app.closed = True
        root.destroy()


def test_gui_nickname_choices_preview_confirmation_and_txt_binding(tmp_path, monkeypatch):
    from tkinter import ttk

    from opendate.wechat_gui import WeChatWindow

    config = AppConfig(source="wechat", llm={"provider":"codex"}, wechat={
        "contact":"对象备注", "my_name":"本人设置", "allowed_contacts":["对象备注"]})
    save_settings(tmp_path, config, MyPersona(), "对象备注", "本人设置")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No native display")
    root.withdraw()
    app = WeChatWindow(root, tmp_path)
    path = write(tmp_path, transcript("源本人", "源好友"))
    monkeypatch.setattr(app, "run", lambda work, done: done(work()))

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    try:
        app.load_import(path)
        mapping, = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        choices = [w for w in descendants(mapping) if isinstance(w, ttk.Combobox)]
        assert [w.get() for w in choices] == ["", ""]
        choices[0].set("源本人")
        choices[1].set("源好友")
        next(w for w in descendants(mapping) if isinstance(w, ttk.Button)
             and w.cget("text") == "确认对应，继续预览").invoke()
        preview, = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        shown = "\n".join(w.get("1.0", "end") for w in descendants(preview) if isinstance(w, tk.Text))
        assert "我 1 条，对方 2 条" in shown and "时间范围" in shown
        assert app.memory.stats("对象备注")[0] == 0
        next(w for w in descendants(preview) if isinstance(w, ttk.Button)
             and w.cget("text") == "确认身份正确，导入并绑定").invoke()
        assert app.memory.stats("对象备注")[0] == 3
        assert app.memory.source("对象备注")[0] == str(path.resolve())
        assert [r.sender for r in app.memory.recent("对象备注")] == ["peer", "me", "peer"]
        with app.memory.connect() as db:
            assert db.execute("SELECT my_sender, peer_sender FROM source_identities "
                              "WHERE contact=?", ("对象备注",)).fetchone() == ("源本人", "源好友")
    finally:
        app.closed = True
        root.destroy()
