from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ai_quota_monitor.config import Settings


def configure_logging(settings: Settings) -> Path:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_path = settings.log_file or settings.data_dir / "logs" / "ai-quota-monitor.log"
    log_path = Path(log_path).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    app_logger = logging.getLogger("ai_quota_monitor")
    app_logger.setLevel(level)
    app_logger.propagate = True

    existing = [
        handler
        for handler in app_logger.handlers
        if isinstance(handler, RotatingFileHandler)
    ]
    for handler in existing:
        if Path(handler.baseFilename) == log_path:
            handler.setLevel(level)
            return log_path
        app_logger.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    app_logger.addHandler(file_handler)
    return log_path
