"""OpenDate command-line interface (``python -m opendate ...``).

Commands
--------
* ``init``       — write example config + .env into the current directory
* ``providers``  — list every supported LLM provider
* ``skills``     — list the loaded dating skills
* ``persona``    — build the user's persona profile from posts/chats
* ``screen``     — preview like/pass decisions on recommendations
* ``run``        — start the runtime loop

Use ``--mock`` (a global flag) anywhere to run fully offline with the mock
connector and a stub LLM — no real credentials required.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import (
    EXAMPLE_CONFIG_YAML,
    EXAMPLE_ENV,
    AppConfig,
    load_config,
    load_secrets,
)
from .connectors.base import build_connector
from .llm.providers import list_providers, provider_ready
from .llm.router import LLMRouter
from .orchestrator.loop import Orchestrator, score_candidate
from .orchestrator.safety import SafetyGuard
from .orchestrator.state import ConversationStore
from .persona.analyze import PersonaProfile, build_persona, load_profile
from .skills.engine import SkillsEngine
from .utils.logging import configure_logging, get_logger

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="OpenDate — a vibe-dating AI agent. Use --mock to run offline.",
)
persona_app = typer.Typer(help="Build and inspect your persona profile.")
app.add_typer(persona_app, name="persona")
wechat_app = typer.Typer(help="个人微信：环境检查；只处理手动打开的指定私聊。")
app.add_typer(wechat_app, name="wechat")

console = Console()
log = get_logger("cli")


@dataclass
class State:
    config_path: Optional[Path]
    env_file: Path
    mock: bool


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"OpenDate {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config YAML (defaults are auto-found)."
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env", help="Path to the .env file with secrets."
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Use the offline mock connector + stub LLM."
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level."),
    _version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True
    ),
) -> None:
    configure_logging(log_level.upper())
    ctx.obj = State(config_path=config, env_file=env_file, mock=mock)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_config(state: State) -> AppConfig:
    return load_config(state.config_path)


def _build_router(state: State, config: AppConfig) -> tuple[LLMRouter, object]:
    if config.source == "wechat" and not state.mock:
        config.wechat.validate_live()
        if not config.wechat.allow_llm_upload:
            raise typer.BadParameter(
                "微信聊天不会默认上传模型。确认数据流后，显式设置 wechat.allow_llm_upload: true。"
            )
    secrets = load_secrets(state.env_file)
    router = LLMRouter.from_config(config.llm, secrets.env_map(), stub=state.mock)
    if not state.mock:
        router.ensure_ready(secrets.env_map())
    return router, secrets


@wechat_app.command("check")
def wechat_check(
    ctx: typer.Context,
    inspect_ui: bool = typer.Option(False, "--ui", help="只检查登录主窗口与 UI 树，不读聊天。"),
) -> None:
    """检查本地源码和依赖。不会安装、启动微信或修改系统设置。"""
    from .connectors.wechat import check_environment

    config = _load_config(ctx.obj)
    if not config.wechat.pyweixin_scripts:
        raise typer.BadParameter("请先填写 wechat.pyweixin_scripts")
    status = check_environment(Path(config.wechat.pyweixin_scripts), inspect_ui=inspect_ui)
    console.print_json(json.dumps(status, ensure_ascii=False))
    if not status["ready"]:
        console.print("未完成真实微信联调。若 --ui 检查失败，请人工检查登录及 UI 可见性；不会自动重试。")
        if inspect_ui or status["missing_packages"] or not status["source_found"] or not status["windows"]:
            raise typer.Exit(1)


@wechat_app.command("check-codex")
def wechat_check_codex(ctx: typer.Context) -> None:
    """只检查 Codex 定位、登录和当前授权，不读取微信、不调用模型。"""
    from .llm.codex_cli import CodexCLIBackend, CodexCLIError

    config = _load_config(ctx.obj)
    if config.llm.provider != "codex":
        raise typer.BadParameter("当前模型未配置为 codex")
    try:
        CodexCLIBackend.executable()
        CodexCLIBackend().ensure_ready()
    except CodexCLIError as exc:
        raise typer.BadParameter(str(exc)) from None
    console.print_json(json.dumps({
        "codex_found": True, "chatgpt_login_ready": True,
        "llm_upload_enabled": config.wechat.allow_llm_upload,
        "model_called": False, "wechat_read": False, "messages_sent": 0,
    }))


@wechat_app.command("inspect")
def wechat_inspect(
    ctx: typer.Context,
    ocr: bool = typer.Option(False, "--ocr", help="UI 树不可读时仅做本地 OCR 统计；不调用模型或发送。"),
) -> None:
    """只读取已指定私聊的有限上下文，显示统计；不调用模型、不保存正文、不发送。"""
    from .connectors.wechat import WeChatError

    state: State = ctx.obj
    config = _load_config(state)
    if config.source != "wechat" or state.mock:
        raise typer.BadParameter("连接检查需使用真实 source: wechat 配置，不能使用 --mock")
    try:
        config.wechat.validate_live()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if ocr:
        from .connectors.wechat_ocr import inspect_local_ocr

        try:
            result = inspect_local_ocr(config.wechat.contact, Path("work/wechat-ocr"))
        except Exception as exc:
            console.print(str(exc) if isinstance(exc, WeChatError) else "本地 OCR 检查失败，已停止。", markup=False)
            raise typer.Exit(1) from None
        console.print_json(json.dumps(result, ensure_ascii=False))
        return
    connector = build_connector(config)

    async def inspect_once() -> None:
        try:
            matches = await connector.get_matches(count=1)
            messages = matches[0].messages if matches else []
            console.print_json(json.dumps({
                "contact": config.wechat.contact,
                "message_count": len(messages),
                "from_me": sum(m.from_me for m in messages),
                "from_contact": sum(not m.from_me for m in messages),
                "latest_sender": messages[-1].sender if messages else None,
                "latest_is_text": not messages[-1].raw.get("unsupported", False) if messages else None,
                "model_called": False, "messages_sent": 0, "chat_saved": False,
            }, ensure_ascii=False))
        except Exception as exc:
            console.print(str(exc) if isinstance(exc, WeChatError) else "微信读取检查失败，已停止。", markup=False)
            raise typer.Exit(1) from None
        finally:
            await connector.close()

    asyncio.run(inspect_once())


@wechat_app.command("draft-ocr")
def wechat_draft_ocr(ctx: typer.Context) -> None:
    """在本地核对 OCR 来信后生成建议；不向微信发送。"""
    from rich.prompt import Confirm, Prompt
    from rich.text import Text

    from .connectors.wechat import WeChatConnector, WeChatError
    from .connectors.wechat_ocr import ReviewedOCRDriver, read_local_ocr_lines, select_ocr_message
    from .llm.codex_cli import CodexCLIError

    state: State = ctx.obj
    config = _load_config(state)
    if config.source != "wechat" or state.mock:
        raise typer.BadParameter("此命令仅用于已配置的真实微信，离线演示请用 --mock run")
    try:
        # Check consent/config/key presence without performing a model request.
        router, _ = _build_router(state, config)
    except (CodexCLIError, typer.BadParameter) as exc:
        raise typer.BadParameter(str(exc)) from None
    except Exception:
        message = (
            "请检查 Codex 的 ChatGPT 登录状态，并明确开启 wechat.allow_llm_upload"
            if config.llm.provider == "codex" else
            "请先在本地 .env 配置所选模型密钥，并明确开启 wechat.allow_llm_upload"
        )
        raise typer.BadParameter(message) from None
    try:
        lines = read_local_ocr_lines(config.wechat.contact, Path("work/wechat-ocr"))[-60:]
        if not lines:
            raise WeChatError("指定聊天区域没有识别到文字，请人工查看窗口")
        console.print("以下文字仅在本机显示，尚未传给模型。请对照微信核对：")
        for number, line in enumerate(lines, 1):
            console.print(Text(f"{number}. {line}"))
        selection = Prompt.ask(
            "请选择对方最后一条消息的行号（如 2 或 2,3；回车取消）", default="", console=console
        )
        if not selection.strip():
            console.print("已取消；未调用模型、未发送。")
            return
        incoming = select_ocr_message(lines, selection)
        console.print(Panel(Text(incoming), title="你选定的好友来信，请核对识别是否准确"))
        if not Confirm.ask(
            f"确认仅将以上来信和你配置的风格资料交给 {config.llm.provider} 生成建议？",
            default=False, console=console,
        ):
            console.print("已取消；未调用模型、未发送。")
            return
        # No live sender is constructed, even if the user changes auto_send.
        connector = WeChatConnector(
            config.wechat,
            ReviewedOCRDriver(config.wechat.contact, config.wechat.my_name, incoming),
            send_enabled=False,
        )
        engine = SkillsEngine()
        engine.load_all()
        orchestrator = Orchestrator(
            connector=connector, router=router, skills=engine,
            persona=_load_or_build_persona(config, router), config=config,
            console=console, interactive=False, store=ConversationStore(config.state_path()),
        )

        async def draft_once() -> None:
            try:
                await orchestrator.run_once()
            finally:
                await connector.close()

        asyncio.run(draft_once())
        console.print("本轮未向微信发送；请审阅建议后自行复制回复。")
    except (EOFError, KeyboardInterrupt):
        console.print("已取消；不会向微信发送。")
    except Exception as exc:
        console.print(str(exc) if isinstance(exc, WeChatError) else "本次建议生成失败，已停止；请检查本地配置及网络。", markup=False)
        raise typer.Exit(1) from None


def _load_or_build_persona(config: AppConfig, router: LLMRouter) -> PersonaProfile:
    path = Path(config.persona.profile_path)
    if path.exists():
        try:
            return load_profile(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not load persona profile %s: %s", path, exc)
    return build_persona(
        config.persona, voice=config.preferences.voice,
        router=None if getattr(router, "prefers_single_call", False) else router,
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."), help="Where to write config.yaml and .env."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Write a starter ``config.yaml`` and ``.env`` you can fill in."""
    directory.mkdir(parents=True, exist_ok=True)
    targets = {
        directory / "config.yaml": EXAMPLE_CONFIG_YAML,
        directory / ".env": EXAMPLE_ENV,
    }
    for path, content in targets.items():
        if path.exists() and not force:
            console.print(f"[yellow]skip[/] {path} (exists; use --force)")
            continue
        path.write_text(content, encoding="utf-8")
        console.print(f"[green]wrote[/] {path}")
    console.print(
        "\nNext: put your secrets in [bold].env[/], edit [bold]config.yaml[/], "
        "then run [bold]opendate --mock run[/] to try it offline."
    )


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------
@app.command()
def providers(ctx: typer.Context) -> None:
    """List every supported LLM provider (Western + Chinese)."""
    state: State = ctx.obj
    try:
        secrets = load_secrets(state.env_file)
        env_map = secrets.env_map()
    except Exception:  # noqa: BLE001
        env_map = None

    table = Table(title="Supported LLM providers", show_lines=False)
    table.add_column("Key", style="bold cyan")
    table.add_column("Provider")
    table.add_column("Region")
    table.add_column("Mode")
    table.add_column("API key env")
    table.add_column("Default model")
    table.add_column("Cfg", justify="center")

    for region in ("Western", "Chinese"):
        for spec in list_providers(region=region):  # type: ignore[arg-type]
            ready = provider_ready(spec.key, env_map) if env_map is not None else False
            table.add_row(
                spec.key,
                spec.label,
                spec.region,
                spec.mode,
                spec.api_key_env,
                spec.default_model,
                "[green]✓[/]" if ready else "[dim]—[/]",
            )
    console.print(table)
    console.print(
        f"[dim]{len(list_providers())} provider routes. "
        "Set the matching API key env var (see .env.example), then select one "
        "in config.yaml under llm.provider.[/]"
    )


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------
@app.command()
def skills(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show playbooks."),
) -> None:
    """List the loaded dating skills."""
    engine = SkillsEngine()
    loaded = engine.load_all()
    table = Table(title=f"Loaded skills ({len(loaded)})")
    table.add_column("Skill", style="bold cyan")
    table.add_column("Category")
    table.add_column("Fires when")
    table.add_column("What it does", overflow="fold", max_width=60)
    for name in sorted(loaded):
        s = loaded[name]
        table.add_row(s.name, s.category or "-", s.fires_when or "-", s.description)
    console.print(table)
    if verbose:
        for name in sorted(loaded):
            console.print(
                Panel(loaded[name].body, title=name, border_style="magenta")
            )


