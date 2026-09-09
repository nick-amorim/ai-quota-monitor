from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_quota_monitor.models import Account
from ai_quota_monitor.services.codex_auth import (
    codex_env_for_account,
    codex_runtime_paths,
)


class CodexAppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexAppServerNotification:
    method: str
    params: dict[str, Any] | None


ProcessFactory = Callable[..., subprocess.Popen]
NotificationHandler = Callable[[CodexAppServerNotification], None]


class CodexAppServerClient:
    def __init__(
        self,
        account: Account,
        *,
        command: Sequence[str] | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
        notification_handler: NotificationHandler | None = None,
        timeout_seconds: float = 15,
    ) -> None:
        self._account = account
        self._command = tuple(command) if command is not None else None
        self._process_factory = process_factory
        self._notification_handler = notification_handler
        self._timeout_seconds = timeout_seconds
        self._process = None
        self._reader = None
        self._messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self._write_lock = threading.Lock()

    def __enter__(self) -> CodexAppServerClient:
        self.start()
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def start(self) -> None:
        if self._process is not None:
            return

        _, workspace_path = codex_runtime_paths(self._account)
        env = os.environ.copy()
        env.update(codex_env_for_account(self._account))
        command, path_dirs = _resolve_codex_command(self._command)
        _prepend_path_dirs(env, path_dirs)
        args = [*command, "app-server", "--listen", "stdio://"]
        self._process = self._process_factory(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(workspace_path),
            env=env,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "ai_quota_monitor",
                    "title": "ai-quota-monitor",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized")
        return result

    def read_account(self) -> dict[str, Any]:
        return self.request("account/read", {"refreshToken": False})

    def read_rate_limits(self) -> dict[str, Any]:
        return self.request("account/rateLimits/read")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.start()
        request_id = str(uuid.uuid4())
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._write_message(message)

        while True:
            try:
                item = self._messages.get(timeout=self._timeout_seconds)
            except queue.Empty as exc:
                raise TimeoutError(f"Timed out waiting for {method}") from exc

            if item is None:
                detail = self._stderr_tail()
                message = "Codex app-server exited"
                if detail:
                    message = f"{message}: {detail}"
                raise CodexAppServerError(message)
            if isinstance(item, BaseException):
                raise CodexAppServerError(str(item)) from item
            if item.get("id") != request_id:
                self._handle_notification_message(item)
                continue
            if "error" in item:
                error = item["error"]
                raise CodexAppServerError(f"{method} failed: {error}")

            result = item.get("result")
            if not isinstance(result, dict):
                raise CodexAppServerError(f"{method} response must be a JSON object")
            return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        self._write_message(message)

    def close(self) -> None:
        process = self._process
        if process is None:
            return

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self._process = None

    def _write_message(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexAppServerError("Codex app-server is not running")

        with self._write_lock:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._messages.put(None)
            return

        try:
            for line in process.stdout:
                message = json.loads(line)
                if self._handle_notification_message(message):
                    continue
                self._messages.put(message)
        except BaseException as exc:
            self._messages.put(exc)
        finally:
            self._messages.put(None)

    def _handle_notification_message(self, message: dict[str, Any]) -> bool:
        if "id" in message:
            return False
        method = message.get("method")
        if not isinstance(method, str):
            return False

        handler = self._notification_handler
        if handler is None:
            return False

        params = message.get("params")
        handler(
            CodexAppServerNotification(
                method=method,
                params=params if isinstance(params, dict) else None,
            )
        )
        return True

    def _stderr_tail(self) -> str:
        process = self._process
        if process is None or process.stderr is None or process.poll() is None:
            return ""
        try:
            value = process.stderr.read()
        except Exception:
            return ""
        return " ".join(value.split())[-1000:]


def _resolve_codex_command(
    command: tuple[str, ...] | None,
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    if command is not None:
        return command, ()

    try:
        from codex_cli_bin import bundled_codex_path, bundled_path_dir
    except ImportError:
        return ("codex",), ()

    path_dirs: tuple[Path, ...] = ()
    path_dir = bundled_path_dir()
    if path_dir is not None:
        path_dirs = (path_dir,)
    return (str(bundled_codex_path()),), path_dirs


def _prepend_path_dirs(env: dict[str, str], path_dirs: tuple[Path, ...]) -> None:
    if not path_dirs:
        return

    path_key = _path_env_key(env)
    if os.name == "nt":
        for key in list(env):
            if key.upper() == "PATH" and key != path_key:
                env.pop(key)

    path_values = [str(path_dir) for path_dir in path_dirs]
    current = env.get(path_key, "")
    existing = [
        entry
        for entry in current.split(os.pathsep)
        if entry and entry not in path_values
    ]
    env[path_key] = os.pathsep.join([*path_values, *existing])


def _path_env_key(env: dict[str, str]) -> str:
    if os.name != "nt":
        return "PATH"

    matching = [key for key in env if key.upper() == "PATH"]
    if "Path" in matching:
        return "Path"
    return matching[-1] if matching else "PATH"
