"""MouseKeyMapper - remap mouse buttons / wheel / keys on Windows WITHOUT admin rights.

Uses per-user low-level hooks (WH_MOUSE_LL / WH_KEYBOARD_LL) and SendInput through
ctypes only, so there is nothing to install beyond Python itself.

Limitation (Windows UIPI): input cannot be injected into windows running elevated
(as admin) unless this app is elevated too.

Mapping syntax
  from: mouse:left|right|middle|x1|x2|wheelup|wheeldown|wheelleft|wheelright
        key:<name>          (e.g. key:f13, key:capslock, key:ctrl)
  to:   key:<name>[+<name>...]   (e.g. key:ctrl+c, key:ctrl+shift+t)
        mouse:<same names as above>
        none                (swallow the input)
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
MAGIC = 0x4D4B4D50  # dwExtraInfo tag on events we inject, so we never re-process them


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)

user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
user32.GetMessageW.argtypes = (ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
user32.PostThreadMessageW.argtypes = (wt.DWORD, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
user32.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
kernel32.GetModuleHandleW.argtypes = (wt.LPCWSTR,)
kernel32.GetModuleHandleW.restype = ctypes.c_void_p

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2

# ---------------------------------------------------------------- key names
_ordered = [
    ("backspace", 8), ("tab", 9), ("enter", 13), ("pause", 0x13), ("capslock", 0x14),
    ("esc", 27), ("space", 32), ("pageup", 33), ("pagedown", 34), ("end", 35),
    ("home", 36), ("left", 37), ("up", 38), ("right", 39), ("down", 40),
    ("printscreen", 44), ("insert", 45), ("delete", 46), ("numlock", 0x90),
    ("scrolllock", 0x91), ("menu", 0x5D),
    ("lshift", 0xA0), ("rshift", 0xA1), ("lctrl", 0xA2), ("rctrl", 0xA3),
    ("lalt", 0xA4), ("ralt", 0xA5), ("lwin", 0x5B), ("rwin", 0x5C),
    ("browserback", 0xA6), ("browserforward", 0xA7), ("browserrefresh", 0xA8),
    ("volumemute", 0xAD), ("volumedown", 0xAE), ("volumeup", 0xAF),
    ("medianext", 0xB0), ("mediaprev", 0xB1), ("mediastop", 0xB2), ("mediaplay", 0xB3),
    (";", 0xBA), ("=", 0xBB), (",", 0xBC), ("-", 0xBD), (".", 0xBE), ("/", 0xBF),
    ("`", 0xC0), ("[", 0xDB), ("\\", 0xDC), ("]", 0xDD), ("'", 0xDE),
    ("num*", 0x6A), ("num+", 0x6B), ("num-", 0x6D), ("num.", 0x6E), ("num/", 0x6F),
    ("sleep", 0x5F), ("clear", 0x0C), ("select", 0x29), ("print", 0x2A), ("execute", 0x2B),
    ("help", 0x2F), ("browsersearch", 0xAA), ("browserfavorites", 0xAB),
    ("browserhome", 0xAC), ("browserstop", 0xA9), ("launchmail", 0xB4),
    ("launchmedia", 0xB5), ("launchapp1", 0xB6), ("launchapp2", 0xB7),
]
NAME_TO_VK, VK_TO_NAME = {}, {}
for _n, _v in _ordered:
    NAME_TO_VK[_n] = _v
    VK_TO_NAME.setdefault(_v, _n)
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    NAME_TO_VK[_c] = ord(_c.upper())
    VK_TO_NAME[ord(_c.upper())] = _c
for _i in range(10):
    NAME_TO_VK[f"num{_i}"] = 0x60 + _i
    VK_TO_NAME[0x60 + _i] = f"num{_i}"
for _i in range(1, 25):
    NAME_TO_VK[f"f{_i}"] = 0x6F + _i
    VK_TO_NAME[0x6F + _i] = f"f{_i}"
NAME_TO_VK.update({"return": 13, "escape": 27, "del": 46, "ins": 45, "pgup": 33, "pgdn": 34,
                   "caps": 0x14, "plus": 0xBB, "minus": 0xBD, "apps": 0x5D, "contextmenu": 0x5D,
                   "prtsc": 44, "prtscr": 44, "break": 0x13, "bksp": 8, "spacebar": 32,
                   "altgr": 0xA5, "ralt": 0xA5, "windows": 0x5B, "winkey": 0x5B, "cmd": 0x5B,
                   "super": 0x5B, "meta": 0x5B, "mute": 0xAD, "play": 0xB3, "next": 0xB0,
                   "playpause": 0xB3, "prev": 0xB1})
# generic modifier names: used as target = left key, used as source = matches left AND right
GENERIC = {"shift": (0xA0, 0xA1), "ctrl": (0xA2, 0xA3), "control": (0xA2, 0xA3),
           "alt": (0xA4, 0xA5), "win": (0x5B, 0x5C), "windows": (0x5B, 0x5C),
           "winkey": (0x5B, 0x5C), "cmd": (0x5B, 0x5C), "super": (0x5B, 0x5C),
           "meta": (0x5B, 0x5C), "option": (0xA4, 0xA5)}
# helper list shown in the GUI picker: (label, name)
SPECIAL_KEYS = [
    ("Windows key", "win"), ("Left Win", "lwin"), ("Right Win", "rwin"),
    ("Alt", "alt"), ("Left Alt", "lalt"), ("Right Alt / AltGr", "ralt"),
    ("Ctrl", "ctrl"), ("Left Ctrl", "lctrl"), ("Right Ctrl", "rctrl"),
    ("Shift", "shift"), ("Left Shift", "lshift"), ("Right Shift", "rshift"),
    ("Menu / Apps", "apps"), ("Caps Lock", "capslock"), ("Num Lock", "numlock"),
    ("Scroll Lock", "scrolllock"), ("Tab", "tab"), ("Enter", "enter"), ("Esc", "esc"),
    ("Space", "space"), ("Backspace", "backspace"), ("Delete", "delete"), ("Insert", "insert"),
    ("Home", "home"), ("End", "end"), ("Page Up", "pageup"), ("Page Down", "pagedown"),
    ("Up", "up"), ("Down", "down"), ("Left", "left"), ("Right", "right"),
    ("Print Screen", "printscreen"), ("Pause/Break", "pause"), ("Sleep", "sleep"),
    ("Volume Up", "volumeup"), ("Volume Down", "volumedown"), ("Mute", "volumemute"),
    ("Media Play/Pause", "mediaplay"), ("Media Next", "medianext"),
    ("Media Previous", "mediaprev"), ("Media Stop", "mediastop"),
    ("Browser Back", "browserback"), ("Browser Forward", "browserforward"),
    ("Browser Refresh", "browserrefresh"), ("Browser Home", "browserhome"),
    ("Browser Search", "browsersearch"), ("Launch Mail", "launchmail"),
    ("Launch Media", "launchmedia"), ("Launch App 1", "launchapp1"),
    ("Launch App 2", "launchapp2"),
] + [(f"F{i}", f"f{i}") for i in range(1, 25)]
EXTENDED_VKS = {33, 34, 35, 36, 37, 38, 39, 40, 44, 45, 46, 0xA3, 0xA5, 0x5B, 0x5C, 0x5D, 0x6F, 0x90}

MOUSE_BUTTONS = {  # name: (down flag, up flag, xbutton data)
    "left": (0x0002, 0x0004, 0), "right": (0x0008, 0x0010, 0), "middle": (0x0020, 0x0040, 0),
    "x1": (0x0080, 0x0100, 1), "x2": (0x0080, 0x0100, 2),
}
MOUSE_WHEELS = {"wheelup": (0x0800, 120), "wheeldown": (0x0800, -120),
                "wheelright": (0x1000, 120), "wheelleft": (0x1000, -120)}


def _vk(name):
    name = name.strip().lower()
    if name in NAME_TO_VK:
        return NAME_TO_VK[name]
    if name in GENERIC:
        return GENERIC[name][0]
    if name.startswith("vk") and name[2:].isdigit():
        return int(name[2:])
    raise ValueError(f"unknown key '{name}'")


def parse_source(spec):
    """-> list of source ids: ('mouse', name) or ('key', vk)."""
    spec = spec.strip().lower()
    kind, _, name = spec.partition(":")
    if not name:
        kind, name = "key", spec
    if kind == "mouse":
        if name not in MOUSE_BUTTONS and name not in MOUSE_WHEELS:
            raise ValueError(f"unknown mouse input '{name}'")
        return [("mouse", name)]
    if kind == "key":
        if name in GENERIC:
            return [("key", v) for v in GENERIC[name]]
        return [("key", _vk(name))]
    raise ValueError(f"bad source '{spec}'")


def parse_target(spec):
    """-> ('key', [vk...]) | ('mouse', name) | ('block',)."""
    spec = spec.strip().lower()
    if spec in ("", "none", "block"):
        return ("block",)
    kind, _, name = spec.partition(":")
    if not name:
        kind, name = "key", spec
    if kind == "mouse":
        if name not in MOUSE_BUTTONS and name not in MOUSE_WHEELS:
            raise ValueError(f"unknown mouse output '{name}'")
        return ("mouse", name)
    if kind == "key":
        return ("key", [_vk(t) for t in name.split("+") if t.strip()])
    raise ValueError(f"bad target '{spec}'")


def describe(src):
    return f"mouse:{src[1]}" if src[0] == "mouse" else f"key:{VK_TO_NAME.get(src[1], 'vk%d' % src[1])}"


# ---------------------------------------------------------------- injection
def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_key(vk, down):
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, MAGIC)
    _send(i)


def send_mouse(name, down):
    i = INPUT(type=0)
    if name in MOUSE_WHEELS:
        if down:
            flag, delta = MOUSE_WHEELS[name]
            i.mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, flag, 0, MAGIC)
            _send(i)
        return
    d, u, data = MOUSE_BUTTONS[name]
    i.mi = MOUSEINPUT(0, 0, data, d if down else u, 0, MAGIC)
    _send(i)


# ---------------------------------------------------------------- mapper
class Mapper:
    def __init__(self):
        self.table = {}
        self.enabled = True
        self.capture = None  # callable(kind, src) -> bool; used by the GUI recorders
        self._active = {}
        self._captured = set()
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self._hooks = []

    def set_mappings(self, mappings):
        table = {}
        for m in mappings:
            if not m.get("enabled", True):
                continue
            target = parse_target(m["to"])
            for src in parse_source(m["from"]):
                table[src] = target
        self.table = table

    # -- core logic (runs inside the hook callbacks; keep it fast)
    def _press(self, t):
        if t[0] == "key":
            for vk in t[1]:
                send_key(vk, True)
        elif t[0] == "mouse":
            send_mouse(t[1], True)

    def _release(self, t):
        if t[0] == "key":
            for vk in reversed(t[1]):
                send_key(vk, False)
        elif t[0] == "mouse":
            send_mouse(t[1], False)

    def _dispatch(self, src, kind):
        """kind: 'down' | 'up' | 'tap'. Returns True to swallow the original event."""
        cap = self.capture
        if cap is not None:
            # recorder active: cap(kind, src) -> True swallows the event
            if cap(kind, src):
                if kind == "down":
                    self._captured.add(src)
                return True
        if kind == "up":
            if src in self._captured:
                self._captured.discard(src)
                return True
            t = self._active.pop(src, None)
            if t is None:
                return False
            self._release(t)
            return True
        if not self.enabled:
            return False
        t = self.table.get(src)
        if t is None:
            return False
        if kind == "tap":
            self._press(t)
            self._release(t)
        elif src in self._active:  # auto-repeat of a held source
            if t[0] == "key":
                self._press(t)
        else:
            self._active[src] = t
            self._press(t)
        return True

    def _mouse_proc(self, n, w, l):
        if n >= 0:
            try:
                info = ctypes.cast(l, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != MAGIC and self._handle_mouse(w, info):
                    return 1
            except Exception:
                pass
        return user32.CallNextHookEx(None, n, w, l)

    def _handle_mouse(self, msg, info):
        hi = (info.mouseData >> 16) & 0xFFFF
        if msg in (0x201, 0x202):
            name = "left"
        elif msg in (0x204, 0x205):
            name = "right"
        elif msg in (0x207, 0x208):
            name = "middle"
        elif msg in (0x20B, 0x20C):
            name = "x1" if hi == 1 else "x2"
        elif msg in (0x20A, 0x20E):
            delta = ctypes.c_short(hi).value
            if msg == 0x20A:
                name = "wheelup" if delta > 0 else "wheeldown"
            else:
                name = "wheelright" if delta > 0 else "wheelleft"
            return self._dispatch(("mouse", name), "tap")
        else:
            return False
        kind = "down" if msg in (0x201, 0x204, 0x207, 0x20B) else "up"
        return self._dispatch(("mouse", name), kind)

    def _kbd_proc(self, n, w, l):
        if n >= 0:
            try:
                info = ctypes.cast(l, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != MAGIC:
                    kind = "down" if w in (0x100, 0x104) else "up"
                    if self._dispatch(("key", info.vkCode), kind):
                        return 1
            except Exception:
                pass
        return user32.CallNextHookEx(None, n, w, l)

    # -- hook thread
    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._mp, self._kp = HOOKPROC(self._mouse_proc), HOOKPROC(self._kbd_proc)
        hmod = kernel32.GetModuleHandleW(None)
        self._hooks = [user32.SetWindowsHookExW(WH_MOUSE_LL, self._mp, hmod, 0),
                       user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kp, hmod, 0)]
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for h in self._hooks:
            if h:
                user32.UnhookWindowsHookEx(h)

    def start(self):
        if self._thread:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(3)
        if not all(self._hooks):
            raise OSError("could not install input hooks")

    def stop(self):
        if self._thread:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)
            self._thread = None
        for t in list(self._active.values()):
            self._release(t)
        self._active.clear()


# ---------------------------------------------------------------- config
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MouseKeyMapper")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return bool(cfg.get("enabled", True)), list(cfg.get("mappings", []))
    except (OSError, ValueError):
        return True, []


def save_config(enabled, mappings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"enabled": enabled, "mappings": mappings}, f, indent=2)


def run_headless():
    enabled, mappings = load_config()
    mapper = Mapper()
    mapper.enabled = enabled
    mapper.set_mappings(mappings)
    mapper.start()
    print(f"Running with {len(mappings)} mapping(s) from {CONFIG_PATH}. Ctrl+C to quit.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        mapper.stop()


if __name__ == "__main__":
    if "--nogui" in sys.argv:
        run_headless()
    else:
        import gui
        gui.run("--minimized" in sys.argv)
