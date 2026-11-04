"""Resumable first-run questions. Drafts never contain API keys or chat bodies."""
from __future__ import annotations

import json
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import AppConfig, load_config
from .llm.providers import PROVIDER_REGISTRY
from .wechat_credentials import provider_secrets, save_api_key
from .wechat_memory import ChatMemory, ConversationBrief, HistoryError, read_history
from .wechat_profile import MyPersona, load_persona, save_settings
from .wechat_text import TxtIdentityError
from .wechat_theme import FONT_SECTION, FONT_TITLE, NativeTheme, ScrollPanel

PROVIDERS = {"codex": "Codex · 现有 ChatGPT 登录", "deepseek": "DeepSeek · API Key",
             "openai": "OpenAI · API Key", "anthropic": "Claude · API Key"}
STEPS = ["选择 AI", "模型与密钥", "微信聊天对象", "聊天历史", "人设与风格", "双方信息与目标", "核对并完成"]


@dataclass
class SetupDraft:
    step: int = 0
    provider: str = "codex"
    model: str = ""
    my_name: str = ""
    contact: str = ""
    history_path: str = ""
    history_conversation: str = ""
    my_sender: str = ""
    peer_sender: str = ""
    skip_history: bool = False
    personality: str = ""
    voice: str = "自然、简短、真诚，先回应对方说的事"
    avoid: str = ""
    examples: str = ""
    my_info: str = ""
    peer_info: str = ""
    goal: str = ""
    allow_upload: bool = False

    @classmethod
    def load(cls, project: Path):
        path = project / "data/wechat/setup-draft.json"
        if not path.exists():
            return cls()
        try:
            value = cls(**json.loads(path.read_text(encoding="utf-8")))
            if type(value.step) is not int or not 0 <= value.step < len(STEPS):
                raise ValueError
            if value.provider not in PROVIDERS:
                raise ValueError
            return value
        except (ValueError, TypeError):
            raise HistoryError("初次配置草稿无法读取，请保留文件并检查 setup-draft.json。") from None

    def save(self, project: Path):
        path = project / "data/wechat/setup-draft.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def config(self, base: AppConfig | None = None) -> AppConfig:
        if len(self.contact.strip()) > 100 or len(self.my_name.strip()) > 100:
            raise HistoryError("好友备注和本人的显示名最多 100 字。")
        if self.provider != "codex" and not self.model.strip():
            raise HistoryError("API 模式请填写服务当前可用的模型 ID；Codex 模式可留空。")
        data = (base or AppConfig()).model_dump(mode="json")
        data.update(source="wechat", auto_send=False, data_dir="data/wechat", max_screen_per_cycle=0)
        data["wechat"].update(contact=self.contact.strip(), my_name=self.my_name.strip(),
                              allowed_contacts=[self.contact.strip()], allow_llm_upload=self.allow_upload)
        data["llm"].update(provider=self.provider, model=self.model.strip() or None,
                          fallbacks=[], timeout=120, max_retries=1, max_tokens=1800)
        config = AppConfig.model_validate(data)
        config.wechat.validate_identity()
        self.persona().validate()
        ConversationBrief(self.my_info, self.peer_info, self.goal).validate()
        return config

    def persona(self):
        return MyPersona(self.personality, self.voice, self.avoid, self.examples)

    def history(self):
        if self.skip_history:
            return None
        if not self.history_path:
            raise HistoryError("请选择聊天 TXT / JSON，或明确勾选“稍后导入历史”。")
        aliases = (self.my_sender, self.peer_sender) if self.my_sender and self.peer_sender else None
        batches = read_history(Path(self.history_path), self.contact.strip(), self.my_name.strip(), txt_senders=aliases)
        if len(batches) == 1:
            return batches[0]
        chosen = next((b for b in batches if b.conversation == self.history_conversation), None)
        if chosen is None:
            raise HistoryError("文件包含多个会话，请在预览下方选择属于此对象的会话 ID。")
        return chosen


def finish_setup(project: Path, draft: SetupDraft, api_key: str = "", base=None):
    """All input validation precedes credential/config/archive writes."""
    config = draft.config(base)
    batch = draft.history()
    if draft.provider != "codex":
        spec = PROVIDER_REGISTRY[draft.provider]
        if api_key.strip():
            if any(c in api_key for c in "\r\n\x00"):
                raise HistoryError("API Key 不能包含换行。")
        elif not provider_secrets(project, config.data_dir, draft.provider).get(spec.api_key_env):
            raise HistoryError("请返回“模型与密钥”，在本机填写 API Key。密钥不会保存在配置草稿中。")
    if draft.provider != "codex" and api_key.strip():
        save_api_key(project, config.data_dir, draft.provider, api_key)
    config = save_settings(project, config, draft.persona(), draft.contact, draft.my_name)
    memory = ChatMemory(project / config.data_dir / "wechat-memory.sqlite3")
    memory.save_brief(config.wechat.contact, ConversationBrief(draft.my_info, draft.peer_info, draft.goal))
    if batch:
        memory.import_batch(config.wechat.contact, batch, draft.history_path)
        memory.bind(config.wechat.contact, Path(draft.history_path), batch.conversation, batch.sender_names)
    (project / "data/wechat/setup-draft.json").unlink(missing_ok=True)
    return config


