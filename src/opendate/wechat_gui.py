"""Native Windows workspace: history, reviewed memory, Codex draft, clipboard."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .config import load_config
from .llm.codex_cli import CodexCLIBackend
from .wechat_desktop import (
    build_reply_context, context_preview, draft_memory, generate_reply_with_strategy, reply_settings,
)
from .wechat_memory import (
    ChatMemory, ChatRecord, ConversationBrief, HistoryBatch, HistoryError, read_history,
)
from .wechat_profile import MyPersona, load_persona, save_settings
from .wechat_text import TxtIdentityError
from .wechat_strategy import (
    ACTIONS, INTENTS, PRIMARY_SKILLS, SKILL_LABELS, STAGES, StrategySettings,
    latest_report, load_settings as load_strategy, report_text,
    save_settings as save_strategy,
)
from .wechat_theme import (
    FONT_EDITOR, FONT_SECTION, FONT_STRONG, FONT_TITLE, LIGHT, NativeTheme, ReplySplit, ScrollPanel,
    enable_dpi_awareness,
)


def local_date(value: str | None) -> str:
    if not value:
        return "暂无"
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


class WeChatWindow:
    def __init__(self, root: tk.Tk, project: Path):
        self.root = root
        self.project = project.resolve()
        self.config = load_config(self.project / "config.wechat.yaml")
        self.config.wechat.validate_identity()
        self.contact = self.config.wechat.contact
        self.my_name = self.config.wechat.my_name
        self.persona = load_persona(self.project, self.config)
        self.note_drafts: dict[str, str] = {}
        self.chat_drafts: dict[str, tuple[str, str, str]] = {}
        self.brief_drafts: dict[str, ConversationBrief] = {}
        self.strategy_drafts: dict[str, StrategySettings] = {}
        self.window_choices: dict[str, dict] = {}
        self.memory = ChatMemory(self.project / self.config.data_dir / "wechat-memory.sqlite3")
        self.events: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self.busy = False
        self.closed = False
        self.controls: list[ttk.Button] = []
        self.packet: dict | None = None
        self.output_revision = ""
        self.reviewed_text = ""
        root.title("恋聊教练 · LoveChat Coach")
        density = max(1.0, root.winfo_fpixels("1i") / 96)
        available_width = root.winfo_screenwidth()-round(80*density)
        available_height = root.winfo_screenheight()-round(100*density)
        root.geometry(f"{min(round(1400*density), available_width)}x"
                      f"{min(round(940*density), available_height)}")
        root.minsize(min(round(1040*density), available_width),
                     min(round(620*density), available_height))
        self._style()
        self._build()
        self.theme._recolor(root)
        self.refresh()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)

    def _style(self):
        self.theme = NativeTheme(self.root)
        self.theme_path = self.project / self.config.data_dir / "window-theme.json"
        try:
            saved = json.loads(self.theme_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("mode") == "dark":
                self.theme.apply(True)
        except (OSError, ValueError):
            pass

    def change_theme(self, _event=None):
        dark = self.theme_choice.get() == "夜间"
        self.theme.apply(dark)
        try:
            self.theme_path.parent.mkdir(parents=True, exist_ok=True)
            self.theme_path.write_text(json.dumps({"mode": "dark" if dark else "light"}), encoding="utf-8")
        except OSError:
            self.status.set("主题已切换，但偏好未能保存；可在下次打开时重新选择。")

    def button(self, parent, title, action, primary=False):
        button = ttk.Button(parent, text=title, command=action, takefocus=True,
                            style="Primary.TButton" if primary else "TButton")
        self.controls.append(button)
        return button

    @staticmethod
    def text(parent, height=6, readonly=False):
        theme = getattr(parent.winfo_toplevel(), "_opendate_theme", None)
        p = theme.colors if theme else LIGHT
        widget = ScrolledText(parent, height=height, wrap="word", relief="flat", borderwidth=0,
                              padx=16, pady=14, bg=p["field"], fg=p["ink"], insertbackground=p["ink"],
                              highlightthickness=1, highlightbackground=p["edge"], highlightcolor=p["focus"],
                              selectbackground=p["selection"], selectforeground=p["ink"], takefocus=True,
                              font=FONT_EDITOR, spacing3=6, undo=not readonly)
        widget.vbar.destroy()
        widget.vbar = ttk.Scrollbar(widget.frame, orient="vertical", command=widget.yview)
        widget.vbar.pack(side="right", fill="y")
        widget.configure(yscrollcommand=widget.vbar.set)
        if readonly:
            widget.configure(state="disabled")
        return widget

    @staticmethod
    def put(widget, value, readonly=False):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        if readonly:
            widget.configure(state="disabled")

    def _build(self):
        outer = ttk.Frame(self.root, padding=(24, 18))
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        title_row = ttk.Frame(header)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="恋聊教练", font=FONT_TITLE).pack(side="left")
        self.theme_choice = tk.StringVar(value="夜间" if self.theme.dark else "暖纸")
        theme_picker = ttk.Combobox(title_row, textvariable=self.theme_choice, state="readonly",
                                   values=["暖纸", "夜间"], width=7)
        theme_picker.pack(side="right")
        theme_picker.bind("<<ComboboxSelected>>", self.change_theme)
        ttk.Label(title_row, text="视觉主题", style="Muted.TLabel").pack(side="right", padx=8)
        self.identity_text = tk.StringVar()
        ttk.Label(header, textvariable=self.identity_text,
                  style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        self.persona_text = tk.StringVar()
        ttk.Label(header, textvariable=self.persona_text, wraplength=1080,
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 8))
        row = ttk.Frame(header)
        row.pack(fill="x")
        ttk.Label(row, text="聊天对象完整备注").pack(side="left")
        self.target_var = tk.StringVar(value=self.contact)
        self.target_box = ttk.Combobox(row, textvariable=self.target_var, width=20)
        self.target_box.pack(side="left", padx=8)
        self.button(row, "应用对象", self.apply_contact).pack(side="left")
        self.button(row, "编辑我的人设与风格", lambda: self.tabs.select(3)).pack(side="left", padx=8)
        self.button(row, "双方信息与目标", lambda: self.tabs.select(4)).pack(side="left")
        row = ttk.Frame(header)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="指定微信窗口").pack(side="left")
        self.window_var = tk.StringVar(value="自动（单个微信主窗口）")
        self.window_box = ttk.Combobox(row, textvariable=self.window_var, state="readonly", width=32,
                                      values=["自动（单个微信主窗口）"])
        self.window_box.pack(side="left", padx=8)
        self.window_box.bind("<<ComboboxSelected>>", self.window_selected)
        self.button(row, "刷新窗口列表", self.refresh_windows).pack(side="left")
        self.layout_var = tk.StringVar(value="微信主窗口")
        ttk.Combobox(row, textvariable=self.layout_var, state="readonly", width=18,
                     values=["微信主窗口", "独立聊天窗口"]).pack(side="left", padx=8)
        self.tabs = ttk.Notebook(outer, takefocus=True)
        self.tabs.enable_traversal()
        self.tabs.pack(fill="both", expand=True)
        reply = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(reply, text="回复建议")
        pages = []
        for title in ("聊天历史", "长期记忆", "我的人设与风格", "双方信息与目标", "接入与说明", "约会策略"):
            host = ScrollPanel(self.tabs)
            self.tabs.add(host, text=title)
            pages.append(host.body)
        history, notes, profile, brief, access, strategy = pages
        self._build_strategy(strategy)

        self.stats_text = tk.StringVar()
        ttk.Label(reply, textvariable=self.stats_text, style="Muted.TLabel").pack(anchor="w")
        self.goal_text = tk.StringVar()
        ttk.Label(reply, textvariable=self.goal_text, wraplength=1040,
                  style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        self.reply_split = ReplySplit(reply)
        self.reply_split.pack(fill="both", expand=True, pady=(14, 0))
        incoming_panel = self.reply_split.left
        outgoing_panel = self.reply_split.right
        incoming_footer = ttk.Frame(incoming_panel)
        incoming_footer.pack(side="bottom", fill="x")
        outgoing_footer = ttk.Frame(outgoing_panel)
        outgoing_footer.pack(side="bottom", fill="x")
        ttk.Label(incoming_panel, text="对方的来信", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(incoming_panel)
        actions.pack(fill="x", pady=(12, 10))
        self.button(actions, "读取当前微信画面", self.ocr).pack(side="left")
        self.button(actions, "导入历史记录", self.import_history).pack(side="left", padx=8)
        self.button(incoming_panel, "使用最近一条对方消息", self.use_latest).pack(anchor="w", pady=(0, 8))
        incoming_hint = ttk.Label(incoming_panel, text="对方这次说了什么？（可直接粘贴，也可从画面中选择）",
                                  wraplength=900, style="Muted.TLabel")
        incoming_hint.pack(anchor="w")
        incoming_panel.bind("<Configure>", lambda event: incoming_hint.configure(wraplength=event.width))
        self.incoming = self.text(incoming_panel, height=4)
        self.incoming.pack(fill="both", expand=True, pady=(8, 8))
        self.incoming.bind("<<Modified>>", self.changed)
        row = ttk.Frame(incoming_footer)
        row.pack(fill="x")
        self.button(row, "预览本次上下文", self.preview).pack(side="left")
        self.button(row, "将这条消息存入历史", self.remember_incoming).pack(side="left", padx=8)
        ttk.Label(incoming_footer, text="完整流程会多次调用所选 AI，使用当前消息、选中的历史与已保存设定，耗时会增加。",
                  wraplength=700, style="Muted.TLabel").pack(anchor="w", pady=(12, 5))
        self.consent = tk.BooleanVar(value=False)
        ttk.Checkbutton(incoming_footer, text="我同意使用这些聊天内容生成回复", variable=self.consent).pack(anchor="w")
        outgoing_head = ttk.Frame(outgoing_panel)
        outgoing_head.pack(fill="x", pady=(0, 8))
        ttk.Label(outgoing_head, text="我的回复建议", style="Section.TLabel").pack(side="left")
        self.button(outgoing_head, "约会策略与执行结果", lambda: self.tabs.select(6)).pack(side="right")
        self.reply = self.text(outgoing_panel, height=5)
        self.reply.pack(fill="both", expand=True)
        row = ttk.Frame(outgoing_footer)
        row.pack(fill="x", pady=(12, 8))
        self.button(row, "生成一条回复", self.generate, primary=True).pack(side="left")
        self.button(row, "复制回复", self.copy_reply).pack(side="left", padx=8)
        ttk.Label(outgoing_footer, text="草稿可修改，复制后自行发送", style="Muted.TLabel").pack(anchor="w")
        self.reply.bind("<<Modified>>", self.reply_changed)
        self.strategy_summary = tk.StringVar(value="约会流程已开启：选技能 → 初稿 → 口吻改写 → 质量与安全复审")
        ttk.Label(outgoing_footer, textvariable=self.strategy_summary, wraplength=800,
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        bar = ttk.Frame(history)
        bar.pack(fill="x", pady=(0, 10))
        self.search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search, width=28).pack(side="left")
        self.button(bar, "搜索本地历史", self.refresh_history).pack(side="left", padx=8)
        self.button(bar, "导入 / 绑定 TXT 或 JSON", self.import_history).pack(side="left")
        self.button(bar, "同步绑定文件", self.sync).pack(side="left", padx=8)
        self.button(history, "连接本机微信历史（检查现有授权）", self.connect_reader).pack(anchor="w", pady=(0, 8))
        self.button(history, "首次授权读取历史", self.authorize_reader).pack(anchor="w", pady=(0, 8))
        ttk.Label(history, text="只显示当前联系人。搜索结果最多显示最近 300 条，完整文字保存在本地。",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.history = self.text(history, height=16, readonly=True)
        self.history.pack(fill="both", expand=True)
        self.source_text = tk.StringVar()
        ttk.Label(history, textvariable=self.source_text, wraplength=950,
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

        ttk.Label(notes, text="只保存你确认过的重要事实，随时可以修改或删除。",
                  font=FONT_SECTION).pack(anchor="w")
        ttk.Label(notes, text="这里保留共同经历、未完成约定及以前保存的记忆。\n"
                  "双方各自的信息与聊天目标，请在“双方信息与目标”页分别填写；历史整理的草稿仍需核对后保存。",
                  style="Muted.TLabel").pack(anchor="w", pady=10)
        self.notes = self.text(notes, height=14)
        self.notes.pack(fill="both", expand=True)
        self.put(self.notes, self.memory.notes(self.contact))
        row = ttk.Frame(notes.action_area)
        row.pack(fill="x", pady=(12, 0))
        self.button(row, "保存已确认的记忆", self.save_notes, primary=True).pack(side="left")
        self.button(row, "从全部已导入历史整理", self.summarize).pack(side="left", padx=8)
        self.notes_notice = tk.StringVar(value="尚未保存的修改不会进入回复上下文。")
        ttk.Label(notes.action_area, textvariable=self.notes_notice, style="Muted.TLabel").pack(anchor="w", pady=8)

        self._build_profile(profile)
        self._build_brief(brief)

        help_text = (
            "恋聊教练基于 OpenDate Skill 开发。可选历史集成来源：chatlog-keeper-skill（MIT）。\n\n"
            "当前画面：读取你已打开的指定私聊，先在本地选择对方的消息。不能用当前画面代表全部历史。\n\n"
            "历史文件：支持直接导入 TXT，也支持 chatlog-keeper 导出的 JSON 和单人 JSON 模板。"
            "TXT 会在本地自动转换，支持昵称一行、日期时间一行、后接消息正文的格式；"
            "昵称与当前设置不一致时，会先让你核对哪一个是我、哪一个是对方。"
            "窗口会核对联系人、会话 ID、双方身份和时间，再将文字存入本地。"
            "绑定的导出文件更新后，每次生成前会自动同步。微信里新来的消息仍需重新导出或读取当前画面。\n\n"
            "可选数据库读取使用单独安装并核验 SHA-256 的 chatlog-keeper v0.3.5。安装后可点击“连接本机微信历史”，"
            "检查已有读取授权，核对当前好友后导出电脑本地可读的历史。数据库读取仍需要可用密钥。"
            "仅安装 skill 无法证明数据库可读；兼容性以当前机器上的实际读取结果为准。"
            "首次没有密钥时，可点击“首次授权读取历史”，按窗口指引退出日常微信后完成授权登录。"
            "此操作只在你点击后启动，读取器不会强行关闭正在运行的微信。\n\n"
            "记忆：保留全部已导入文字。回复结合已确认记忆、最近最多 40 条与关键词命中的旧记录；"
            "可先预览实际上传内容。大量历史可点“从全部已导入历史整理”，分批生成记忆草稿，核对后保存。"
            "这会逐批把已导入文字发送给所选 AI，需要等待，且会使用账号额度。\n\n"
            "双方信息与目标：分别填写我的信息、对方的信息与此人的最终聊天目标。"
            "保存后会按当前对象交给 AI；预览可核对双方身份、归属及目标。空白内容不由 AI 自行补成事实。\n\n"
            "图片、语音和附件正文尚不支持读取；未导入、未同步到电脑的记录也不在覆盖范围内。\n\n"
            "本地历史与记忆保存在 G 盘项目的 data/wechat/wechat-memory.sqlite3，未加入 Git。"
            "回复是草稿，复制和生成都不会标记为已发送。"
        )
        help_box = self.text(access, height=16, readonly=True)
        help_box.pack(fill="both", expand=True)
        self.put(help_box, help_text, readonly=True)
        row = ttk.Frame(access.action_area)
        row.pack(fill="x", pady=(10, 0))
        self.button(row, "保存单人导入模板", self.template).pack(side="left")
        self.button(row, "检查模型接入", self.check_login).pack(side="left", padx=8)
        self.button(row, "重新打开配置引导", self.configure_model).pack(side="left")
        self.button(row, "查看 GitHub 来源", self.open_source).pack(side="left")
        self.status = tk.StringVar(value="准备好了。可以读取当前画面，或先导入历史。")
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(10, 0), before=self.tabs)
        ttk.Label(footer, textvariable=self.status, wraplength=870,
                  style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(footer, text="停止后续整理", command=self.cancelled.set,
                                      state="disabled")
        self.stop_button.pack(side="right")

    def changed(self, _event=None):
        if self.incoming.edit_modified():
            self.packet = None
            self.incoming.edit_modified(False)

    def reply_changed(self, _event=None):
        if self.reply.edit_modified():
            if self.reviewed_text and self.reply.get("1.0", "end").strip() != self.reviewed_text:
                self.strategy_summary.set("你已修改草稿；策略页中的复审结果对应原生成内容。")
            self.reply.edit_modified(False)

    def _build_strategy(self, panel):
        ttk.Label(panel, text="约会技能与策略", font=FONT_SECTION).pack(anchor="w")
        ttk.Label(panel, text="按当前对象独立保存。自动判断来自现有文字，阶段推测不代表对方已同意推进关系。",
                  wraplength=1040, style="Muted.TLabel").pack(anchor="w", pady=(8, 12))
        self.strategy_vars = {}
        mappings = (("intent", "关系方向", INTENTS), ("action", "本次任务", ACTIONS),
                    ("skill", "主要聊天技能", {k: SKILL_LABELS[k] for k in PRIMARY_SKILLS}),
                    ("stage", "交流阶段", STAGES))
        self.strategy_maps = {key: values for key, _, values in mappings}
        for key, label, values in mappings:
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=15).pack(side="left")
            var = tk.StringVar()
            box = ttk.Combobox(row, textvariable=var, values=list(values.values()),
                               state="readonly", width=32)
            box.pack(side="left")
            self.strategy_vars[key] = var
        self.show_strategy(load_strategy(self.memory, self.contact))
        row = ttk.Frame(panel.action_area)
        row.pack(fill="x", pady=(12, 8))
        self.button(row, "保存此对象的约会策略", self.save_strategy, primary=True).pack(side="left")
        self.button(row, "查看原版技能库", self.show_skill_library).pack(side="left", padx=8)
        self.strategy_notice = tk.StringVar(value="完整流程已启用。切换选项后请保存，再生成回复。")
        ttk.Label(panel.action_area, textvariable=self.strategy_notice, wraplength=1040,
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.strategy_report = self.text(panel, height=15, readonly=True)
        self.strategy_report.pack(fill="both", expand=True)
        self.put(self.strategy_report, report_text(latest_report(self.memory, self.contact)), readonly=True)

    def editing_strategy(self):
        data = {}
        for key, var in self.strategy_vars.items():
            data[key] = next(k for k, label in self.strategy_maps[key].items() if label == var.get())
        return StrategySettings(**data)

    def show_strategy(self, settings):
        for key, var in self.strategy_vars.items():
            var.set(self.strategy_maps[key][getattr(settings, key)])

    def save_strategy(self):
        try:
            save_strategy(self.memory, self.contact, self.editing_strategy())
        except (ValueError, HistoryError, OSError) as exc:
            messagebox.showerror("策略未保存", str(exc), parent=self.root)
            return
        self.strategy_notice.set("已保存。后续实际生成会使用这些约会设置。")
        self.packet = None
        self.status.set("当前对象的约会策略已保存。")

    def show_skill_library(self):
        from .skills.engine import SkillsEngine
        engine = SkillsEngine()
        lines = ["原版 14 个技能均已加载。资料筛选用于认识新对象，当前微信私聊不执行自动筛选。",
                 "本人口吻、关系意图、安全边界三个技能始终叠加；其余 10 个聊天技能可自动或手动选择。"]
        lines.extend(f"\n【{SKILL_LABELS[s.name]} / {s.name}】\n{s.body}" for s in engine)
        self.show_text("原版 OpenDate 约会技能库", "\n".join(lines))

    def _build_profile(self, panel):
        ttk.Label(panel, text="这是你自己的说话设定，保存后用于生成回复。",
                  font=FONT_SECTION).pack(anchor="w")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=(12, 8))
        ttk.Label(row, text="自己的微信显示名").pack(side="left")
        self.name_var = tk.StringVar(value=self.my_name)
        ttk.Entry(row, textvariable=self.name_var, width=28).pack(side="left", padx=8)
        ttk.Label(row, text="填写你希望保持的性格与表达习惯。", style="Muted.TLabel").pack(side="left", padx=8)
        ttk.Label(panel, text="当前人设 / 性格与背景").pack(anchor="w")
        self.personality_box = self.text(panel, height=3)
        self.personality_box.pack(fill="both", expand=True, pady=(5, 8))
        self.put(self.personality_box, self.persona.personality)
        ttk.Label(panel, text="聊天风格（例如：自然简短、轻松幽默、直接但温和、少用表情）").pack(anchor="w")
        self.voice_box = self.text(panel, height=2)
        self.voice_box.pack(fill="x", pady=(5, 8))
        self.put(self.voice_box, self.persona.voice)
        ttk.Label(panel, text="不想使用的表达 / 聊天边界").pack(anchor="w")
        self.avoid_box = self.text(panel, height=2)
        self.avoid_box.pack(fill="x", pady=(5, 8))
        self.put(self.avoid_box, self.persona.avoid)
        ttk.Label(panel, text="常用表达或语气示例（作为风格参考，不记作真实已发送消息）").pack(anchor="w")
        self.examples_box = self.text(panel, height=2)
        self.examples_box.pack(fill="x", pady=(5, 8))
        self.put(self.examples_box, self.persona.examples)
        row = ttk.Frame(panel.action_area)
        row.pack(fill="x", pady=(4, 0))
        self.button(row, "保存我的人设与设置", self.save_persona, primary=True).pack(side="left")
        ttk.Label(row, text="对象专属的关系与约定，请放在“长期记忆”页。",
                  style="Muted.TLabel").pack(side="left", padx=12)

    def apply_contact(self):
        self._apply_settings(self.persona, self.my_name)

    def _build_brief(self, panel):
        ttk.Label(panel, text="把双方信息分清，让 AI 按你的目标接话。",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(panel, text="这些内容按聊天对象分别保存。填写已知信息；保存后才会进入 AI 上下文。",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 12))
        columns = ttk.Frame(panel)
        columns.pack(fill="both", expand=True)
        columns.columnconfigure((0, 1), weight=1, uniform="people")
        columns.rowconfigure(1, weight=1)
        self.my_info_title = tk.StringVar()
        self.peer_info_title = tk.StringVar()
        ttk.Label(columns, textvariable=self.my_info_title,
                  font=FONT_STRONG).grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(columns, textvariable=self.peer_info_title,
                  font=FONT_STRONG).grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 6))
        self.my_info_box = self.text(columns, height=8)
        self.my_info_box.grid(row=1, column=0, sticky="nsew")
        self.peer_info_box = self.text(columns, height=8)
        self.peer_info_box.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        ttk.Label(panel, text="我与这个对象聊天的最终目标",
                  font=FONT_STRONG).pack(anchor="w", pady=(16, 5))
        ttk.Label(panel, text="填写希望达成的结果、希望的推进节奏，以及你在意的边界。",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.goal_box = self.text(panel, height=4)
        self.goal_box.pack(fill="x")
        row = ttk.Frame(panel.action_area)
        row.pack(fill="x", pady=(12, 8))
        self.button(row, "保存双方信息与目标", self.save_brief, primary=True).pack(side="left")
        self.button(row, "查看 AI 会读取的已保存信息", self.preview_brief).pack(side="left", padx=8)
        self.brief_notice = tk.StringVar(value="尚未保存的修改不会进入 AI 上下文。")
        ttk.Label(panel.action_area, textvariable=self.brief_notice, style="Muted.TLabel").pack(anchor="w")
        for box in (self.my_info_box, self.peer_info_box, self.goal_box):
            box.bind("<<Modified>>", self.brief_changed)
        self.show_brief(self.memory.brief(self.contact))

    def editing_brief(self):
        return ConversationBrief(my_info=self.my_info_box.get("1.0", "end").strip(),
                                 peer_info=self.peer_info_box.get("1.0", "end").strip(),
                                 goal=self.goal_box.get("1.0", "end").strip())

    def show_brief(self, brief):
        self.put(self.my_info_box, brief.my_info)
        self.put(self.peer_info_box, brief.peer_info)
        self.put(self.goal_box, brief.goal)
        for box in (self.my_info_box, self.peer_info_box, self.goal_box):
            box.edit_modified(False)

    def brief_changed(self, _event=None):
        boxes = (self.my_info_box, self.peer_info_box, self.goal_box)
        if any(box.edit_modified() for box in boxes):
            for box in boxes:
                box.edit_modified(False)
            self.brief_notice.set("有未保存修改。点击“保存双方信息与目标”后，AI 才会读取新内容。")

    def save_brief(self):
        try:
            brief = self.editing_brief()
            self.memory.save_brief(self.contact, brief)
        except (HistoryError, OSError) as exc:
            messagebox.showerror("未保存", str(exc), parent=self.root)
            return
        self.brief_drafts[self.contact] = brief
        for box in (self.my_info_box, self.peer_info_box, self.goal_box):
            box.edit_modified(False)
        self.packet = None
        self.brief_notice.set("已保存。AI 将区分双方信息，并结合此目标生成回复；可预览核对。")
        self.refresh()
        self.status.set(f"已保存与 {self.contact} 的双方信息和聊天目标，后续生成会实际读取。")

    def preview_brief(self):
        settings = reply_settings(self.config, self.project, self.memory)
        people = settings["participants"]
        persona = settings["my_persona"]
        text = ("以下是已保存、会进入 AI 请求的信息。生成时还会加入当前来信和选定历史。\n\n"
                f"【我的信息 · {self.my_name}】\n{people['me']['confirmed_information'] or '未填写'}\n\n"
                f"【对方的信息 · {self.contact}】\n{people['peer']['confirmed_information'] or '未填写'}\n\n"
                f"【我与此人的最终聊天目标】\n{settings['conversation_goal']['desired_outcome'] or '未设置'}\n\n"
                f"【我的人设】\n{persona['personality'] or '未填写'}\n\n"
                f"【聊天风格】\n{persona['voice']}\n\n"
                f"【避免的表达】\n{persona['avoid'] or '未填写'}\n\n"
                f"【语气示例】\n{persona['examples'] or '未填写'}")
        self.show_text("AI 会读取的已保存信息", text)

    def save_persona(self):
        persona = MyPersona(personality=self.personality_box.get("1.0", "end").strip(),
                            voice=self.voice_box.get("1.0", "end").strip(),
                            avoid=self.avoid_box.get("1.0", "end").strip(),
                            examples=self.examples_box.get("1.0", "end").strip())
        self._apply_settings(persona, self.name_var.get())

    def _apply_settings(self, persona, my_name):
        if self.busy:
            return
        target = self.target_var.get().strip()
        try:
            updated = save_settings(self.project, self.config, persona, target, my_name)
        except (ValueError, HistoryError, OSError) as exc:
            messagebox.showerror("设置未保存", str(exc), parent=self.root)
            return
        previous = self.contact
        self.strategy_drafts[previous] = self.editing_strategy()
        self.brief_drafts[previous] = self.editing_brief()
        self.note_drafts[previous] = self.notes.get("1.0", "end").strip()
        self.chat_drafts[previous] = (self.incoming.get("1.0", "end").strip(),
                                     self.reply.get("1.0", "end").strip(), self.output_revision)
        self.config, self.persona = updated, persona
        self.contact, self.my_name = updated.wechat.contact, updated.wechat.my_name
        self.target_var.set(self.contact)
        self.name_var.set(self.my_name)
        if previous != self.contact:
            self.reviewed_text = ""
            self.show_strategy(self.strategy_drafts.get(self.contact, load_strategy(self.memory, self.contact)))
            self.strategy_notice.set("当前对象的策略独立保存；未保存选择仍留在本窗口。")
            self.put(self.strategy_report, report_text(latest_report(self.memory, self.contact)), readonly=True)
            self.strategy_summary.set("已切换对象，请在约会策略页核对当前设置。")
            self.show_brief(self.brief_drafts.get(self.contact, self.memory.brief(self.contact)))
            self.brief_notice.set("当前对象的信息与目标独立保存；草稿需点击保存后才供 AI 使用。")
            self.put(self.notes, self.note_drafts.get(self.contact, self.memory.notes(self.contact)))
            incoming, reply, revision = self.chat_drafts.get(self.contact, ("", "", ""))
            self.put(self.incoming, incoming)
            self.put(self.reply, reply)
            self.output_revision = revision
            self.consent.set(False)
            self.search.set("")
            self.packet = None
            self.window_var.set("自动（单个微信主窗口）")
            self.layout_var.set("微信主窗口")
            self.notes_notice.set("当前对象的记忆独立保存；其他对象未保存的编辑仍留在本窗口。")
        self.refresh()
        self.status.set(f"已保存。当前聊天对象：{self.contact}。读取前请在微信打开对应私聊，或指定该聊天窗口。")

    def refresh_windows(self):
        from .connectors.wechat_ocr import list_wechat_windows
        try:
            windows = list_wechat_windows()
        except Exception as exc:
            messagebox.showerror("无法列出微信窗口", str(exc), parent=self.root)
            return
        self.window_choices = {f"{item['title']} · 窗口 {i}": item
                               for i, item in enumerate(windows, 1)}
        values = ["自动（单个微信主窗口）", *self.window_choices]
        self.window_box.configure(values=values)
        if self.window_var.get() not in values:
            self.window_var.set(values[0])
        self.status.set(f"找到 {len(windows)} 个微信窗口。选择后，读取时仍会核对当前聊天对象标题。")

    def window_selected(self, _event=None):
        choice = self.window_choices.get(self.window_var.get())
        self.layout_var.set("独立聊天窗口" if choice and choice["title"] == self.contact else "微信主窗口")

    def refresh(self):
        self.identity_text.set(f"当前聊天：{self.contact}   /   我：{self.my_name}   /   AI：{self.config.llm.provider}")
        self.persona_text.set(f"我的人设：{self.persona.personality.strip()[:70] or '尚未填写'}"
                              f"   ｜   聊天风格：{self.persona.voice.strip()[:90]}")
        self.target_box.configure(values=sorted({self.contact, *self.memory.contacts()}))
        self.my_info_title.set(f"我的信息 · {self.my_name}")
        self.peer_info_title.set(f"对方的信息 · {self.contact}")
        goal = self.memory.brief(self.contact).goal
        self.goal_text.set(f"当前最终目标：{goal[:90] if goal else '未设置（在“双方信息与目标”页编辑）'}")
        count, first, last = self.memory.stats(self.contact)
        self.stats_text.set(f"本地已记住 {count} 条文字  ·  {local_date(first)} → {local_date(last)}"
                            "  ·  全部微信历史覆盖：未验证")
        source = self.memory.source(self.contact)
        self.source_text.set(f"已绑定导出文件：{source[0]}\n生成前会同步文件更新；不直接监听微信数据库。"
                             if source else "尚未绑定历史文件。当前画面与手动保存的消息也可积累为本地记忆。")
        self.refresh_history()

    def refresh_history(self):
        records = self.memory.recent(self.contact, 300, self.search.get().strip())
        value = "\n\n".join(f"{local_date(r.timestamp)}  {'我' if r.sender == 'me' else self.contact}\n{r.text}"
                             for r in records)
        self.put(self.history, value or "暂无记录。导入历史后，会在这里按时间显示。", readonly=True)

    def run(self, work, done, stop_title="停止后续整理"):
        if self.busy:
            return
        self.busy = True
        self.cancelled.clear()
        for control in self.controls:
            control.configure(state="disabled")
        self.stop_button.configure(state="normal", text=stop_title)
        def worker():
            try:
                value = work()
                self.events.put(("done", done, value))
            except Exception as exc:
                # Only local, expected diagnostics; no raw subprocess output.
                self.events.put(("error", None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def progress(self, text):
        self.events.put(("progress", None, text))

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, callback, value = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(value)
                    continue
                self.busy = False
                for control in self.controls:
                    control.configure(state="normal")
                self.stop_button.configure(state="disabled")
                if kind == "error":
                    self.status.set("这一步未完成，详情见提示。")
                    self.put(self.strategy_report, report_text(latest_report(self.memory, self.contact)), readonly=True)
                    self.strategy_summary.set("当前操作未完成，请查看提示与策略页中的执行记录。")
                    messagebox.showerror("未完成", value, parent=self.root)
                else:
                    try:
                        callback(value)
                    except Exception as exc:
                        messagebox.showerror("未完成", str(exc), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show_text(self, title, text):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("850x640")
        box = self.text(window, readonly=True)
        box.pack(fill="both", expand=True, padx=14, pady=14)
        self.put(box, text, readonly=True)
        return window

    def import_history(self):
        path = filedialog.askopenfilename(parent=self.root, title=f"选择与 {self.contact} 的聊天 TXT 或 JSON",
                                          initialdir=str(self.project / "data"),
                                          filetypes=[("聊天记录 TXT / JSON", "*.txt *.json"),
                                                     ("聊天 TXT", "*.txt"), ("聊天 JSON", "*.json")])
        if not path:
            return
        self.status.set("正在本地核对历史文件，不上传模型…")
        self.load_import(Path(path))

    def load_import(self, path, txt_senders=None):
        def work():
            try:
                return read_history(path, self.contact, self.my_name, txt_senders=txt_senders)
            except TxtIdentityError as exc:
                return exc
        def done(result):
            if isinstance(result, TxtIdentityError):
                self.map_txt_identity(path, result.participants)
            else:
                self.confirm_import(path, result)
        self.run(work, done)

    def map_txt_identity(self, path, participants):
        window = tk.Toplevel(self.root)
        window.title("核对 TXT 中的双方身份")
        window.transient(self.root)
        window.grab_set()
        panel = ttk.Frame(window, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"当前对象：{self.contact}　本人显示名：{self.my_name}",
                  font=FONT_STRONG).pack(anchor="w")
        ttk.Label(panel, text="TXT 中使用了不同的昵称。请确认这是当前对象的私聊，再对应双方身份。\n"
                  "不会根据文件名或聊天内容猜测身份，也不会改动原文件。", wraplength=900,
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 16))
        mine = tk.StringVar(value=self.my_name if self.my_name in participants else "")
        peer = tk.StringVar(value=self.contact if self.contact in participants else "")
        for label, var in (("TXT 中哪位是我？", mine), ("TXT 中哪位是对方？", peer)):
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=8)
            ttk.Label(row, text=label, width=18).pack(side="left")
            ttk.Combobox(row, textvariable=var, values=participants, state="readonly", width=30).pack(side="left")
        def accept():
            names = mine.get(), peer.get()
            if names[0] not in participants or names[1] not in participants or names[0] == names[1]:
                messagebox.showerror("请核对身份", "请分别选择不同的本人和对方昵称。", parent=window)
                return
            window.destroy()
            self.status.set("已确认 TXT 身份对应，正在本地转换并核对…")
            self.load_import(path, names)
        ttk.Button(panel, text="确认对应，继续预览", style="Primary.TButton", command=accept).pack(anchor="e", pady=(16, 0))

    def confirm_import(self, path, batches):
        window = tk.Toplevel(self.root)
        window.title("核对会话后导入")
        window.geometry("760x520")
        window.transient(self.root)
        window.grab_set()
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"好友：{self.contact}　我：{self.my_name}\n请选择你确认属于此人的会话 ID。",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        choice = ttk.Combobox(panel, state="readonly", values=[
            f"{b.conversation}  /  {len(b.records)} 条文字" for b in batches])
        choice.pack(fill="x", pady=12)
        choice.current(0)
        preview = self.text(panel, readonly=True)
        preview.pack(fill="both", expand=True)
        def update(_event=None):
            batch = batches[choice.current()]
            rows = sorted(batch.records, key=lambda r: r.timestamp)
            shown = rows[:2] + rows[-3:] if len(rows) > 5 else rows
            mine = sum(r.sender == "me" for r in rows)
            value = f"{len(rows)} 条文字：我 {mine} 条，对方 {len(rows)-mine} 条。跳过 {batch.skipped} 条非文字记录。\n"
            if rows:
                value += f"时间范围：{local_date(rows[0].timestamp)} 至 {local_date(rows[-1].timestamp)}\n"
            if batch.sender_names:
                value += (f"TXT 已自动转换；文件中的我：{batch.sender_names[0]}，对方：{batch.sender_names[1]}。\n"
                          "无时区的 TXT 时间按北京时间读取。昵称对应仅用于此对象的文件同步。\n")
            value += "\n"
            value += "\n\n".join(f"{local_date(r.timestamp)}  {'我' if r.sender == 'me' else self.contact}\n{r.text}"
                                   for r in shown)
            self.put(preview, value, readonly=True)
        choice.bind("<<ComboboxSelected>>", update)
        update()
        def accept():
            batch = batches[choice.current()]
            window.destroy()
            def work():
                added = self.memory.import_batch(self.contact, batch, str(path))
                self.memory.bind(self.contact, path, batch.conversation, batch.sender_names)
                return added
            def done(added):
                self.refresh()
                self.status.set(f"导入完成：新增 {added} 条文字。重复记录已跳过，尚未上传。")
            self.run(work, done)
        ttk.Button(panel, text="确认身份正确，导入并绑定", style="Primary.TButton",
                   command=accept).pack(anchor="e", pady=(12, 0))

    def sync(self):
        self.status.set("正在同步已绑定的本地历史…")
        self.run(lambda: self.memory.sync(self.contact, self.my_name), self.synced)

    def connect_reader(self):
        from .wechat_reader import LocalHistoryReader
        window = tk.Toplevel(self.root)
        window.title("连接本机微信历史")
        window.transient(self.root)
        panel = ttk.Frame(window, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="先检查现有本机读取授权。只导出你核对过的当前好友，不会自动提取密钥。",
                  wraplength=650).pack(anchor="w")
        data_root = tk.StringVar(value=self.config.wechat.history_data_root)
        ttk.Label(panel, text="微信数据目录（留空自动定位；可选择 xwechat_files 文件夹）").pack(anchor="w", pady=(12, 5))
        row = ttk.Frame(panel)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=data_root, width=55).pack(side="left", fill="x", expand=True)
        def browse():
            folder = filedialog.askdirectory(parent=window, title="选择 xwechat_files 文件夹")
            if folder:
                data_root.set(folder)
        ttk.Button(row, text="选择文件夹", command=browse).pack(side="left", padx=8)
        def check():
            folder = data_root.get().strip()
            window.destroy()
            self.status.set("正在本地检查已有读取授权与联系人目录，不读取聊天正文…")
            reader = LocalHistoryReader(self.project)
            self.run(lambda: reader.directory(self.contact, folder),
                     lambda choices: self.choose_reader(reader, choices, folder))
        ttk.Button(panel, text="检查连接", command=check, style="Primary.TButton").pack(anchor="e", pady=(16, 0))

    def authorize_reader(self):
        from .wechat_reader import LocalHistoryReader
        window = tk.Toplevel(self.root)
        window.title("首次本机历史读取授权")
        window.transient(self.root)
        window.grab_set()
        panel = ttk.Frame(window, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="先退出微信，再打开一次性的授权登录窗口。",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(panel, text="1. 在任务栏右下角微信图标上右键，选择“退出微信”。\n"
                  "2. 确认数据目录后，点击下面的“开始授权”。\n"
                  "3. 如果弹出微信登录窗口，在窗口内完成登录。\n\n"
                  "授权会获取本人本机数据库的读取密钥，密钥保存在项目私有目录，不交给模型。\n"
                  "本操作不会上传聊天；只有随后点击生成或整理记忆，才会使用所选 AI。\n"
                  "读取器不会强行退出日常微信；版本不兼容或验证失败会停止。",
                  wraplength=700, justify="left").pack(anchor="w", pady=14)
        folder = tk.StringVar(value=self.config.wechat.history_authorization_root
                              or self.config.wechat.history_data_root)
        ttk.Entry(panel, textvariable=folder, width=65).pack(fill="x")
        def start():
            data_root = folder.get().strip()
            if not data_root or not Path(data_root).is_dir():
                messagebox.showerror("请选择数据目录", "请填写已存在的微信数据或当前账号目录。", parent=window)
                return
            window.destroy()
            reader = LocalHistoryReader(self.project)
            self.status.set("正在进行本机授权，最多等待 5 分钟；若弹出微信窗口，请在窗口内完成登录。")
            def done(result):
                if result.get("ok") is True:
                    self.status.set("读取密钥已通过本机校验。现在可以连接并核对指定好友的历史。")
                    messagebox.showinfo("授权完成", "本机读取授权已完成。请点击“连接本机微信历史”继续。", parent=self.root)
                    return
                errors = {
                    "client_running": "日常微信仍在运行。请先从右下角托盘退出微信，再点击开始授权。",
                    "timed_out": "授权等待已超时。此次未完成读取授权；可以稍后重新尝试。",
                    "cancelled": "本次授权已停止。",
                    "helper_unavailable": "当前微信版本的授权读取未成功，需要继续适配；尚未接入完整历史。",
                    "source_unavailable": "未找到可用于校验的聊天数据库，请核对数据目录。",
                    "verification_failed": "未能验证本机数据库密钥，尚未接入完整历史。",
                    "active_operation_exists": "已有授权任务在进行，请等待它结束。",
                }
                messagebox.showinfo("授权尚未完成", errors.get(result.get("error_code"),
                                    "授权未完成。请先保留现有数据，联系助手继续检查。"), parent=self.root)
                self.status.set("首次读取授权尚未完成；仍可使用当前画面和已有 TXT / JSON。")
            self.run(lambda: reader.authorize(data_root, cancelled=self.cancelled.is_set),
                     done, stop_title="停止本次授权")
        ttk.Button(panel, text="微信已退出，开始授权", command=start,
                   style="Primary.TButton").pack(anchor="e", pady=(16, 0))

    def choose_reader(self, reader, choices, folder):
        window = tk.Toplevel(self.root)
        window.title("核对历史读取对象")
        window.transient(self.root)
        window.grab_set()
        panel = ttk.Frame(window, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"即将导出与 {self.contact} 的电脑本地可读历史。\n请核对本机账号和会话 ID；"
                  "未同步到电脑、不可解密及附件内容不保证覆盖。", wraplength=680).pack(anchor="w")
        choice = ttk.Combobox(panel, state="readonly", width=75, values=[
            f"账号 {item['account_id']} / 私聊 {item['conversation_id']} / {item.get('message_count', '?')} 条"
            for item in choices])
        choice.current(0)
        choice.pack(fill="x", pady=16)
        def export():
            selected = choices[choice.current()]
            window.destroy()
            self.status.set("正在本地导出已确认好友的可读历史，不上传模型…")
            def work():
                path = reader.export(selected, folder)
                return path, read_history(path, self.contact, self.my_name)
            self.run(work, lambda result: self.confirm_import(*result))
        ttk.Button(panel, text="确认对象，读取历史", command=export,
                   style="Primary.TButton").pack(anchor="e")

    def synced(self, count):
        self.refresh()
        self.status.set(f"本地同步完成：新增 {count} 条。文件未更新时不会重复导入。")

    def use_latest(self):
        rows = self.memory.recent(self.contact, 1)
        if not rows or rows[-1].sender != "peer":
            messagebox.showinfo("没有待回复消息", "最新历史不是对方发来的文字。请粘贴本次消息或读取当前画面。",
                                parent=self.root)
            return
        self.put(self.incoming, rows[-1].text)
        self.status.set("已填入对方最近一条消息，请确认这仍是你要回复的内容。")

    def ocr(self):
        from .connectors.wechat_ocr import read_local_ocr_lines
        self.status.set("正在本地读取指定好友的当前微信画面…")
        choice = self.window_choices.get(self.window_var.get())
        hwnd = choice["handle"] if choice else None
        layout = "detached" if self.layout_var.get() == "独立聊天窗口" else "main"
        contact = self.contact
        self.run(lambda: read_local_ocr_lines(contact, self.project / "work/ocr",
                                             window_handle=hwnd, layout=layout), self.choose_ocr)

    def choose_ocr(self, lines):
        window = tk.Toplevel(self.root)
        window.title("选择对方这次发来的文字")
        window.geometry("780x580")
        window.transient(self.root)
        window.grab_set()
        ttk.Label(window, text="画面中的内容可能包括日期和自己的话。请选择属于对方同一条消息的文字行。",
                  wraplength=720).pack(padx=18, pady=14, anchor="w")
        listing = tk.Listbox(window, selectmode="extended", exportselection=False,
                             font=("Microsoft YaHei UI", 11))
        listing.pack(fill="both", expand=True, padx=18)
        for line in lines:
            listing.insert("end", line)
        def select():
            from .connectors.wechat_ocr import select_ocr_message
            indices = listing.curselection()
            if not indices:
                return
            try:
                text = select_ocr_message(lines, ",".join(str(i + 1) for i in indices))
            except Exception as exc:
                messagebox.showerror("请重新选择", str(exc), parent=window)
                return
            self.put(self.incoming, text)
            window.destroy()
            self.status.set("已填入你选择的文字，可以先修改和预览；尚未上传。")
        ttk.Button(window, text="这些是对方的话，填入回复框", command=select,
                   style="Primary.TButton").pack(padx=18, pady=16, anchor="e")

    def remember_incoming(self):
        text = self.incoming.get("1.0", "end").strip()
        if not text:
            return
        window = tk.Toplevel(self.root)
        window.title("保存一条真实聊天")
        window.transient(self.root)
        panel = ttk.Frame(window, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="请填写这条消息实际发生的时间和发送人。保存的是聊天，不是生成的草稿。").pack(anchor="w")
        stamp = tk.StringVar(value=datetime.now().astimezone().isoformat(timespec="seconds"))
        ttk.Entry(panel, textvariable=stamp, width=45).pack(fill="x", pady=10)
        role = ttk.Combobox(panel, state="readonly", values=[self.contact, "我"])
        role.current(0)
        role.pack(fill="x")
        def save():
            from .wechat_memory import normalize_time
            try:
                record = ChatRecord(normalize_time(stamp.get()), "peer" if role.current() == 0 else "me",
                                    text, f"manual:{uuid.uuid4()}")
                self.memory.import_batch(self.contact, HistoryBatch(self.contact, self.contact, [record]),
                                         "人工核对")
            except (HistoryError, ValueError) as exc:
                messagebox.showerror("未保存", str(exc), parent=window)
                return
            window.destroy()
            self.refresh()
            self.status.set("已将核对过的这条消息保存到当前好友的本地历史。")
        ttk.Button(panel, text="确认并保存", command=save).pack(anchor="e", pady=(14, 0))

    def build_packet(self):
        self.memory.sync(self.contact, self.my_name)
        return build_reply_context(self.config, self.project, self.memory,
                                   self.incoming.get("1.0", "end").strip())

    def preview(self):
        try:
            self.packet = self.build_packet()
        except (HistoryError, OSError) as exc:
            messagebox.showerror("无法预览", str(exc), parent=self.root)
            return
        self.refresh()
        self.show_text("本次实际发送给 AI 的上下文", context_preview(self.packet))
        self.status.set("已展示实际上下文。近期聊天和旧记录会按预算选取，完整历史留在本地。")

    def generate(self):
        if not self.consent.get():
            messagebox.showinfo("使用聊天内容", "请勾选使用聊天内容生成回复，或先点预览查看具体内容。", parent=self.root)
            return
        try:
            # Freeze exactly the current input; background workers never read Tk widgets.
            packet = self.build_packet()
        except (HistoryError, OSError) as exc:
            messagebox.showerror("无法生成", str(exc), parent=self.root)
            return
        revision = self.incoming.get("1.0", "end").strip()
        self.reviewed_text = ""
        self.put(self.reply, "")
        self.put(self.strategy_report, "正在执行本次约会策略，尚未通过复审。", readonly=True)
        self.strategy_summary.set("正在判断阶段、选择技能并逐步复审…")
        self.status.set("AI 正在结合本地记忆生成回复，通常需要等待一会儿…")
        def done(value):
            self.reviewed_text = value.text
            self.put(self.reply, value.text)
            self.put(self.strategy_report, report_text(value.report), readonly=True)
            if value.blocked:
                self.strategy_summary.set("本次已暂停：" + value.report.get("notice", "请查看策略页。"))
                self.status.set("本次未给出可用草稿，请在约会策略页查看检查结果。")
                self.tabs.select(6)
                return
            self.strategy_summary.set("本次采用：" + SKILL_LABELS[value.report['skill']] +
                                      " · 口吻改写、质量与安全检查均已通过")
            self.output_revision = revision
            self.status.set("回复已生成，可修改后复制。" if self.incoming.get("1.0", "end").strip() == revision
                            else "回复基于生成开始时的消息；输入已改变，请核对或重新生成。")
        self.run(lambda: generate_reply_with_strategy(self.config, self.project, packet,
                                                     self.progress, self.cancelled.is_set), done,
                 stop_title="停止后续策略步骤")

    def copy_reply(self):
        value = self.reply.get("1.0", "end").strip()
        if not value:
            return
        if self.incoming.get("1.0", "end").strip() != self.output_revision:
            if not messagebox.askyesno("内容已改变", "这条回复对应的是修改前的消息，仍要复制吗？", parent=self.root):
                return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update()
        self.status.set("已复制到剪贴板。请到微信粘贴、核对后自行发送。")

    def save_notes(self):
        try:
            self.memory.save_notes(self.contact, self.notes.get("1.0", "end").strip())
        except HistoryError as exc:
            messagebox.showerror("未保存", str(exc), parent=self.root)
            return
        self.notes_notice.set("已保存。后续回复会使用这些已确认的记忆；清空后保存即可删除。")
        self.status.set("长期记忆已保存在本地。")

    def summarize(self):
        count = self.memory.stats(self.contact)[0]
        if not count:
            messagebox.showinfo("先导入历史", "暂无可整理的聊天历史。", parent=self.root)
            return
        if not messagebox.askyesno("整理全部已导入文字",
                                  f"将把当前好友已导入的 {count} 条文字分批发送给所选 AI 整理。\n"
                                  "这会使用账号额度，完成后请核对并保存记忆。\n"
                                  "编辑区未保存内容将被草稿替换，已保存记忆会保留。继续？", parent=self.root):
            return
        self.status.set("正在整理全部已导入文字…")
        def done(value):
            self.put(self.notes, value)
            self.notes_notice.set("这是自动整理的草稿，尚未保存。请核对日期、双方身份和事实后再保存。")
            self.tabs.select(2)
            self.status.set("记忆草稿已完成，等待你核对。")
        self.run(lambda: draft_memory(self.config, self.project, self.memory,
                                     self.progress, self.cancelled.is_set), done)

    def template(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="保存单人 JSON 模板",
                                            initialdir=str(self.project / "data"),
                                            initialfile="单人聊天导入模板.json", defaultextension=".json")
        if path:
            data = {"contact": self.contact, "messages": [
                {"timestamp": "2026-01-01T10:00:00+08:00", "sender": "peer",
                 "text": "请替换为对方真实消息，并修改实际时间。"},
                {"timestamp": "2026-01-01T10:01:00+08:00", "sender": "me",
                 "text": "请替换为你真实发出的消息，并修改实际时间。"}]}
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set("已保存模板。示例文字不是真实聊天，请替换后再导入。")

    def check_login(self):
        self.status.set("正在检查本机模型接入配置，不读取或上传聊天…")
        def work():
            if self.config.llm.provider == "codex":
                CodexCLIBackend(work_dir=str(self.project / "work/codex-runtime")).ensure_ready()
            else:
                from .llm.router import LLMRouter
                from .wechat_credentials import provider_secrets
                secrets = provider_secrets(self.project, self.config.data_dir, self.config.llm.provider)
                LLMRouter.from_config(self.config.llm, secrets).ensure_ready(secrets)
        self.run(work, lambda _: self.status.set("本机模型接入配置已就绪；实际网络和服务额度仍以生成结果为准。"))

    def configure_model(self):
        import subprocess
        subprocess.Popen([sys.executable, "-m", "opendate.wechat_gui", str(self.project), "--setup"],
                         cwd=self.project, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.status.set("已在新窗口打开配置引导；完成后请在新窗口继续使用。")

    def open_source(self):
        import webbrowser
        webbrowser.open("https://github.com/15628925702/chatlog-keeper-skill")

    def close(self):
        if self.busy and not messagebox.askyesno("任务还在进行", "关闭后将丢弃尚未显示的结果。是否关闭？",
                                                parent=self.root):
            return
        self.cancelled.set()
        self.closed = True
        self.root.destroy()


def main():
    enable_dpi_awareness()
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    os.chdir(project)
    root = tk.Tk()
    def open_main():
        window = WeChatWindow(root, project)
        window.refresh_windows()
    try:
        if not (project / "config.wechat.yaml").exists() or "--setup" in sys.argv[2:]:
            from .wechat_setup import SetupWizard
            SetupWizard(root, project, open_main)
        else:
            open_main()
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("恋聊教练未能启动", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