# ---------------------------------------------------------------------------
# persona build
# ---------------------------------------------------------------------------
@persona_app.command("build")
def persona_build(ctx: typer.Context) -> None:
    """Ingest your posts/chats and build a persona profile."""
    state: State = ctx.obj
    config = _load_config(state)
    router, _ = _build_router(state, config)
    profile = build_persona(
        config.persona,
        voice=config.preferences.voice,
        router=router,
        save_path=config.persona.profile_path,
    )
    console.print(
        Panel(
            profile.style_brief(),
            title="Persona profile",
            border_style="blue",
        )
    )
    console.print(
        f"[green]Saved[/] persona to {config.persona.profile_path} "
        f"(LLM-refined: {profile.generated_with_llm})"
    )


@persona_app.command("show")
def persona_show(ctx: typer.Context) -> None:
    """Show the saved persona profile."""
    state: State = ctx.obj
    config = _load_config(state)
    path = Path(config.persona.profile_path)
    if not path.exists():
        console.print(f"[yellow]No persona at {path}. Run `opendate persona build`.[/]")
        raise typer.Exit(code=1)
    profile = load_profile(path)
    console.print(Panel(profile.voice_card(), title="Voice card", border_style="blue"))
    console.print(Panel(profile.style_brief(), title="Persona detail", border_style="blue"))


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------
@app.command()
def screen(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit", help="How many candidates to screen."),
) -> None:
    """Preview like/pass decisions on current recommendations (read-only)."""
    state: State = ctx.obj
    config = _load_config(state)
    secrets = load_secrets(state.env_file)
    connector = build_connector(config, secrets, force_mock=state.mock)

    async def _run() -> None:
        try:
            candidates = await connector.get_recommendations(limit)
            table = Table(title="Screening preview")
            table.add_column("Candidate", style="bold")
            table.add_column("Age")
            table.add_column("Decision")
            table.add_column("Score")
            table.add_column("Why", overflow="fold", max_width=60)
            for cand in candidates:
                decision, score, reasons, _ = score_candidate(
                    cand, config.preferences
                )
                color = "green" if decision == "like" else "red"
                table.add_row(
                    cand.name or cand.id,
                    str(cand.age or "-"),
                    f"[{color}]{decision}[/]",
                    str(score),
                    "; ".join(reasons),
                )
            console.print(table)
        finally:
            await connector.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
