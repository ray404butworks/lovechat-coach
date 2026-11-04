"""WeChat contract and full-loop tests. Never import/use real desktop APIs."""

import io
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from rich.console import Console
from typer.testing import CliRunner

from opendate.cli import app
from opendate.config import AppConfig, WeChatConfig
from opendate.connectors.base import MatchSource, build_connector
from opendate.connectors.wechat import PyWeixinDriver, WeChatConnector, WeChatError
from opendate.orchestrator.loop import Orchestrator, build_situation
from opendate.orchestrator.quality import critique_message
from opendate.orchestrator.safety import SafetyGuard
from opendate.orchestrator.state import ConversationStore


class FakeDriver:
    def __init__(self):
        self.rows = [{"sender": "指定好友", "text": "你最近在忙什么呢？", "type": "text"}]
        self.reads = []
        self.sent = []
        self.error = False

    def read(self, contact, my_name, limit):
        self.reads.append((contact, my_name, limit))
        if self.error:
            raise RuntimeError("PRIVATE PAYLOAD MUST NOT BE SHOWN")
        return [dict(row) for row in self.rows[-limit:]]

    def send(self, contact, my_name, text):
        self.sent.append((contact, text))
        if self.error:
            raise RuntimeError("uncertain result")
        self.rows.append({"sender": my_name, "text": text, "type": "text"})


@pytest.fixture
def wx():
    config = WeChatConfig(
        allowed_contacts=["指定好友", "另一位白名单好友"],
        contact="指定好友",
        my_name="本人显示名",
    )
    return WeChatConnector(config, FakeDriver(), send_enabled=True)


def test_config_rejects_auto_send_and_bad_scope():
    with pytest.raises(ValidationError, match="auto_send"):
        AppConfig(source="wechat", auto_send=True)
    for contacts in (["重复", "重复"], [" "], [" 前空格"]):
        with pytest.raises(ValidationError):
            WeChatConfig(allowed_contacts=contacts)
    with pytest.raises(ValueError, match="白名单"):
        WeChatConfig().validate_live()
    with pytest.raises(ValueError, match="歧义"):
        WeChatConfig(
            allowed_contacts=["Ray Li"], contact="Ray Li", my_name="Ray"
        ).validate_identity()


async def test_single_contact_and_no_discovery(wx):
    assert isinstance(wx, MatchSource)
    assert await wx.get_recommendations() == []
    assert await wx.get_matches(count=0) == []
    matches = await wx.get_matches()
    assert [m.name for m in matches] == ["指定好友"]
    assert len(wx.driver.reads) == 1
    assert matches[0].messages[0].sender == "them"
    assert matches[0].messages[0].sent_at is None
    with pytest.raises(WeChatError):
        await wx.get_messages("另一位白名单好友")
    with pytest.raises(WeChatError):
        await wx.like("指定好友")
    assert len(wx.driver.reads) == 1


async def test_explicit_confirm_and_single_use(wx):
    await wx.get_matches()
    with pytest.raises(WeChatError, match="人工确认"):
        await wx.send_message("指定好友", "最近在整理一些东西，你呢？")
    text = "最近在整理一些东西，你呢？"
    wx.approve_message("指定好友", text)
    result = await wx.send_message("指定好友", text)
    assert result.from_me
    assert wx.driver.sent == [("指定好友", text)]
    with pytest.raises(WeChatError):
        await wx.send_message("指定好友", text)
    assert len(wx.driver.sent) == 1


async def test_changed_text_needs_new_approval(wx):
    await wx.get_matches()
    wx.approve_message("指定好友", "原文")
    with pytest.raises(WeChatError):
        await wx.send_message("指定好友", "修改后的文字")
    assert wx.driver.sent == []


async def test_stale_snapshot_is_cancelled(wx):
    await wx.get_matches()
    wx.approve_message("指定好友", "回复建议")
    wx.driver.rows.append({"sender": "指定好友", "text": "先别回复", "type": "text"})
    with pytest.raises(WeChatError, match="聊天已变化"):
        await wx.send_message("指定好友", "回复建议")
    assert wx.driver.sent == []
    with pytest.raises(WeChatError):
        await wx.send_message("指定好友", "回复建议")


