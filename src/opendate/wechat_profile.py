"""User-editable persona and explicit desktop contact selection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from .config import AppConfig
from .wechat_memory import HistoryError


@dataclass(frozen=True)
class MyPersona:
    personality: str = ""
    voice: str = "自然、简短、真诚，先回应对方说的事"
    avoid: str = ""
    examples: str = ""

    def validate(self) -> None:
        if any(not isinstance(v, str) for v in asdict(self).values()):
            raise HistoryError("人设的各项内容请填写文字。")
        if not self.voice.strip():
            raise HistoryError("请填写你希望使用的聊天风格。")
        if len(json.dumps(asdict(self), ensure_ascii=False)) > 6000:
            raise HistoryError("人设和聊天风格合计请控制在 6000 字以内。")

    def packet(self, my_name: str) -> dict:
        return {"display_name": my_name, **asdict(self)}


def profile_path(project: Path, config: AppConfig) -> Path:
    return project / config.data_dir / "my-profile.json"


def load_persona(project: Path, config: AppConfig) -> MyPersona:
    path = profile_path(project, config)
    if not path.is_file():
        return MyPersona(voice=config.preferences.voice)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        persona = MyPersona(**values)
        persona.validate()
        return persona
    except (ValueError, TypeError, HistoryError):
        raise HistoryError("本地人设配置无法读取，请保留 my-profile.json 并检查格式。") from None


def save_settings(project: Path, config: AppConfig, persona: MyPersona,
                  contact: str, my_name: str) -> AppConfig:
    """Validate before any write. Switching never moves or merges chat archives."""
    persona.validate()
    contact, my_name = contact.strip(), my_name.strip()
    if len(contact) > 100 or len(my_name) > 100:
        raise HistoryError("好友备注和自己的显示名请控制在 100 字以内。")
    data = config.model_dump(mode="json")
    data["wechat"].update(contact=contact, my_name=my_name, allowed_contacts=[contact])
    data["preferences"]["voice"] = persona.voice
    data["auto_send"] = False
    updated = AppConfig.model_validate(data)
    updated.wechat.validate_identity()
    profile = profile_path(project, updated)
    profile.parent.mkdir(parents=True, exist_ok=True)
    config_file = project / "config.wechat.yaml"
    # Both writes are atomic at file level; configuration holds no API secrets.
    profile_tmp = profile.with_suffix(".json.tmp")
    config_tmp = config_file.with_suffix(".yaml.tmp")
    profile_text = json.dumps(asdict(persona), ensure_ascii=False, indent=2)
    config_text = yaml.safe_dump(updated.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
    profile_tmp.write_text(profile_text, encoding="utf-8")
    config_tmp.write_text(config_text, encoding="utf-8")
    profile_tmp.replace(profile)
    config_tmp.replace(config_file)
    return updated
