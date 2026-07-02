"""Bootstrap launcher, targeted directly by the autostart registry entry
(run via the venv's pythonw.exe, so venv site-packages are already on
sys.path). Only needs the project root on sys.path to import `flowlocal`.

Any startup failure is written to %APPDATA%\\FlowLocal\\error.log instead
of crashing silently (there is no console under pythonw.exe).
"""
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _error_log_path() -> str:
    app_data = os.environ.get("APPDATA") or os.path.expanduser("~")
    directory = os.path.join(app_data, "FlowLocal")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        directory = app_data
    return os.path.join(directory, "error.log")


def _main() -> None:
    from flowlocal.__main__ import main

    main()


if __name__ == "__main__":
    try:
        _main()
    except Exception:
        try:
            with open(_error_log_path(), "a", encoding="utf-8") as f:
                f.write("=== FlowLocal startup failure ===\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:
            pass
