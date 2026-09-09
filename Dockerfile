FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV AI_QUOTA_MONITOR_ENV=production
ENV AI_QUOTA_MONITOR_HOST=0.0.0.0
ENV AI_QUOTA_MONITOR_PORT=8080
ENV AI_QUOTA_MONITOR_DATABASE_URL=sqlite:////var/lib/ai-quota-monitor/ai-quota-monitor.sqlite3
ENV AI_QUOTA_MONITOR_DATA_DIR=/var/lib/ai-quota-monitor
ENV AI_QUOTA_MONITOR_DEPLOYMENT_MODE=docker
ENV AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=false

WORKDIR /opt/ai-quota-monitor

RUN useradd --create-home --shell /usr/sbin/nologin aiquota

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

RUN python -c "from codex_cli_bin import bundled_codex_path; print(bundled_codex_path())"

RUN mkdir -p /var/lib/ai-quota-monitor \
    && chown -R aiquota:aiquota /var/lib/ai-quota-monitor /opt/ai-quota-monitor

USER aiquota
VOLUME ["/var/lib/ai-quota-monitor"]
EXPOSE 8080

CMD ["ai-quota-monitor"]
