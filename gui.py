"""Friendly GUI for MouseKeyMapper: cards, shortcut recorder, tray icon, presets."""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import mousekeymapper as mk

# premium dark palette: ink background, raised surfaces, violet accent, mint for "result"
BG, CARD, FIELD, BORDER = "#0e1016", "#171a23", "#212637", "#272c3c"
TEXT, MUTED = "#eef0f7", "#8a91a7"
ACCENT, ACCENT_H = "#7c5cff", "#6a48f2"
BTN, BTN_H = "#252a3b", "#30364b"
CHIP_FROM, FROM_FG = "#242b55", "#b4c0ff"
CHIP_TO, TO_FG = "#17382f", "#6ee7b7"
PREVIEW = "#cfc6ff"
ON, OFF = "#34d399", "#3a4054"
DISABLED_BG, DISABLED_FG = "#242838", "#6a7088"
FONT = ("Segoe UI", 10)

MOUSE_LABELS = {
    "left": "Left click", "right": "Right click", "middle": "Middle click",
    "x1": "Mouse Back button", "x2": "Mouse Forward button",
    "wheelup": "Scroll up", "wheeldown": "Scroll down",
    "wheelleft": "Scroll left (tilt)", "wheelright": "Scroll right (tilt)",
}
KEY_LABELS = {n: lbl for lbl, n in mk.SPECIAL_KEYS}
KEY_LABELS.update({"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Windows key",
                   "enter": "Enter", "esc": "Esc"})
GENERIC_OF = {0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl",
              0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}

PRESETS = [
    ("Back button → Copy (Ctrl+C)", [("mouse:x1", "key:ctrl+c")]),
    ("Forward button → Paste (Ctrl+V)", [("mouse:x2", "key:ctrl+v")]),
    ("Middle click → Task view (Win+Tab)", [("mouse:middle", "key:win+tab")]),
    ("Caps Lock → Ctrl", [("key:capslock", "key:ctrl")]),
    ("Tilt wheel → Volume down / up",
     [("mouse:wheelleft", "key:volumedown"), ("mouse:wheelright", "key:volumeup")]),
]


def pretty_key(name):
    if name in KEY_LABELS:
        return KEY_LABELS[name]
    return name.upper() if len(name) == 1 else name.title()


def pretty(spec):
    spec = (spec or "").strip().lower()
    if not spec:
        return ""
    if spec in ("none", "block"):
        return "Do nothing (blocked)"
    kind, _, name = spec.partition(":")
    if kind == "mouse":
        return MOUSE_LABELS.get(name, name)
    return " + ".join(pretty_key(t) for t in name.split("+") if t)


def combo_spec(vks):
    names = []
    for vk in vks:
        n = GENERIC_OF.get(vk) or mk.VK_TO_NAME.get(vk, "vk%d" % vk)
        if n not in names:
            names.append(n)
    mods = [n for n in names if n in ("ctrl", "alt", "shift", "win")]
    rest = [n for n in names if n not in mods]
    mods.sort(key=["ctrl", "alt", "shift", "win"].index)
    return "key:" + "+".join(mods + rest)


# ------------------------------------------------------------ DPI / instance
def _dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _single_instance():
    h = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MouseKeyMapperSingleton")
    return ctypes.windll.kernel32.GetLastError() != 183, h


# ------------------------------------------------------------ autostart
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_get():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "MouseKeyMapper")
            return True
    except OSError:
        return False


