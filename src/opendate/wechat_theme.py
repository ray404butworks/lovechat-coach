"""Hallmark Hum adapted to native Tk; Open Claude UI split-pane interaction.

References: Nutlope/hallmark (Hum, app-screen rules),
Caho1/open-claude-ui/code-examples/ArtifactsSplitPaneResizer.tsx (MIT).
Native equivalents replace CSS; no browser or React runtime is introduced.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

FONT_FAMILY = "Microsoft YaHei UI"
FONT_BODY = (FONT_FAMILY, 10)
FONT_EDITOR = (FONT_FAMILY, 11)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_TITLE = (FONT_FAMILY, 19, "bold")
FONT_SECTION = (FONT_FAMILY, 12, "bold")
FONT_STRONG = (FONT_FAMILY, 10, "bold")

LIGHT = {"paper": "#F7F5EC", "panel": "#F0EDE2", "field": "#FDFCF7",
         "ink": "#242830", "muted": "#616672", "line": "#D8D5CA", "edge": "#858574",
         "accent": "#F1D45B", "accent_hover": "#E6C33F", "accent_press": "#D9B22C",
         "accent_ink": "#242830", "hover": "#E7E3D6", "pressed": "#DAD6C8",
         "focus": "#25639B", "disabled": "#E0DDD2", "disabled_ink": "#75766F",
         "selection": "#EBDFAC", "success": "#286144", "error": "#A43C32"}
DARK = {"paper": "#1D2026", "panel": "#272B32", "field": "#30353D",
        "ink": "#F1F0E8", "muted": "#BBBDBF", "line": "#454B55", "edge": "#8F959E",
        "accent": "#F1D45B", "accent_hover": "#F6DE7F", "accent_press": "#D9B22C",
        "accent_ink": "#242830", "hover": "#393F48", "pressed": "#454C56",
        "focus": "#90BCE7", "disabled": "#383D45", "disabled_ink": "#9399A2",
        "selection": "#645D3A", "success": "#9AC8AA", "error": "#F0A69C"}


def enable_dpi_awareness():
    """Use physical desktop geometry, avoiding clipped DPI-virtualized windows."""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def _rounded_image(root, fill, outline, *, radius=8, size=36):
    image = tk.PhotoImage(master=root, width=size, height=size)
    for y in range(size):
        edge_y = min(y, size - y - 1)
        inset = 0 if edge_y >= radius else math.ceil(radius - math.sqrt(radius**2 - (radius-edge_y)**2))
        if size - 2*inset > 0:
            image.put(outline, to=(inset, y, size-inset, y+1))
        inner_y = min(y-2, size-y-3)
        inner_radius = radius-2
        if inner_y >= 0:
            inner = 2 if inner_y >= inner_radius else 2+math.ceil(
                inner_radius-math.sqrt(inner_radius**2-(inner_radius-inner_y)**2))
            if size - 2*inner > 0:
                image.put(fill, to=(inner, y, size-inner, y+1))
    return image


class NativeTheme:
    def __init__(self, root):
        self.root, self.dark = root, False
        self.colors = LIGHT
        self.images = []
        root._opendate_theme = self
        self.apply(False)

    def apply(self, dark=False):
        self.dark = dark
        self.colors = p = DARK if dark else LIGHT
        root = self.root
        root.title("恋聊教练 · " + ("夜间" if dark else "暖纸") + "主题")
        root.configure(bg=p["paper"])
        root.option_add("*Font", FONT_BODY)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=FONT_BODY, background=p["paper"], foreground=p["ink"])
        style.configure("TFrame", background=p["paper"])
        style.configure("Panel.TFrame", background=p["panel"])
        style.configure("TLabel", background=p["paper"], foreground=p["ink"])
        style.configure("Muted.TLabel", foreground=p["muted"], font=FONT_SMALL)
        style.configure("Title.TLabel", font=FONT_TITLE)
        style.configure("Section.TLabel", font=FONT_SECTION)
        style.configure("Card.TFrame", background=p["field"])
        style.configure("Card.TLabel", background=p["field"], foreground=p["ink"])
        # Image-backed ttk elements preserve native keyboard, focus and disabled state.
        for name, primary in (("TButton", False), ("Primary.TButton", True)):
            fills = [p["accent"], p["accent_hover"], p["accent_press"]] if primary else [p["panel"], p["hover"], p["pressed"]]
            images = [_rounded_image(root, color, color) for color in fills]
            focus = _rounded_image(root, fills[0], p["focus"])
            disabled = _rounded_image(root, p["disabled"], p["disabled"])
            self.images.extend([*images, focus, disabled])
            element = f"OpenDate{len(self.images)}.border"
            style.element_create(element, "image", images[0], ("disabled", disabled),
                                 ("pressed", images[2]), ("focus", focus), ("active", images[1]),
                                 border=10, sticky="nsew")
            style.layout(name, [(element, {"sticky":"nsew", "children":[
                ("Button.padding", {"sticky":"nsew", "children":[("Button.label", {"sticky":"nsew"})]})]})])
            style.configure(name, padding=(13, 8), font=FONT_STRONG if primary else FONT_BODY,
                            foreground=p["accent_ink"] if primary else p["ink"], anchor="center",
                            background=p["paper"])
            style.map(name, foreground=[("disabled", p["disabled_ink"])])
        for name in ("TEntry", "TCombobox"):
            style.configure(name, padding=(10, 7), fieldbackground=p["field"], background=p["panel"],
                            foreground=p["ink"], bordercolor=p["edge"], lightcolor=p["edge"],
                            darkcolor=p["edge"], arrowcolor=p["ink"], selectbackground=p["selection"],
                            selectforeground=p["ink"])
            style.map(name, fieldbackground=[("readonly",p["field"]),("disabled",p["disabled"])],
                      foreground=[("disabled",p["disabled_ink"])],
                      bordercolor=[("focus",p["focus"])], lightcolor=[("focus",p["focus"])],
                      darkcolor=[("focus",p["focus"])])
        root.option_add("*TCombobox*Listbox.background", p["field"])
        root.option_add("*TCombobox*Listbox.foreground", p["ink"])
        root.option_add("*TCombobox*Listbox.selectBackground", p["selection"])
        root.option_add("*TCombobox*Listbox.selectForeground", p["ink"])
        style.configure("TNotebook", background=p["paper"], borderwidth=0, tabmargins=(0,8,0,0),
                        bordercolor=p["line"], lightcolor=p["line"], darkcolor=p["line"])
        style.configure("TNotebook.Tab", padding=(12,9), background=p["panel"], borderwidth=0,
                        font=FONT_BODY, foreground=p["muted"], bordercolor=p["line"],
                        lightcolor=p["line"], darkcolor=p["line"])
        style.map("TNotebook.Tab", background=[("selected",p["ink"]),("active",p["hover"])],
                  foreground=[("selected",p["paper"]),("active",p["ink"])])
        style.configure("TCheckbutton", background=p["paper"], foreground=p["ink"], padding=(0,5))
        style.map("TCheckbutton", background=[("active",p["paper"])],
                  indicatorbackground=[("selected",p["accent"])], indicatorforeground=[("selected",p["accent_ink"])])
        style.configure("Vertical.TScrollbar", background=p["line"], troughcolor=p["field"],
                        borderwidth=0, arrowcolor=p["muted"])
        style.configure("TSeparator", background=p["line"])
        style.configure("Focus.TFrame", background=p["focus"])
        self._recolor(root)

    def _recolor(self, widget):
        p = self.colors
        if isinstance(widget, tk.Text):
            widget.configure(bg=p["field"], fg=p["ink"], insertbackground=p["ink"],
                             selectbackground=p["selection"], selectforeground=p["ink"],
                             highlightbackground=p["edge"], highlightcolor=p["focus"])
        elif isinstance(widget, (tk.Frame, tk.Canvas)):
            widget.configure(bg=p["paper"])
        elif isinstance(widget, tk.Scrollbar) and not isinstance(widget, ttk.Scrollbar):
            widget.configure(bg=p["panel"], troughcolor=p["field"], activebackground=p["line"])
        for child in widget.winfo_children():
            self._recolor(child)


class ReplySplit(ttk.Frame):
    """4px dead-zone, clamped widths, pointer capture, Esc rollback, 50/50 reset.

    Native adaptation of Open Claude UI's split-pane interactions. There is no
    iframe or collapse feature here, so neither iframe shielding nor physics
    animations are claimed. Keeps both reply editors reachable at all times.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.left, self.right = ttk.Frame(self), ttk.Frame(self)
        self.handle = ttk.Frame(self, cursor="sb_h_double_arrow", takefocus=True)
        grip = ttk.Separator(self.handle, orient="vertical")
        grip.place(relx=0.5, rely=0.15, relheight=0.7, width=1)
        grip.bindtags((str(self.handle), *grip.bindtags()))
        self.ratio = 0.43
        self.density = max(1.0, self.winfo_fpixels("1i")/96)
        self.start = None
        self.dragging = False
        self.handle.bind("<ButtonPress-1>", self.press)
        self.handle.bind("<B1-Motion>", self.drag)
        self.handle.bind("<ButtonRelease-1>", self.release)
        self.handle.bind("<Double-Button-1>", self.reset)
        self.handle.bind("<Escape>", self.cancel)
        self.handle.bind("<Left>", lambda _: self.move(-0.025))
        self.handle.bind("<Right>", lambda _: self.move(0.025))
        self.handle.bind("<Home>", self.reset)
        self.handle.bind("<FocusIn>", lambda _: self.handle.configure(style="Focus.TFrame"))
        self.handle.bind("<FocusOut>", lambda _: self.handle.configure(style="TFrame"))
        self.bind("<Configure>", self.layout)

    @staticmethod
    def clamp(ratio, width, density=1.0):
        # At narrow window sizes both regions keep a useful minimum width.
        left = min(360*density, max(0, (width-12)/2))
        right = min(420*density, max(0, (width-12)/2))
        return max(left/max(1,width), min(1-right/max(1,width), ratio))

    def layout(self, _event=None):
        width = max(1, self.winfo_width())
        self.ratio = self.clamp(self.ratio, width, self.density)
        split = round(width*self.ratio)
        gap = round(10*self.density)
        self.left.place(x=0, y=0, width=max(1,split-gap), relheight=1)
        self.handle.place(x=split-round(4*self.density), y=0, width=round(8*self.density), relheight=1)
        self.right.place(x=split+gap, y=0, width=max(1,width-split-gap), relheight=1)

    def press(self, event):
        self.start = event.x_root, self.ratio
        self.dragging = False
        self.handle.focus_set()
        self.handle.grab_set()

    def drag(self, event):
        if self.start:
            delta = event.x_root-self.start[0]
            if abs(delta) >= 4 or self.dragging:
                self.dragging = True
                self.ratio = self.clamp(self.start[1]+delta/max(1,self.winfo_width()), self.winfo_width(), self.density)
                self.layout()

    def release(self, _event=None):
        self.start = None
        self.dragging = False
        if self.handle.grab_current() == self.handle:
            self.handle.grab_release()

    def cancel(self, _event=None):
        if self.start:
            self.ratio = self.start[1]
        self.release()
        self.layout()
        return "break"

    def reset(self, _event=None):
        self.release()
        self.ratio = 0.5
        self.layout()
        return "break"

    def move(self, delta):
        self.ratio = self.clamp(self.ratio+delta, self.winfo_width(), self.density)
        self.layout()
        return "break"


class ScrollPanel(ttk.Frame):
    """Keep form actions reachable on short / high-DPI desktop viewports."""
    def __init__(self, parent):
        super().__init__(parent)
        p = parent.winfo_toplevel()._opendate_theme.colors
        self.footer = ttk.Frame(self, padding=(16, 8))
        self.footer.pack(side="bottom", fill="x")
        self.canvas = tk.Canvas(self, bg=p["paper"], borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = ttk.Frame(self.canvas, padding=16)
        self.body.action_area = self.footer
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self.resize)
        self.body.bind("<Configure>", self.resize)
        self.canvas.bind_all("<MouseWheel>", self.wheel, add="+")

    def resize(self, _event=None):
        self.canvas.itemconfigure(self.item, width=max(1, self.canvas.winfo_width()),
                                  height=max(self.canvas.winfo_height(), self.body.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def wheel(self, event):
        widget = event.widget
        if isinstance(widget, tk.Text):
            return None  # Let editable text retain its own native scrolling.
        while widget is not None:
            if widget == self.body or widget == self.canvas:
                self.canvas.yview_scroll(-round(event.delta/120)*3, "units")
                return "break"
            widget = getattr(widget, "master", None)
        return None
