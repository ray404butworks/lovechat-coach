"""Local OCR scope checks use synthetic images and injected recognition only."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from opendate.connectors.wechat import WeChatError
from opendate.connectors.wechat_ocr import inspect_local_ocr, recognize_local


class FakeImage:
    size = (1000, 800)

    def __init__(self):
        self.crops = []

    def crop(self, bounds):
        self.crops.append(bounds)
        return self


def test_ocr_header_must_match_before_body(monkeypatch, tmp_path):
    image = FakeImage()
    monkeypatch.setattr("opendate.connectors.wechat_ocr.capture_wechat_window", lambda: (image, 1))
    calls = []

    def recognize(*args):
        calls.append(args)
        return [{"text": "其他好友", "words": [{"x": 80}]}]

    monkeypatch.setattr("opendate.connectors.wechat_ocr.recognize_local", recognize)
    with pytest.raises(WeChatError, match="未识别聊天正文"):
        inspect_local_ocr("指定好友", tmp_path)
    assert len(calls) == 1
    assert len(image.crops) == 1


def test_ocr_outputs_only_statistics_and_crops_chat(monkeypatch, tmp_path):
    image = FakeImage()
    monkeypatch.setattr("opendate.connectors.wechat_ocr.capture_wechat_window", lambda: (image, 1))
    results = iter(
        [
            [{"text": "指 定 好 友", "words": [{"x": 80, "y": 5, "height": 20}]}],
            [{"text": "PRIVATE CHAT TEXT", "words": []}],
        ]
    )
    monkeypatch.setattr("opendate.connectors.wechat_ocr.recognize_local", lambda *_: next(results))
    result = inspect_local_ocr("指定好友", tmp_path)
    assert result["target_title_verified"]
    assert result["visible_text_line_count"] == 1
    assert "PRIVATE" not in str(result)
    assert result["messages_sent"] == 0
    assert not result["model_called"]
    assert not result["parsed_messages"]
    assert image.crops[1][0] == 310


@pytest.mark.parametrize("returncode,stdout", [(0, '{"lines": []}'), (1, "PRIVATE TEXT")])
def test_temporary_screenshot_removed_on_success_and_error(
    monkeypatch, tmp_path, returncode, stdout
):
    image = SimpleNamespace(save=lambda path: Path(path).write_bytes(b"fake png"))
    monkeypatch.setattr(
        "opendate.connectors.wechat_ocr.subprocess.run",
        lambda *_, **__: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    if returncode:
        with pytest.raises(WeChatError) as error:
            recognize_local(image, tmp_path)
        assert "PRIVATE" not in str(error.value)
    else:
        assert recognize_local(image, tmp_path) == []
    assert list(tmp_path.glob("*.png")) == []


@pytest.mark.parametrize("selection", ["0", "9", "2,1", "1,1", "not-a-number"])
def test_local_selection_rejects_invalid_lines(selection):
    from opendate.connectors.wechat_ocr import select_ocr_message

    with pytest.raises(WeChatError):
        select_ocr_message(["第一行", "第二行"], selection)


def test_local_selection_only_uses_chosen_lines():
    from opendate.connectors.wechat_ocr import select_ocr_message

    assert select_ocr_message(["不应上传", "你 好", "最 近 如 何"], "2,3") == "你好最近如何"


def test_reviewed_driver_can_never_send():
    from opendate.connectors.wechat_ocr import ReviewedOCRDriver

    driver = ReviewedOCRDriver("指定好友", "本人昵称", "已复核来信")
    assert driver.read("指定好友", "本人昵称", 12)[0]["sender"] == "指定好友"
    with pytest.raises(WeChatError):
        driver.read("其他好友", "本人昵称", 12)
    with pytest.raises(WeChatError):
        driver.send("指定好友", "本人昵称", "任意文字")


def test_explicit_detached_window_still_verifies_contact_before_reading(monkeypatch, tmp_path):
    from opendate.connectors.wechat_ocr import read_local_ocr_lines
    image = FakeImage()
    captured = []
    def capture(handle):
        captured.append(handle)
        return image, 1
    monkeypatch.setattr("opendate.connectors.wechat_ocr.capture_wechat_window", capture)
    results = iter([[{"text": "指定好友", "words": [{"x": 300, "y": 5, "height": 20}]}],
                    [{"text": "已核对窗口中的文字"}]])
    monkeypatch.setattr("opendate.connectors.wechat_ocr.recognize_local", lambda *_: next(results))
    assert read_local_ocr_lines("指定好友", tmp_path, window_handle=123, layout="detached") == ["已核对窗口中的文字"]
    assert captured == [123]
    assert image.crops[1][0] == 20


def test_custom_contact_keeps_meaningful_english_spaces(monkeypatch, tmp_path):
    from opendate.connectors.wechat_ocr import read_local_ocr_lines
    image = FakeImage()
    monkeypatch.setattr("opendate.connectors.wechat_ocr.capture_wechat_window", lambda: (image, 1))
    results = iter([[{"text": "Alice Chen", "words": [{"x": 80, "y": 5, "height": 20}]}],
                    [{"text": "hello"}]])
    monkeypatch.setattr("opendate.connectors.wechat_ocr.recognize_local", lambda *_: next(results))
    assert read_local_ocr_lines("Alice Chen", tmp_path) == ["hello"]


@pytest.mark.parametrize("approve", [False, True])
def test_draft_ocr_requires_local_review_and_uploads_only_selection(
    monkeypatch, tmp_path, stub_router, approve
):
    from typer.testing import CliRunner
    from opendate.cli import app

    config = tmp_path / "live.yaml"
    config.write_text(
        "source: wechat\nwechat:\n  allowed_contacts: [指定好友]\n  contact: 指定好友\n"
        "  my_name: 本人昵称\n  pyweixin_scripts: unused\n  allow_llm_upload: true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    prompts = []
    original_responder = stub_router.backend.responder

    def respond(messages):
        prompts.extend(message["content"] for message in messages)
        return original_responder(messages)

    stub_router.backend.responder = respond
    monkeypatch.setattr("opendate.cli._build_router", lambda *_: (stub_router, None))
    monkeypatch.setattr(
        "opendate.connectors.wechat_ocr.read_local_ocr_lines",
        lambda *_: [
            "UNSELECTED PRIVATE",
            "你最近有没有什么有趣的事情呀？",
        ],
    )
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *_, **__: "2")
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *_, **__: approve)
    monkeypatch.setattr("opendate.cli.build_connector", lambda *_: pytest.fail("no live sender"))
    result = CliRunner().invoke(app, ["--config", str(config), "wechat", "draft-ocr"])
    assert result.exit_code == 0, result.output
    assert "UNSELECTED PRIVATE" not in " ".join(prompts)
    assert bool(stub_router.usage["calls"]) == approve
    if approve:
        assert "你最近有没有什么有趣的事情呀？" in " ".join(prompts)
        assert "本轮未向微信发送" in result.output
    else:
        assert not (tmp_path / "data").exists()
