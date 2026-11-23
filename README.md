# Mouse Key Mapper

Remap mouse buttons, the scroll wheel and keyboard keys on Windows — **no admin rights, no installer, no third-party packages**. Pure Python (`ctypes` + `tkinter`).

It uses per-user low-level hooks (`WH_MOUSE_LL` / `WH_KEYBOARD_LL`) and `SendInput`.

## Features

- Dark, card-based GUI with a one-click shortcut recorder
- Map mouse buttons (left, right, middle, Back/Forward), scroll up/down/tilt, or any key
- Targets: a key or shortcut (e.g. `Ctrl+Shift+T`), a mouse action, or "do nothing" (block)
- Quick-add presets (Back → Copy, Forward → Paste, Caps Lock → Ctrl, ...)
- System tray icon: pause/resume or exit; closing the window keeps it running
- Optional "Start with Windows"
- Config saved per user in `%APPDATA%\MouseKeyMapper\config.json`
- Headless mode with `--nogui`

## Requirements

- Windows 10/11
- Python 3.8+ (with tkinter, included in the standard installer)

## Run

```powershell
python mousekeymapper.py            # GUI
python mousekeymapper.py --minimized  # start hidden in the tray
python mousekeymapper.py --nogui    # headless, uses saved config
```

Or double-click `run.bat`.

## Mapping syntax (config / headless)

```
from: mouse:left|right|middle|x1|x2|wheelup|wheeldown|wheelleft|wheelright
      key:<name>              e.g. key:f13, key:capslock, key:ctrl
to:   key:<name>[+<name>...]  e.g. key:ctrl+c, key:ctrl+shift+t
      mouse:<name>
      none                    swallow the input
```

Example `config.json`:

```json
{
  "enabled": true,
  "mappings": [
    { "from": "mouse:x1", "to": "key:ctrl+c", "enabled": true },
    { "from": "key:capslock", "to": "key:ctrl", "enabled": true }
  ]
}
```

## Limitations

- Windows UIPI: input can't be injected into windows running **as administrator** unless Mapper is elevated too.
- Remapping the left/right mouse button can make the mouse hard to use; use the tray icon → *Pause* to recover.

## License

MIT
