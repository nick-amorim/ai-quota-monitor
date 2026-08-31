from __future__ import annotations

import uvicorn

from ai_quota_monitor.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "ai_quota_monitor.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.env == "development",
    )


if __name__ == "__main__":
    main()
