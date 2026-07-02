"""Entry point: `python -m flowlocal`.

Sets up rotating file logging to %APPDATA%\\FlowLocal\\flowlocal.log, then
constructs and runs the App (single-instance guarded).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from flowlocal.config import Config, config_path


def _setup_logging() -> None:
    app_data_dir = os.path.dirname(config_path())
    os.makedirs(app_data_dir, exist_ok=True)
    log_path = os.path.join(app_data_dir, "flowlocal.log")

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)


def main() -> int:
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("FlowLocal starting")

    from flowlocal.app import App, SingleInstanceGuard

    guard = SingleInstanceGuard()
    if not guard.acquire():
        logger.warning("Another instance is already running; exiting")
        return 1

    cfg = Config.load()

    try:
        import flowlocal.autostart as autostart

        if cfg.autostart and not autostart.is_enabled():
            autostart.enable()
        elif not cfg.autostart and autostart.is_enabled():
            autostart.disable()
    except Exception as exc:
        logger.warning("Could not sync autostart state: %s", exc)

    app = App(cfg)
    try:
        app.run()
    except Exception:
        logger.exception("Fatal error in App.run()")
        return 1
    finally:
        guard.release()

    logger.info("FlowLocal exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
