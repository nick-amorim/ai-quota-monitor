from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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


class CodexAppServerClient:
    def __init__(
        self,
        account: Account,
        *,
        command: Sequence[str] = ("codex",),
        process_factory: ProcessFactory = subprocess.Popen,
        timeout_seconds: float = 15,
    ) -> None:
        self._account = account
        self._command = tuple(command)
        self._process_factory = process_factory
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
        args = [*self._command, "app-server", "--listen", "stdio://"]
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
                raise CodexAppServerError("Codex app-server exited")
            if isinstance(item, BaseException):
                raise CodexAppServerError(str(item)) from item
            if item.get("id") != request_id:
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
                self._messages.put(json.loads(line))
        except BaseException as exc:
            self._messages.put(exc)
        finally:
            self._messages.put(None)