def autostart_set(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                exe = exe[:-10] + "pythonw.exe"
            script = os.path.abspath(mk.__file__)
            winreg.SetValueEx(k, "MouseKeyMapper", 0, winreg.REG_SZ, f'"{exe}" "{script}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, "MouseKeyMapper")
            except OSError:
                pass


# ------------------------------------------------------------ tray icon
WNDPROC = ctypes.WINFUNCTYPE(mk.LRESULT, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", ctypes.c_void_p),
                ("szTip", ctypes.c_wchar * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", ctypes.c_wchar * 256), ("uTimeout", wt.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", ctypes.c_void_p)]


_u, _s = mk.user32, ctypes.WinDLL("shell32", use_last_error=True)
_u.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
_u.DefWindowProcW.restype = mk.LRESULT
_u.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
_u.CreateWindowExW.argtypes = (wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_u.CreateWindowExW.restype = wt.HWND
_u.LoadIconW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
_u.LoadIconW.restype = ctypes.c_void_p
_u.AppendMenuW.argtypes = (ctypes.c_void_p, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
_u.CreatePopupMenu.restype = ctypes.c_void_p
_u.DestroyMenu.argtypes = (ctypes.c_void_p,)
_u.TrackPopupMenu.argtypes = (ctypes.c_void_p, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wt.HWND, ctypes.c_void_p)
_u.PostMessageW.argtypes = (wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
_u.SetForegroundWindow.argtypes = (wt.HWND,)
_u.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
_s.Shell_NotifyIconW.argtypes = (wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW))


class Tray:
    WM_TRAY = 0x8001

    def __init__(self, emit, is_enabled):
        self.emit, self.is_enabled = emit, is_enabled
        self.hwnd = None
        self.ok = False
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(3)

    def _proc(self, hwnd, msg, wp, lp):
        if msg == self.WM_TRAY:
            ev = lp & 0xFFFF
            if ev in (0x202, 0x203):  # left click / double click
                self.emit("open")
            elif ev == 0x205:  # right click -> menu
                self._menu()
            return 0
        if msg == 0x0002:  # WM_DESTROY
            _u.PostQuitMessage(0)
            return 0
        return _u.DefWindowProcW(hwnd, msg, wp, lp)

    def _menu(self):
        m = _u.CreatePopupMenu()
        _u.AppendMenuW(m, 0, 1, "Open Mouse Key Mapper")
        _u.AppendMenuW(m, 0, 2, "Pause mappings" if self.is_enabled() else "Resume mappings")
        _u.AppendMenuW(m, 0x800, 0, None)
        _u.AppendMenuW(m, 0, 3, "Exit")
        pt = wt.POINT()
        _u.GetCursorPos(ctypes.byref(pt))
        _u.SetForegroundWindow(self.hwnd)
        cmd = _u.TrackPopupMenu(m, 0x180, pt.x, pt.y, 0, self.hwnd, None)  # RETURNCMD|NONOTIFY
        _u.PostMessageW(self.hwnd, 0, 0, 0)
        _u.DestroyMenu(m)
        if cmd:
            self.emit({1: "open", 2: "toggle", 3: "exit"}[cmd])

    def _run(self):
        try:
            hinst = mk.kernel32.GetModuleHandleW(None)
            self._wp = WNDPROC(self._proc)
            wc = WNDCLASSW()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._wp, hinst, "MKMTrayWindow"
            _u.RegisterClassW(ctypes.byref(wc))
            self.hwnd = _u.CreateWindowExW(0, "MKMTrayWindow", "MKM", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd, nid.uID, nid.uFlags = self.hwnd, 1, 0x7  # MESSAGE|ICON|TIP
            nid.uCallbackMessage = self.WM_TRAY
            nid.hIcon = _u.LoadIconW(None, 32512)  # IDI_APPLICATION
            nid.szTip = "Mouse Key Mapper"
            self.nid = nid
            self.ok = bool(_s.Shell_NotifyIconW(0, ctypes.byref(nid)))  # NIM_ADD
        finally:
            self._ready.set()
        msg = wt.MSG()
        while _u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _u.TranslateMessage(ctypes.byref(msg))
            _u.DispatchMessageW(ctypes.byref(msg))

    def remove(self):
        if self.ok:
            _s.Shell_NotifyIconW(2, ctypes.byref(self.nid))  # NIM_DELETE
            self.ok = False
            _u.PostMessageW(self.hwnd, 0x10, 0, 0)  # WM_CLOSE


# ------------------------------------------------------------ widgets
def dark_titlebar(win):
    """Dark native title bar (Windows 10 20H1+/11); silently ignored elsewhere."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)
    except Exception:
        pass


def button(parent, text, cmd, primary=False, **kw):
    bg, fg, hov = (ACCENT, "white", ACCENT_H) if primary else (BTN, TEXT, BTN_H)
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=hov,
                  activeforeground=fg, disabledforeground=DISABLED_FG, relief="flat", bd=0,
                  padx=16, pady=8, font=("Segoe UI Semibold", 10), cursor="hand2", **kw)
    b._look = (bg, fg, hov)

    def enter(e):
        if str(b["state"]) == "normal":
            b.config(bg=b._look[2])

    b.bind("<Enter>", enter)
    b.bind("<Leave>", lambda e: b.config(bg=b._look[0] if str(b["state"]) == "normal" else DISABLED_BG))
    return b


def set_button_enabled(b, ok):
    b.config(state="normal" if ok else "disabled", bg=b._look[0] if ok else DISABLED_BG,
             cursor="hand2" if ok else "arrow")


def style_ttk(root):
    s = ttk.Style()
    s.theme_use("clam")
    s.configure("TCombobox", fieldbackground=FIELD, background=BTN, foreground=TEXT, arrowcolor=MUTED,
                bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD, selectbackground=FIELD,
                selectforeground=TEXT, padding=5)
    s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
          selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)],
          background=[("active", BTN_H)], arrowcolor=[("active", TEXT)])
    s.configure("Vertical.TScrollbar", background=BTN, troughcolor=BG, bordercolor=BG,
                lightcolor=BTN, darkcolor=BTN, arrowcolor=MUTED, gripcount=0)
    s.map("Vertical.TScrollbar", background=[("active", BTN_H)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    root.option_add("*TCombobox*Listbox.font", FONT)


class Switch(tk.Canvas):
    def __init__(self, parent, value, command, bg=CARD):
        super().__init__(parent, width=56, height=30, bg=bg, highlightthickness=0, cursor="hand2")
        self.value, self.command = value, command
        self.bind("<Button-1>", lambda e: self.set(not self.value, True))
        self.draw()

    def set(self, v, fire=False):
        self.value = v
        self.draw()
        if fire:
            self.command(v)

    def draw(self):
        self.delete("all")
        c = ON if self.value else OFF
        self.create_oval(2, 3, 28, 27, fill=c, outline=c)
        self.create_oval(28, 3, 54, 27, fill=c, outline=c)
        self.create_rectangle(15, 3, 41, 28, fill=c, outline=c)
        x = 30 if self.value else 4
        self.create_oval(x, 6, x + 22, 28 - 2, fill="white", outline="white")


# ------------------------------------------------------------ app
def run(start_hidden=False):
    _dpi()
    first, _mutex = _single_instance()
    if not first:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Mouse Key Mapper", "Mouse Key Mapper is already running.\n"
                            "Look for its icon in the system tray (near the clock).")
        return

    enabled, saved = mk.load_config()
    mappings = []
    for m in saved:
        try:
            mk.Mapper().set_mappings([m])
            mappings.append({"from": m["from"], "to": m["to"], "enabled": m.get("enabled", True)})
        except (ValueError, KeyError):
            pass
    mapper = mk.Mapper()
    mapper.enabled = enabled
    mapper.set_mappings(mappings)
    try:
        mapper.start()
    except OSError as e:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Mouse Key Mapper", f"Could not start: {e}")
        return

    root = tk.Tk()
    root.title("Mouse Key Mapper")
    root.configure(bg=BG)
    root.geometry("680x640")
    root.minsize(560, 420)
    style_ttk(root)
    dark_titlebar(root)
    events = queue.Queue()

    def pump():
        while not events.empty():
            events.get()()
        root.after(40, pump)

    def persist():
        mapper.set_mappings(mappings)
        mk.save_config(mapper.enabled, mappings)

    # ---- header
    head = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    head.pack(fill="x")
    logo = tk.Canvas(head, width=44, height=44, bg=CARD, highlightthickness=0)
    logo.create_oval(2, 2, 42, 42, fill=ACCENT, outline=ACCENT)
    logo.create_oval(14, 9, 30, 35, outline="white", width=2)
    logo.create_line(22, 9, 22, 20, fill="white", width=2)
    logo.pack(side="left", padx=(18, 0), pady=14)
    hl = tk.Frame(head, bg=CARD)
    hl.pack(side="left", padx=12, pady=14)
    tk.Label(hl, text="Mouse Key Mapper", bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(anchor="w")
    status = tk.Label(hl, bg=CARD, font=FONT)
    status.pack(anchor="w")

    def refresh_status():
        on = mapper.enabled
        status.config(text="● Mappings are active" if on else "● Paused – nothing is remapped",
                      fg=ON if on else MUTED)

    def set_enabled(v):
        mapper.enabled = v
        refresh_status()
        persist()

    master = Switch(head, enabled, set_enabled)
    master.pack(side="right", padx=18)

    # ---- body (scrollable card list)
    body = tk.Frame(root, bg=BG)  # packed after the bottom bar so the bar always stays visible
    tk.Label(body, text="Your mappings", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
    wrap = tk.Frame(body, bg=BG)
    wrap.pack(fill="both", expand=True, pady=(6, 0))
    canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
    sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=BG)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.bind("<Enter>", lambda e: canvas.bind_all(
        "<MouseWheel>", lambda ev: canvas.yview_scroll(-1 * (ev.delta // 120), "units")))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def chip(parent, text, bg, fg, dim):
        return tk.Label(parent, text=text, bg=FIELD if dim else bg, fg=MUTED if dim else fg,
                        font=("Segoe UI Semibold", 10), padx=12, pady=8, wraplength=170, justify="center")

    def render():
        for w in inner.winfo_children():
            w.destroy()
        if not mappings:
            box = tk.Frame(inner, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            box.pack(fill="x", pady=4, padx=2)
            tk.Label(box, text="No mappings yet", bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(pady=(24, 4))
            tk.Label(box, text="A mapping turns one mouse button, scroll or key into another action.\n"
                     "Example: make your mouse Back button press Ctrl+C.",
                     bg=CARD, fg=MUTED, font=FONT, justify="center", wraplength=420).pack()
            button(box, "+  Add your first mapping", lambda: edit(None), primary=True).pack(pady=(14, 24))
            return
        for i, m in enumerate(mappings):
            on = m.get("enabled", True)
            card = tk.Frame(inner, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", pady=4, padx=2)
            card.columnconfigure(1, weight=1, uniform="c")
            card.columnconfigure(3, weight=1, uniform="c")
            sw = Switch(card, on, lambda v, i=i: toggle_row(i, v))
            sw.grid(row=0, column=0, padx=(12, 8), pady=12)
            chip(card, pretty(m["from"]), CHIP_FROM, FROM_FG, not on).grid(row=0, column=1, sticky="nsew", pady=12)
            tk.Label(card, text="→", bg=CARD, fg=ACCENT if on else MUTED, font=("Segoe UI", 15)).grid(row=0, column=2, padx=10)
            chip(card, pretty(m["to"]), CHIP_TO, TO_FG, not on).grid(row=0, column=3, sticky="nsew", pady=12)
            button(card, "Edit", lambda i=i: edit(i)).grid(row=0, column=4, padx=(12, 4))
            x = button(card, "✕", lambda i=i: delete(i))
            x.config(padx=10)
            x._look = (BTN, "#f87171", BTN_H)
            x.config(fg="#f87171", activeforeground="#f87171")
            x.grid(row=0, column=5, padx=(0, 12))

    def toggle_row(i, v):
        mappings[i]["enabled"] = v
        persist()
        render()

    def delete(i):
        del mappings[i]
        persist()
        render()

    def add_or_replace(m, index=None):
        """Insert mapping; handle the same source already mapped. Returns False if cancelled."""
        same = [j for j, o in enumerate(mappings) if o["from"].lower() == m["from"].lower() and j != index]
        if same:
            if not messagebox.askyesno("Already mapped",
                                       f"\"{pretty(m['from'])}\" already has a mapping.\nReplace it?"):
                return False
            for j in reversed(same):
                del mappings[j]
                if index is not None and j < index:
                    index -= 1
        if index is None:
            mappings.append(m)
        else:
            mappings[index] = m
        persist()
        render()
        return True

    # ---- bottom bar
    foot_note = tk.Label(root, text="Closing this window keeps the mapper running in the system tray. "
                         "Right-click the tray icon to pause or exit.", bg=BG, fg=MUTED,
                         font=("Segoe UI", 9), wraplength=580, justify="left")
    foot_note.pack(side="bottom", fill="x", padx=16, pady=(0, 10), anchor="w")
    bar = tk.Frame(root, bg=BG)
    bar.pack(side="bottom", fill="x", padx=16, pady=12)
    body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
    button(bar, "+  Add mapping", lambda: edit(None), primary=True).pack(side="left")
    qa = tk.Menubutton(bar, text="Quick add  ▾", bg=BTN, fg=TEXT, relief="flat", font=("Segoe UI Semibold", 10),
                       padx=16, pady=8, cursor="hand2", activebackground=BTN_H, activeforeground=TEXT)
    menu = tk.Menu(qa, tearoff=0, font=FONT, bg=FIELD, fg=TEXT, activebackground=ACCENT,
                   activeforeground="white", bd=0, relief="flat")
    qa.config(menu=menu)
    qa.pack(side="left", padx=8)

    def preset(items):
        for f, t in items:
            add_or_replace({"from": f, "to": t, "enabled": True})

    for label, items in PRESETS:
        menu.add_command(label=label, command=lambda it=items: preset(it))

    auto = tk.BooleanVar(value=autostart_get())

    def on_auto():
        try:
            autostart_set(auto.get())
        except OSError as e:
            auto.set(not auto.get())
            messagebox.showerror("Start with Windows", str(e))

    tk.Checkbutton(bar, text="Start with Windows", variable=auto, command=on_auto, bg=BG, fg=TEXT,
                   activebackground=BG, activeforeground=TEXT, selectcolor=FIELD, font=FONT,
                   highlightthickness=0, bd=0).pack(side="right")

    # ------------------------------------------------------------ add / edit dialog
    def edit(index):
        existing = mappings[index] if index is not None else None
        dlg = tk.Toplevel(root)
        dlg.title("Edit mapping" if existing else "New mapping")
        dlg.configure(bg=BG)
        dlg.transient(root)
        dlg.resizable(False, False)
        dark_titlebar(dlg)
        st = {"from": existing["from"] if existing else "", "to": existing["to"] if existing else "",
              "timer": None}
        mode = tk.StringVar(value="keys")
        if existing:
            t = existing["to"].lower()
            mode.set("mouse" if t.startswith("mouse:") else "block" if t in ("none", "block") else "keys")

        def card(title):
            fr = tk.Frame(dlg, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            fr.pack(fill="x", padx=14, pady=(12, 0))
            tk.Label(fr, text=title, bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=14, pady=(10, 4))
            return fr

        # step 1
        c1 = card("1   When I press…")
        from_btn = tk.Button(c1, relief="flat", bd=0, bg=CHIP_FROM, fg=FROM_FG, font=("Segoe UI Semibold", 11),
                             pady=16, cursor="hand2", wraplength=420, activebackground="#2d3668",
                             activeforeground=FROM_FG)
        from_btn.pack(fill="x", padx=14)
        row1 = tk.Frame(c1, bg=CARD)
        row1.pack(fill="x", padx=14, pady=(8, 12))
        tk.Label(row1, text="or choose:", bg=CARD, fg=MUTED, font=FONT).pack(side="left")
        src_items = [(MOUSE_LABELS[n], "mouse:" + n) for n in
                     ("x1", "x2", "middle", "wheelup", "wheeldown", "wheelleft", "wheelright", "left", "right")]
        src_items += [(lbl, "key:" + n) for lbl, n in mk.SPECIAL_KEYS]
        src_cb = ttk.Combobox(row1, values=[a for a, _ in src_items], state="readonly", width=34)
        src_cb.pack(side="left", padx=8)
        src_cb.bind("<<ComboboxSelected>>", lambda e: (cancel_rec(), st.update({"from": src_items[src_cb.current()][1]}), refresh()))

        # step 2
        c2 = card("2   …do this")
        mrow = tk.Frame(c2, bg=CARD)
        mrow.pack(fill="x", padx=14)
        for txt, val in (("Press a key / shortcut", "keys"), ("Mouse action", "mouse"), ("Do nothing (block)", "block")):
            tk.Radiobutton(mrow, text=txt, variable=mode, value=val, bg=CARD, fg=TEXT, activebackground=CARD,
                           activeforeground=TEXT, selectcolor=FIELD, highlightthickness=0, bd=0, font=FONT,
                           command=lambda: (cancel_rec(), refresh())).pack(side="left", padx=(0, 14))
        pane = tk.Frame(c2, bg=CARD)
        pane.pack(fill="x", padx=14, pady=(8, 12))

        # keys pane
        kp = tk.Frame(pane, bg=CARD)
        to_lbl = tk.Label(kp, bg=CHIP_TO, fg=TO_FG, font=("Segoe UI Semibold", 11), pady=16, wraplength=420)
        to_lbl.pack(fill="x")
        kb = tk.Frame(kp, bg=CARD)
        kb.pack(fill="x", pady=(8, 0))
        rec_to_btn = button(kb, "●  Record shortcut", lambda: rec_to())
        rec_to_btn.pack(side="left")
        button(kb, "Clear", lambda: (cancel_rec(), st.update({"to": ""}), refresh())).pack(side="left", padx=6)
        kb2 = tk.Frame(kp, bg=CARD)
        kb2.pack(fill="x", pady=(8, 0))
        tk.Label(kb2, text="Add special key:", bg=CARD, fg=MUTED, font=FONT).pack(side="left")
        key_cb = ttk.Combobox(kb2, values=[l for l, _ in mk.SPECIAL_KEYS], state="readonly", width=24)
        key_cb.pack(side="left", padx=8)

        def add_key(_=None):
            if key_cb.current() < 0:
                return
            n = mk.SPECIAL_KEYS[key_cb.current()][1]
            cur = st["to"] if st["to"].lower().startswith("key:") else ""
            st["to"] = f"{cur}+{n}" if cur else f"key:{n}"
            key_cb.set("")
            refresh()

        key_cb.bind("<<ComboboxSelected>>", add_key)
        tk.Label(kp, text="Tip: click Record, then hold your shortcut (e.g. Ctrl + Shift + T) and let go.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))

        # mouse pane
        mp = tk.Frame(pane, bg=CARD)
        tk.Label(mp, text="Perform:", bg=CARD, fg=MUTED, font=FONT).pack(side="left")
        out_items = [(MOUSE_LABELS[n], "mouse:" + n) for n in
                     ("left", "right", "middle", "x1", "x2", "wheelup", "wheeldown", "wheelleft", "wheelright")]
        out_cb = ttk.Combobox(mp, values=[a for a, _ in out_items], state="readonly", width=28)
        out_cb.pack(side="left", padx=8)
        out_cb.bind("<<ComboboxSelected>>", lambda e: (st.update({"to": out_items[out_cb.current()][1]}), refresh()))

        # block pane
        bp = tk.Frame(pane, bg=CARD)
        tk.Label(bp, text="The original input will be ignored – it won't do anything.", bg=CARD, fg=MUTED,
                 font=FONT).pack(anchor="w")

        # preview + footer
        prev = tk.Label(dlg, bg=BG, fg=PREVIEW, font=("Segoe UI Semibold", 11), wraplength=500)
        prev.pack(pady=(12, 0))
        foot = tk.Frame(dlg, bg=BG)
        foot.pack(fill="x", padx=14, pady=12)
        save_btn = button(foot, "Save", lambda: save(), primary=True)
        save_btn.pack(side="right")
        button(foot, "Cancel", lambda: close()).pack(side="right", padx=8)

        # recording
        def cancel_rec():
            mapper.capture = None
            if st["timer"]:
                root.after_cancel(st["timer"])
                st["timer"] = None
            from_btn.config(text=from_text(), fg=FROM_FG)
            rec_to_btn.config(text="●  Record shortcut")

        def from_text():
            return (pretty(st["from"]) + "     (click to change)") if st["from"] else \
                "●  Click here, then press a key, mouse button or scroll"

        def rec_from():
            cancel_rec()
            from_btn.config(text="Waiting… press / click / scroll now")

            def cap(kind, src):
                if kind == "up":
                    return False
                mapper.capture = None
                spec = mk.describe(src)
                events.put(lambda: (cancel_rec(), st.update({"from": spec}), refresh()))
                return True

            mapper.capture = cap
            st["timer"] = root.after(8000, cancel_rec)

        def rec_to():
            cancel_rec()
            rec_to_btn.config(text="Listening… press your shortcut")
            held, order, seen = set(), [], set()

            def cap(kind, src):
                if src[0] != "key":
                    return False
                vk = src[1]
                if kind == "down":
                    held.add(vk)
                    seen.add(vk)
                    if vk not in order:
                        order.append(vk)
                    return True
                if kind == "up" and vk in seen:
                    held.discard(vk)
                    if not held:
                        mapper.capture = None
                        spec = combo_spec(order)
                        events.put(lambda: (cancel_rec(), st.update({"to": spec}), refresh()))
                    return True
                return False

            mapper.capture = cap
            st["timer"] = root.after(10000, cancel_rec)

        from_btn.config(command=rec_from)

        def target_spec():
            m = mode.get()
            if m == "block":
                return "none"
            if m == "mouse":
                return st["to"] if st["to"].lower().startswith("mouse:") else ""
            return st["to"] if st["to"].lower().startswith("key:") else ""

        def refresh():
            for p in (kp, mp, bp):
                p.pack_forget()
            {"keys": kp, "mouse": mp, "block": bp}[mode.get()].pack(fill="x")
            from_btn.config(text=from_text())
            tgt = target_spec()
            to_lbl.config(text=pretty(tgt) if tgt else "No shortcut yet – click Record, or add a special key below")
            if mode.get() == "mouse" and tgt:
                for i, (_, s) in enumerate(out_items):
                    if s == tgt:
                        out_cb.current(i)
            ok = bool(st["from"] and tgt)
            if ok:
                try:
                    mk.parse_source(st["from"])
                    mk.parse_target(tgt)
                except ValueError:
                    ok = False
            prev.config(text=f"{pretty(st['from'])}   →   {pretty(tgt)}" if ok else "")
            set_button_enabled(save_btn, ok)

        def close():
            cancel_rec()
            dlg.destroy()

        def save():
            frm, tgt = st["from"], target_spec()
            if frm.lower() in ("mouse:left", "mouse:right") and not messagebox.askyesno(
                    "Careful", "Remapping the left or right mouse button can make your mouse hard to use.\n"
                    "(You can recover via the tray icon → Pause.)\n\nContinue?", parent=dlg):
                return
            m = {"from": frm, "to": tgt, "enabled": existing.get("enabled", True) if existing else True}
            cancel_rec()
            if add_or_replace(m, index):
                dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", close)
        refresh()
        dlg.update_idletasks()
        dlg.geometry(f"+{root.winfo_x() + 60}+{root.winfo_y() + 40}")
        dlg.focus_set()

    # ------------------------------------------------------------ tray + lifecycle
    def quit_app():
        tray.remove()
        mapper.stop()
        root.destroy()

    def show():
        root.deiconify()
        root.lift()
        root.focus_force()

    def toggle_from_tray():
        master.set(not mapper.enabled, True)

    tray = Tray(lambda ev: events.put({"open": show, "toggle": toggle_from_tray, "exit": quit_app}[ev]),
                lambda: mapper.enabled)

    def on_close():
        if tray.ok:
            root.withdraw()
        else:
            quit_app()

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh_status()
    render()
    pump()
    if start_hidden and tray.ok:
        root.withdraw()
    root.mainloop()
