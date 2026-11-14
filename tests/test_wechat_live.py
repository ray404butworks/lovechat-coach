from PIL import Image, ImageDraw
import pytest

from opendate.connectors.wechat import WeChatError
from opendate.wechat_live import extract_incoming, read_incoming


def line(text, x, y):
    return {"text": text, "words": [{"text":text, "x":x, "y":y, "width":100, "height":18}]}


def test_extracts_only_incoming_bubbles_after_latest_outgoing():
    image = Image.new("RGB", (700, 400), (238, 238, 238))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 20, 270, 75), fill="white")
    draw.rectangle((450, 100, 650, 155), fill=(149, 236, 105))
    draw.rectangle((40, 200, 270, 260), fill="white")
    draw.rectangle((40, 290, 270, 350), fill="white")
    result = extract_incoming(image, [line("旧来信", 60, 35), line("我发出的消息", 470, 115),
                                      line("新来信一", 60, 215), line("新来信二", 60, 305)])
    assert result == "新来信一\n\n新来信二"


def test_latest_outgoing_does_not_reuse_old_peer_message():
    image = Image.new("RGB", (700, 300), (238, 238, 238))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 20, 270, 75), fill="white")
    draw.rectangle((450, 160, 650, 220), fill=(149, 236, 105))
    assert extract_incoming(image, [line("旧来信", 60, 35)]) == ""


def test_unknown_theme_stops_instead_of_guessing_roles():
    with pytest.raises(WeChatError, match="无法可靠区分"):
        extract_incoming(Image.new("RGB", (700, 400), "#242830"), [])


def test_auto_read_checks_title_before_any_message_ocr(monkeypatch, tmp_path):
    image = Image.new("RGB", (1000, 800), (238, 238, 238))
    monkeypatch.setattr("opendate.wechat_live.capture_wechat_window", lambda _: (image, 1))
    calls = []
    def recognize(*args):
        calls.append(args)
        return [line("其他对象", 20, 10)]
    monkeypatch.setattr("opendate.wechat_live.recognize_local", recognize)
    with pytest.raises(WeChatError, match="当前私聊与所选对象不一致"):
        read_incoming("指定好友", tmp_path, window_handle=123)
    assert len(calls) == 1