class SetupWizard:
    def __init__(self, root, project, on_complete):
        self.root, self.project, self.on_complete = root, project, on_complete
        self.draft = SetupDraft.load(project)
        self.base = None
        if (project / "config.wechat.yaml").exists():
            self.base = load_config(project / "config.wechat.yaml")
            if not (project / "data/wechat/setup-draft.json").exists():
                persona = load_persona(project, self.base)
                brief = ChatMemory(project / self.base.data_dir / "wechat-memory.sqlite3").brief(self.base.wechat.contact)
                self.draft = SetupDraft(provider=self.base.llm.provider if self.base.llm.provider in PROVIDERS else "codex",
                    model=self.base.llm.model or "", contact=self.base.wechat.contact, my_name=self.base.wechat.my_name,
                    skip_history=True, **asdict(persona), **asdict(brief), allow_upload=self.base.wechat.allow_llm_upload)
        if self.draft.step > 3 and not self.draft.skip_history:
            try:
                self.draft.history()
            except (ValueError, OSError):
                self.draft.step = 3
        if self.draft.step > 1 and self.draft.provider != "codex":
            try:
                spec = PROVIDER_REGISTRY[self.draft.provider]
                ready = provider_secrets(project, "data/wechat", self.draft.provider).get(spec.api_key_env)
            except HistoryError:
                ready = False
            if not ready:
                self.draft.step = 1
        self.api_key = tk.StringVar()
        self.fields = {}
        self.confirm = tk.BooleanVar(value=False)
        self.theme = NativeTheme(root)
        root.title("恋聊教练 · 初次配置引导")
        density = max(1, root.winfo_fpixels("1i") / 96)
        root.geometry(f"{min(round(980*density), root.winfo_screenwidth()-100)}x"
                      f"{min(round(780*density), root.winfo_screenheight()-120)}")
        root.minsize(700, 550)
        self.shell = ttk.Frame(root, padding=24)
        self.shell.pack(fill="both", expand=True)
        ttk.Label(self.shell, text="恋聊教练  LoveChat Coach", font=FONT_TITLE).pack(anchor="w")
        ttk.Label(self.shell, text="基于 OpenDate Skill 开发 · 一步步配置你的聊天助手",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 16))
        self.progress = tk.StringVar()
        ttk.Label(self.shell, textvariable=self.progress, font=FONT_SECTION).pack(anchor="w", pady=(0, 12))
        nav = ttk.Frame(self.shell)
        nav.pack(side="bottom", fill="x", pady=(14, 0))
        self.back = ttk.Button(nav, text="上一步", command=self.previous)
        self.back.pack(side="left")
        ttk.Button(nav, text="保存进度，稍后继续", command=self.pause).pack(side="left", padx=12)
        self.next_button = ttk.Button(nav, text="下一步", style="Primary.TButton", command=self.next)
        self.next_button.pack(side="right")
        self.panel = ScrollPanel(self.shell)
        self.panel.pack(fill="both", expand=True)
        self.body = self.panel.body
        root.protocol("WM_DELETE_WINDOW", self.pause)
        self.render()

    def label(self, text):
        ttk.Label(self.body, text=text, wraplength=900, justify="left").pack(anchor="w", pady=(8, 6))

    def entry(self, key, question, *, secret=False):
        self.label(question)
        var = self.api_key if secret else tk.StringVar(value=getattr(self.draft, key))
        ttk.Entry(self.body, textvariable=var, show="●" if secret else "").pack(fill="x", pady=(0, 10))
        if not secret:
            self.fields[key] = var

    def text(self, key, question, height=4):
        from tkinter.scrolledtext import ScrolledText
        self.label(question)
        box = ScrolledText(self.body, height=height, wrap="word", undo=True, font=("Microsoft YaHei UI", 11))
        box.insert("1.0", getattr(self.draft, key))
        box.pack(fill="x", pady=(0, 10))
        self.fields[key] = box

    def checkbox(self, key, text):
        var = tk.BooleanVar(value=getattr(self.draft, key))
        ttk.Checkbutton(self.body, text=text, variable=var).pack(anchor="w", pady=10)
        self.fields[key] = var

    def collect(self):
        for key, widget in self.fields.items():
            setattr(self.draft, key, widget.get("1.0", "end-1c") if isinstance(widget, tk.Text) else widget.get())

    def render(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.fields = {}
        step = self.draft.step
        self.progress.set(f"第 {step+1} / {len(STEPS)} 步：{STEPS[step]}")
        self.back.configure(state="normal" if step else "disabled")
        self.next_button.configure(text="确认并开始使用" if step == 6 else "下一步")
        if step == 0:
            self.label("你希望用哪种 AI 生成回复？Codex 复用本机 ChatGPT 登录；其他服务需要你自己的 API Key。")
            var = tk.StringVar(value=self.draft.provider)
            for key, label in PROVIDERS.items():
                ttk.Radiobutton(self.body, text=label, value=key, variable=var).pack(anchor="w", pady=10)
            self.fields["provider"] = var
            self.label("导入与预览在本机完成。生成回复或整理记忆时，所选文字、人设和目标会交给你选定的服务。\n助手生成草稿，由你修改、复制并自行发送。")
        elif step == 1:
            if self.draft.provider == "codex":
                self.label("你已经在这台电脑的 Codex 中使用 ChatGPT 登录了吗？不需要 API Key。\n安装并登录 Codex 后，可点击检查；也可以先完成其他配置。")
                ttk.Button(self.body, text="检查本机 Codex 登录", command=self.check_codex).pack(anchor="w", pady=12)
            else:
                self.label(f"请在本机填写 {PROVIDERS[self.draft.provider]} 的密钥。输入已遮蔽，完成时使用 Windows 用户级加密保存。\n不要把密钥发送到聊天、截图、GitHub 或问题报告中。暂停后需重新输入尚未保存的密钥。")
                self.entry("api_key", "这个服务的 API Key 是什么？（已有本地密钥或环境变量时可留空）", secret=True)
            self.entry("model", "使用哪个模型？Codex 可留空；API 模式请填写服务当前可用的模型 ID。")
        elif step == 2:
            self.entry("my_name", "你在这段微信聊天中的显示昵称是什么？")
            self.entry("contact", "你希望和谁聊天？请填对方在微信中的完整、唯一备注。")
            self.label("只处理你指定的这位对象。文件中的昵称若与备注不同，下一步会让你核对双方身份。")
        elif step == 3:
            self.entry("history_path", "你有双方的聊天历史吗？选择 TXT 或 JSON；原文件保留在本机。")
            ttk.Button(self.body, text="选择聊天文件", command=self.choose_history).pack(anchor="w", pady=8)
            self.checkbox("skip_history", "稍后导入历史（现在先配置其他信息）")
            self.entry("my_sender", "TXT 中“我”的昵称（与设置一致时可留空）")
            self.entry("peer_sender", "TXT 中“对方”的昵称（与设置一致时可留空）")
            ttk.Button(self.body, text="识别身份并预览导入", command=self.preview_history).pack(anchor="w", pady=8)
            self.history_note = tk.StringVar(value="确认后才会保存历史。支持昵称、时间、正文逐行排列的 TXT。")
            ttk.Label(self.body, textvariable=self.history_note, wraplength=900).pack(anchor="w", pady=10)
            self.label("多个会话时，请选择与此对象对应的会话 ID：")
            self.conversation = ttk.Combobox(self.body, state="readonly", textvariable=tk.StringVar(value=self.draft.history_conversation))
            self.conversation.pack(fill="x", pady=8)
            self.fields["history_conversation"] = self.conversation
        elif step == 4:
            self.text("personality", "你希望 AI 如何理解你的人设、性格和背景？（可暂留空）")
            self.text("voice", "你希望和对方聊天时是什么风格？", 3)
            self.text("avoid", "有哪些不想使用的表达或不希望越过的边界？", 3)
            self.text("examples", "给几句你平时会说的话，让回复更像你。（可暂留空）", 3)
        elif step == 5:
            self.text("my_info", "关于你本人，有哪些确定的信息需要 AI 知道？")
            self.text("peer_info", "对方明确说过哪些喜好、情况和边界？请把猜测标明。（可暂留空）")
            self.text("goal", "你希望和这个对象最终发展成什么关系？有什么节奏或底线？")
        else:
            self.label(f"AI：{PROVIDERS[self.draft.provider]}\n模型：{self.draft.model or '接入方式默认模型'}\n我：{self.draft.my_name}\n对象：{self.draft.contact}\n聊天风格：{self.draft.voice}\n目标：{self.draft.goal or '尚未填写'}")
            batch = self.draft.history()
            self.label(self.history_description(batch) if batch else "历史：此次不导入，之后可在聊天历史页添加。")
            self.checkbox("allow_upload", "允许生成/整理时将选定聊天、人设和目标交给上述 AI（可不勾选，先离线使用）")
            ttk.Checkbutton(self.body, text="我已核对双方身份与导入范围，确认保存这些设置。", variable=self.confirm).pack(anchor="w", pady=12)
            self.label("完整历史保存在本机；每次生成结合近期消息、检索到的旧记录和已保存记忆，并非每次上传整个聊天库。\n配置完成不代表 API 余额、登录或微信兼容性已验证。")
        self.root.update_idletasks()
        self.panel.canvas.yview_moveto(0)

    @staticmethod
    def history_description(batch):
        mine = sum(r.sender == "me" for r in batch.records)
        times = sorted(r.timestamp for r in batch.records)
        return (f"历史：{len(batch.records)} 条文字，我 {mine} 条 / 对方 {len(batch.records)-mine} 条；"
                f"跳过非文字 {batch.skipped} 条。\n原始时间范围（UTC）：{times[0]} 至 {times[-1]}\n会话 ID：{batch.conversation}")

    def choose_history(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择聊天历史 TXT / JSON",
                                         filetypes=[("聊天文件", "*.txt *.json")])
        if path:
            self.fields["history_path"].set(path)
            self.fields["skip_history"].set(False)
            self.fields["my_sender"].set("")
            self.fields["peer_sender"].set("")
            self.fields["history_conversation"].set("")

    def preview_history(self):
        self.collect()
        try:
            self.draft.skip_history = False
            self.fields["skip_history"].set(False)
            aliases = (self.draft.my_sender, self.draft.peer_sender) if self.draft.my_sender and self.draft.peer_sender else None
            batches = read_history(Path(self.draft.history_path), self.draft.contact.strip(), self.draft.my_name.strip(), txt_senders=aliases)
            self.conversation.configure(values=[b.conversation for b in batches])
            if len(batches) == 1:
                self.conversation.set(batches[0].conversation)
            self.collect()
            batch = self.draft.history()
            self.history_note.set(self.history_description(batch))
        except TxtIdentityError as exc:
            self.history_note.set("检测到文件昵称：" + "、".join(exc.participants) + "。请在上方填写哪位是我、哪位是对方，再预览。")
        except (ValueError, OSError) as exc:
            messagebox.showerror("请核对历史", str(exc), parent=self.root)

    def check_codex(self):
        from .llm.codex_cli import CodexCLIBackend
        try:
            CodexCLIBackend(work_dir=str(self.project / "work/codex-runtime")).ensure_ready()
            messagebox.showinfo("登录检查", "本机 Codex 已使用 ChatGPT 登录。未上传聊天、未调用模型。", parent=self.root)
        except RuntimeError as exc:
            messagebox.showerror("尚未就绪", str(exc), parent=self.root)

    def validate_step(self):
        step = self.draft.step
        if step == 1 and self.draft.provider != "codex" and not self.api_key.get().strip():
            spec = PROVIDER_REGISTRY[self.draft.provider]
            if not provider_secrets(self.project, "data/wechat", self.draft.provider).get(spec.api_key_env):
                raise HistoryError("请在本机输入所选服务的 API Key，或先配置该服务的环境变量。")
        if step == 1 and self.draft.provider != "codex" and not self.draft.model.strip():
            raise HistoryError("请填写所选服务当前可用的模型 ID。")
        if step == 2:
            self.draft.config(self.base)
        if step == 3:
            self.draft.history()
        if step == 4:
            self.draft.persona().validate()
        if step == 5:
            ConversationBrief(self.draft.my_info, self.draft.peer_info, self.draft.goal).validate()
            self.draft.history()

    def next(self):
        provider_before = self.draft.provider
        self.collect()
        if provider_before != self.draft.provider:
            self.api_key.set("")
            self.draft.model = ""
            self.draft.allow_upload = False
            self.confirm.set(False)
        try:
            self.validate_step()
            if self.draft.step == 6:
                if not self.confirm.get():
                    raise HistoryError("请先核对并勾选确认，再保存配置。")
                finish_setup(self.project, self.draft, self.api_key.get(), self.base)
                self.api_key.set("")
                self.shell.destroy()
                self.on_complete()
                return
            self.draft.step += 1
            self.draft.save(self.project)
            self.render()
        except (ValueError, OSError) as exc:
            messagebox.showerror("这一步还没完成", str(exc), parent=self.root)

    def previous(self):
        self.collect()
        self.confirm.set(False)
        self.draft.step = max(0, self.draft.step-1)
        self.draft.save(self.project)
        self.render()

    def pause(self):
        self.collect()
        self.draft.save(self.project)
        self.api_key.set("")
        self.root.destroy()