async def test_suggestion_mode_has_no_send_even_with_approval(wx):
    wx.send_enabled = False
    await wx.get_matches()
    with pytest.raises(WeChatError):
        wx.approve_message("指定好友", "回复")
    with pytest.raises(WeChatError):
        await wx.send_message("指定好友", "回复")
    assert wx.driver.sent == []


async def test_failed_read_never_retries_or_leaks_payload(wx):
    wx.driver.error = True
    with pytest.raises(WeChatError) as error:
        await wx.get_matches()
    assert "PRIVATE" not in str(error.value)
    with pytest.raises(WeChatError):
        await wx.get_matches()
    assert len(wx.driver.reads) == 1


async def test_uncertain_send_leaves_persistent_lock(wx, tmp_path):
    wx.pending_path = tmp_path / "pending.json"
    await wx.get_matches()
    wx.approve_message("指定好友", "测试回复")
    original_read = wx.driver.read

    def good_read_then_fail(*args):
        rows = original_read(*args)
        wx.driver.error = True
        return rows

    wx.driver.read = good_read_then_fail
    with pytest.raises(WeChatError, match="不会重试"):
        await wx.send_message("指定好友", "测试回复")
    assert wx.pending_path.exists()
    assert "测试回复" not in wx.pending_path.read_text()
    restarted = WeChatConnector(
        wx.config, FakeDriver(), send_enabled=True, pending_path=wx.pending_path
    )
    await restarted.get_matches()
    restarted.approve_message("指定好友", "测试回复")
    with pytest.raises(WeChatError, match="上次未确认"):
        await restarted.send_message("指定好友", "测试回复")
    assert restarted.driver.sent == []


@pytest.mark.parametrize("text", ["", " " * 3, "字" * 501, "带制表\t内容"])
async def test_send_text_limits(wx, text):
    await wx.get_matches()
    wx.approve_message("指定好友", text)
    with pytest.raises(WeChatError):
        await wx.send_message("指定好友", text)
    assert wx.driver.sent == []


def make_loop(wx, stub_router, skills_engine, persona, tmp_path, **kwargs):
    config = AppConfig(source="wechat", data_dir=str(tmp_path), max_screen_per_cycle=0)
    return Orchestrator(
        connector=wx,
        router=stub_router,
        skills=skills_engine,
        persona=persona,
        config=config,
        console=Console(file=io.StringIO(), markup=False),
        **kwargs,
    )


@pytest.mark.parametrize(
    "approved,interactive,sent", [(True, True, 1), (False, True, 0), (True, False, 0)]
)
async def test_full_loop_confirm(
    wx, stub_router, skills_engine, persona, tmp_path, approved, interactive, sent
):
    loop = make_loop(
        wx,
        stub_router,
        skills_engine,
        persona,
        tmp_path,
        confirm=lambda _: approved,
        interactive=interactive,
    )
    actions = await loop.run_once()
    assert len(wx.driver.sent) == sent
    assert actions[0].skill == "wechat-reply"
    assert "咖啡" in actions[0].text  # deterministic Chinese stub
    assert actions[0].sent == bool(sent)
    assert loop.store.get("指定好友").sent_count == sent


async def test_full_loop_suggestions_do_not_prompt(
    wx, stub_router, skills_engine, persona, tmp_path
):
    wx.send_enabled = False
    loop = make_loop(
        wx,
        stub_router,
        skills_engine,
        persona,
        tmp_path,
        confirm=lambda _: pytest.fail("suggestions must not prompt to send"),
    )
    assert not (await loop.run_once())[0].sent
    assert not wx.driver.sent


@pytest.mark.parametrize("message", ["别再给我发消息了", "我今年17岁", "这样让我不舒服"])
async def test_chinese_stop_before_llm(wx, stub_router, skills_engine, persona, tmp_path, message):
    wx.driver.rows = [
        {"sender": "指定好友", "text": message, "type": "text"},
        {"sender": "指定好友", "text": "还有一句普通消息", "type": "text"},
    ]
    loop = make_loop(wx, stub_router, skills_engine, persona, tmp_path, confirm=lambda _: True)
    actions = await loop.run_once()
    assert actions[0].blocked
    assert not wx.driver.sent
    assert stub_router.usage["calls"] == 0