@app.command()
def run(
    ctx: typer.Context,
    cycles: int = typer.Option(1, "--cycles", help="Loop cycles (0 = run forever)."),
    interval: Optional[float] = typer.Option(
        None, "--interval", help="Seconds between cycles (defaults to config)."
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Prompt before sending. --no-interactive never sends (dry run).",
    ),
    auto_send: Optional[bool] = typer.Option(
        None,
        "--auto-send/--no-auto-send",
        help="Override config auto_send (send without asking).",
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="Override LLM provider."),
    model: Optional[str] = typer.Option(None, "--model", help="Override LLM model."),
    wechat_send: bool = typer.Option(
        False, "--wechat-send", help="启用微信逐条确认发送；默认只给建议，不能自动发送。"
    ),
) -> None:
    """Run the OpenDate loop: Sync → Screen → Decide → Generate → Voice → Guard → Act."""
    state: State = ctx.obj
    config = _load_config(state)
    if provider:
        config.llm.provider = provider
    if model:
        config.llm.model = model
    if auto_send is not None:
        config.auto_send = auto_send
    if config.source == "wechat":
        if config.auto_send:
            raise typer.BadParameter("个人微信不支持 --auto-send，只能逐条人工确认")
        if cycles != 1:
            raise typer.BadParameter("微信每次仅处理一轮（--cycles 1），不在后台循环或自动重试")
        if wechat_send and (not interactive or state.mock):
            raise typer.BadParameter("微信发送需要真实模式及交互确认；演示和 --no-interactive 不能发送")
    elif wechat_send:
        raise typer.BadParameter("--wechat-send 仅用于 source: wechat")
    if not interactive:
        config.auto_send = False

    router, secrets = _build_router(state, config)
    connector = build_connector(
        config, secrets, force_mock=state.mock, enable_wechat_send=wechat_send
    )
    engine = SkillsEngine()
    engine.load_all()
    persona = _load_or_build_persona(config, router)
    guard = SafetyGuard(
        config.safety,
        router=router,
        guidance=(engine.get_or_none("consent-and-safety").body if engine.get_or_none("consent-and-safety") else ""),
    )

    orchestrator = Orchestrator(
        connector=connector,
        router=router,
        skills=engine,
        persona=persona,
        config=config,
        safety=guard,
        console=console,
        interactive=interactive,
        store=ConversationStore(config.state_path()),
    )

    mode = "MOCK" if state.mock else config.source.upper()
    send_mode = (
        "auto-send ON" if config.auto_send else "human-in-the-loop (no auto-send)"
    )
    console.print(
        Panel(
            f"Source: [bold]{mode}[/]   LLM: [bold]{router.primary_label}[/]"
            f"{' (stub)' if router.is_stub else ''}\n"
            f"Mode: {send_mode}   Interactive: {interactive}",
            title="OpenDate run",
            border_style="bold magenta",
        )
    )

    async def _run() -> None:
        try:
            await orchestrator.run(cycles=cycles, interval=interval or config.poll_interval)
        except Exception as exc:
            if config.source != "wechat":
                raise
            # Library/transport errors can contain payloads. Do not dump a
            # traceback, request, or private conversation to the terminal.
            from .connectors.wechat import WeChatError

            console.print(str(exc) if isinstance(exc, WeChatError) else "微信操作失败，已停止；请检查配置及界面。", markup=False)
            raise typer.Exit(1) from None
        finally:
            await connector.close()

    asyncio.run(_run())


def main_entry() -> None:
    """Console-script / ``python -m opendate`` entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main_entry()
