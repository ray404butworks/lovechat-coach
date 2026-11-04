"""Codex CLI routing: no shell interpolation, credential copying, or real calls."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from opendate.config import LLMConfig
from opendate.llm.codex_cli import CodexCLIBackend, CodexCLIError
from opendate.llm.providers import get_provider, resolve_model
from opendate.llm.router import LLMRouter


def agent_event(text):
    return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})


def test_codex_resolves_without_any_api_key():
    resolved = resolve_model("codex", None, {})
    assert resolved.provider == "codex"
    assert resolved.model == "default"
    assert resolved.api_key is None
    assert get_provider("codex").env_keys() == []
    router = LLMRouter.from_config(LLMConfig(provider="codex"), {})
    assert isinstance(router.backend, CodexCLIBackend)
    assert router.max_retries == 1
    assert router.prefers_single_call


def test_resolves_desktop_install_when_explorer_path_has_no_codex(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENDATE_CODEX_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("opendate.llm.codex_cli.shutil.which", lambda *_: None)
    installed = tmp_path / "OpenAI" / "Codex" / "bin" / "installed-version" / "codex.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"test fixture only")
    assert CodexCLIBackend.executable() == str(installed.resolve())


def test_explicit_cli_path_and_missing_install_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENDATE_CODEX_PATH", str(tmp_path / "missing.exe"))
    with pytest.raises(CodexCLIError, match="指向的 Codex 程序不存在"):
        CodexCLIBackend.executable()
    monkeypatch.delenv("OPENDATE_CODEX_PATH")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("opendate.llm.codex_cli.shutil.which", lambda *_: None)
    with pytest.raises(CodexCLIError, match="未找到本机 Codex 程序"):
        CodexCLIBackend.executable()


def test_codex_startup_diagnostic_is_not_masked_as_upload_error(monkeypatch, tmp_path):
    from opendate.cli import app
    from typer.testing import CliRunner

    config = tmp_path / "config.yaml"
    config.write_text("source: wechat\nllm:\n  provider: codex\n", encoding="utf-8")

    def missing_cli(*args):
        raise CodexCLIError("未找到本机 Codex 程序")

    monkeypatch.setattr("opendate.cli._build_router", missing_cli)
    result = CliRunner().invoke(app, ["--config", str(config), "wechat", "draft-ocr"])
    assert result.exit_code != 0
    assert "未找到本机 Codex 程序" in result.output
    assert "allow_llm_upload" not in result.output


def test_codex_cannot_fall_back_to_other_provider():
    config = LLMConfig(provider="codex", fallbacks=[{"provider": "deepseek"}])
    with pytest.raises(ValueError, match="不能混用"):
        LLMRouter.from_config(config, {})


def test_auth_requires_chatgpt_and_never_relogs(monkeypatch, tmp_path):
    backend = CodexCLIBackend(work_dir=str(tmp_path))
    monkeypatch.setattr(backend, "executable", lambda: "codex.exe")
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT")

    monkeypatch.setattr(subprocess, "run", run)
    backend.ensure_ready()
    assert seen == [["codex.exe", "login", "status"]]


@pytest.mark.parametrize("status", ["Not logged in", "Logged in using an API key"])
def test_auth_does_not_silently_use_api_key(monkeypatch, tmp_path, status):
    backend = CodexCLIBackend(work_dir=str(tmp_path))
    monkeypatch.setattr(backend, "executable", lambda: "codex.exe")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(
            returncode=0,
            stdout=status,
            stderr="",
        ),
    )
    with pytest.raises(CodexCLIError, match="ChatGPT 登录"):
        backend.ensure_ready()


def test_environment_keeps_login_path_without_api_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", "G:/safe/CodexHome")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "DO-NOT-COPY")
    monkeypatch.setenv("OPENAI_API_KEY", "DO-NOT-COPY")
    monkeypatch.setenv("TINDER_AUTH_TOKEN", "DO-NOT-COPY")
    monkeypatch.setenv("CODEX_THREAD_ID", "DO-NOT-RESUME")
    env = CodexCLIBackend().environment(tmp_path)
    assert env["CODEX_HOME"] == "G:/safe/CodexHome"
    assert env["TEMP"] == str(tmp_path)
    assert not any("DO-NOT" in value for value in env.values())


def test_command_disables_tools_and_does_not_choose_a_model(monkeypatch, tmp_path):
    backend = CodexCLIBackend()
    monkeypatch.setattr(backend, "executable", lambda: "codex.exe")
    command = backend.command("default", tmp_path)
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    for feature in ("shell_tool", "apps", "plugins", "hooks", "browser_use", "multi_agent"):
        assert command[command.index(feature) - 1] == "--disable"
    assert "--model" not in command
    assert command[-1] == "-"


def test_explicit_codex_model_is_preserved(monkeypatch, tmp_path):
    backend = CodexCLIBackend()
    monkeypatch.setattr(backend, "executable", lambda: "codex.exe")
    command = backend.command("user-chosen-model", tmp_path)
    assert command[command.index("--model") + 1] == "user-chosen-model"


def test_prompt_only_goes_through_stdin_and_output_is_temporary(monkeypatch, tmp_path):
    backend = CodexCLIBackend(work_dir=str(tmp_path))
    monkeypatch.setattr(backend, "executable", lambda: "codex.exe")
    observed = {}

    def run(args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout=agent_event("一条自然回复"), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    text = 'PRIVATE TEXT $(not-a-command) `quoted` "你好"'
    result = backend.complete(resolve_model("codex"), [{"role": "user", "content": text}])
    assert result.text == "一条自然回复"
    assert not observed["kwargs"].get("shell")
    assert text not in " ".join(observed["args"])
    assert "PRIVATE TEXT" in observed["kwargs"]["input"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "event",
    [
        {"type": "turn.failed", "error": {"message": "PRIVATE"}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "PRIVATE"}},
        {"type": "item.started", "item": {"type": "mcp_tool_call", "arguments": "PRIVATE"}},
    ],
)
def test_error_and_tool_events_fail_without_leaking_content(event):
    with pytest.raises(CodexCLIError) as error:
        CodexCLIBackend.parse_output(json.dumps(event))
    assert "PRIVATE" not in str(error.value)


def test_stderr_never_leaks_on_failed_call(monkeypatch, tmp_path):
    backend = CodexCLIBackend(work_dir=str(tmp_path))
    monkeypatch.setattr(backend, "executable", lambda: "codex.exe")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(
            returncode=1,
            stdout="PRIVATE",
            stderr="PRIVATE",
        ),
    )
    with pytest.raises(CodexCLIError) as error:
        backend.complete(resolve_model("codex"), [])
    assert "PRIVATE" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_setup_diagnostics_are_not_mistaken_for_reply_or_tool():
    stdout = "\n".join(
        [
            json.dumps(
                {"type": "item.completed", "item": {"type": "error", "message": "PRIVATE SETUP"}}
            ),
            json.dumps({"type": "turn.started"}),
            agent_event("只有这条回复"),
        ]
    )
    assert CodexCLIBackend.parse_output(stdout) == "只有这条回复"
    with pytest.raises(CodexCLIError):
        CodexCLIBackend.parse_output(json.dumps({"type": "turn.started"}) + "\n" + stdout)


async def test_failed_cli_generation_is_not_retried_by_quality_loop(
    stub_router, skills_engine, persona
):
    from opendate.config import AppConfig
    from opendate.connectors.base import Match
    from opendate.orchestrator.loop import Orchestrator
    from opendate.orchestrator.state import ConversationState
    from opendate.skills.engine import SituationContext

    stub_router.prefers_single_call = True
    loop = Orchestrator(
        connector=SimpleNamespace(),
        router=stub_router,
        skills=skills_engine,
        persona=persona,
        config=AppConfig(source="wechat"),
    )
    calls = []

    async def fail_generation(*args, **kwargs):
        calls.append(True)
        return ""

    loop._generate = fail_generation
    context = SituationContext()
    result, _ = await loop._generate_quality(
        Match(id="synthetic"),
        context,
        skills_engine.select(context),
        ConversationState(match_id="synthetic"),
    )
    assert result == ""
    assert len(calls) == 1


async def test_codex_pipeline_generates_once_with_local_guards(
    monkeypatch, tmp_path, skills_engine, persona
):
    from rich.console import Console
    from opendate.config import AppConfig, WeChatConfig
    from opendate.connectors.wechat import WeChatConnector
    from opendate.connectors.wechat_ocr import ReviewedOCRDriver
    from opendate.orchestrator.loop import Orchestrator
    import io

    router = LLMRouter.from_config(LLMConfig(provider="codex"), {})
    monkeypatch.setattr(router.backend, "executable", lambda: "codex.exe")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0, stdout=agent_event("你想找哪一片的咖啡店？"), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", run)
    router.backend.work_dir = tmp_path / "runtime"
    config = AppConfig(
        source="wechat",
        wechat=WeChatConfig(
            allowed_contacts=["指定好友"],
            contact="指定好友",
            my_name="本人",
            allow_llm_upload=True,
        ),
        max_screen_per_cycle=0,
    )
    connector = WeChatConnector(
        config.wechat, ReviewedOCRDriver("指定好友", "本人", "想找个咖啡店，你有什么想法吗？")
    )
    loop = Orchestrator(
        connector=connector,
        router=router,
        skills=skills_engine,
        persona=persona,
        config=config,
        interactive=False,
        console=Console(file=io.StringIO()),
    )
    actions = await loop.run_once()
    assert actions[0].kind == "send"
    assert not actions[0].sent
    assert len(calls) == 1