@pytest.mark.parametrize(
    "row",
    [
        {"sender": "本人显示名", "text": "上一条是我发的", "type": "text"},
        {"sender": "指定好友", "text": "", "type": "unsupported"},
    ],
)
async def test_no_followup_or_media_guess(wx, stub_router, skills_engine, persona, tmp_path, row):
    wx.driver.rows = [row]
    loop = make_loop(wx, stub_router, skills_engine, persona, tmp_path, confirm=lambda _: True)
    assert (await loop.run_once())[0].kind == "skip"
    assert not wx.driver.sent
    assert stub_router.usage["calls"] == 0


async def test_pacing_survives_restart(wx, stub_router, skills_engine, persona, tmp_path):
    store_path = tmp_path / "state.json"
    loop = make_loop(
        wx,
        stub_router,
        skills_engine,
        persona,
        tmp_path,
        confirm=lambda _: True,
        store=ConversationStore(store_path),
    )
    await loop.run_once()
    wx.driver.rows.append({"sender": "指定好友", "text": "我又回复了一条新消息", "type": "text"})
    loop = make_loop(
        wx,
        stub_router,
        skills_engine,
        persona,
        tmp_path,
        confirm=lambda _: True,
        store=ConversationStore(store_path),
    )
    actions = await loop.run_once()
    assert actions[0].kind == "skip"
    assert len(wx.driver.sent) == 1


async def test_success_clears_marker_only_after_state_save(
    wx, stub_router, skills_engine, persona, tmp_path
):
    wx.pending_path = tmp_path / "pending.json"
    store = ConversationStore(tmp_path / "state.json")
    original_save = store.save

    def save_while_locked():
        assert wx.pending_path.exists()
        original_save()

    store.save = save_while_locked
    loop = make_loop(
        wx, stub_router, skills_engine, persona, tmp_path, confirm=lambda _: True, store=store
    )
    # _execute_send saves synchronously before acknowledging the marker.
    await loop._handle_match(
        (await wx.get_matches())[0],
        build_situation((await wx.get_matches())[0]),
        store.get("指定好友"),
        loop._clock(),
    )
    assert not wx.pending_path.exists()
    assert ConversationStore(store.path).get("指定好友").sent_count == 1


async def test_failed_state_save_keeps_recovery_marker(
    wx, stub_router, skills_engine, persona, tmp_path
):
    wx.pending_path = tmp_path / "pending.json"
    store = ConversationStore(tmp_path / "state.json")

    def fail_save():
        raise OSError("disk full")

    store.save = fail_save
    loop = make_loop(
        wx, stub_router, skills_engine, persona, tmp_path, confirm=lambda _: True, store=store
    )
    with pytest.raises(OSError):
        await loop.run_once()
    assert wx.pending_path.exists()
    assert len(wx.driver.sent) == 1


async def test_empty_chat_is_read_once(wx, stub_router, skills_engine, persona, tmp_path):
    wx.driver.rows = []
    loop = make_loop(wx, stub_router, skills_engine, persona, tmp_path)
    assert (await loop.run_once())[0].kind == "skip"
    assert len(wx.driver.reads) == 1


def test_library_also_requires_upload_consent(wx, skills_engine, persona, tmp_path):
    with pytest.raises(ValueError, match="allow_llm_upload"):
        make_loop(wx, SimpleNamespace(is_stub=False), skills_engine, persona, tmp_path)


def test_chinese_safety_and_question_units():
    guard = SafetyGuard(AppConfig().safety)
    for text in ("你必须回复我", "发裸照给我", "假装我是医生", "你这个贱人"):
        assert guard.check_message(text).blocked
    assert critique_message("你想找哪一片的咖啡店？", skill="rapport-building").score == 1


def test_driver_reverses_without_reading_unrelated_chats(monkeypatch):
    driver = PyWeixinDriver("unused")
    window = SimpleNamespace(child_window=lambda **_: SimpleNamespace(children=lambda **_: [1]))
    ui = SimpleNamespace(Lists=SimpleNamespace(FriendChatList={}))
    utils = SimpleNamespace(
        traverse_message=lambda *_, **__: [
            ("指定好友 最新消息", "mmui::ChatTextItemView"),
            ("本人显示名 较早消息", "mmui::ChatTextItemView"),
        ]
    )
    monkeypatch.setattr(driver, "_load", lambda: (ui, utils, None))
    monkeypatch.setattr(driver, "_window", lambda _: window)
    rows = driver.read("指定好友", "本人显示名", 12)
    assert [r["text"] for r in rows] == ["较早消息", "最新消息"]


