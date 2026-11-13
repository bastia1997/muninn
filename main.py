import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from muninn.app import main
    main()
except Exception:
    try:
        from muninn.config import DATA_DIR
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = DATA_DIR / "error.log"
    except Exception:
        log = Path.home() / "muninn_error.log"
    log.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        import tkinter.messagebox as mb
        mb.showerror("Muninn", f"Startup error. Details in:\n{log}")
    except Exception:
        pass
    raise