@pytest.mark.parametrize("group,title", [(True, "指定好友"), (False, "无关好友")])
def test_driver_rejects_group_and_wrong_title_before_message_read(monkeypatch, group, title):
    import sys

    window = SimpleNamespace(
        class_name=lambda: "mmui::MainWindow",
        child_window=lambda **_: SimpleNamespace(window_text=lambda: title),
    )
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(FindWindow=lambda *_: 123))
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(
            Desktop=lambda **_: SimpleNamespace(window=lambda **_: window),
        ),
    )
    driver = PyWeixinDriver("unused")
    monkeypatch.setattr(
        driver,
        "_load",
        lambda: (
            SimpleNamespace(Texts=SimpleNamespace(CurrentChatNameText={})),
            None,
            SimpleNamespace(is_group_chat=lambda _: group),
        ),
    )
    with pytest.raises(WeChatError):
        driver._window("指定好友")


def test_mock_never_constructs_live_driver(monkeypatch):
    monkeypatch.setattr(
        "opendate.connectors.wechat.PyWeixinDriver",
        lambda *_: pytest.fail("mock cannot attach desktop"),
    )
    connector = build_connector(AppConfig(source="wechat"), force_mock=True)
    assert connector.config.contact == "演示好友"
    assert not connector.send_enabled


def test_cli_mock_and_disallowed_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "demo.yaml"
    config.write_text("source: wechat\ndata_dir: data/wechat-demo\n", encoding="utf-8")
    runner = CliRunner()
    args = ["--config", str(config), "--mock", "run"]
    result = runner.invoke(app, [*args, "--no-interactive"])
    assert result.exit_code == 0, result.output
    assert "咖啡" in result.output
    assert "nothing sent" in result.output
    for extra in (["--auto-send"], ["--wechat-send"], ["--cycles", "0"]):
        assert runner.invoke(app, [*args, *extra]).exit_code != 0


def test_cli_requires_upload_opt_in_before_any_model_or_ui(tmp_path, monkeypatch):
    config = tmp_path / "live.yaml"
    config.write_text(
        "source: wechat\nwechat:\n  allowed_contacts: [指定好友]\n  contact: 指定好友\n"
        "  my_name: 本人显示名\n  pyweixin_scripts: unused\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "opendate.cli.LLMRouter.from_config", lambda *_, **__: pytest.fail("no upload")
    )
    result = CliRunner().invoke(app, ["--config", str(config), "run", "--no-interactive"])
    assert result.exit_code != 0
    assert "allow_llm_upload" in result.output


def test_chinese_sentence_is_not_mistaken_for_one_word():
    from opendate.connectors.base import Match, Message

    match = Match(
        id="m",
        messages=[
            Message(
                id="1",
                match_id="m",
                sender="them",
                text="你最近有没有什么有趣的事情呀？",
            )
        ],
    )
    situation = build_situation(match)
    assert situation.their_msg_words > 2
    assert situation.their_asked_question


def test_live_inspection_never_uses_model_sends_or_saves(wx, tmp_path, monkeypatch):
    config = tmp_path / "live.yaml"
    config.write_text(
        "source: wechat\nwechat:\n  allowed_contacts: [指定好友]\n  contact: 指定好友\n"
        "  my_name: 本人显示名\n  pyweixin_scripts: unused\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("opendate.cli.build_connector", lambda *_: wx)
    monkeypatch.setattr("opendate.cli._build_router", lambda *_: pytest.fail("no model"))
    result = CliRunner().invoke(app, ["--config", str(config), "wechat", "inspect"])
    assert result.exit_code == 0, result.output
    assert '"message_count": 1' in result.output
    assert '"model_called": false' in result.output
    assert "你最近在忙什么" not in result.output
    assert not (tmp_path / "data").exists()
    assert wx.driver.sent == []


def test_inspection_rejects_mock(tmp_path):
    config = tmp_path / "demo.yaml"
    config.write_text("source: wechat", encoding="utf-8")
    result = CliRunner().invoke(app, ["--config", str(config), "--mock", "wechat", "inspect"])
    assert result.exit_code != 0
