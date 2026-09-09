from __future__ import annotations

import json
import queue

from ai_quota_monitor.models import Account
from ai_quota_monitor.services.app_server import CodexAppServerClient


class FakeStdin:
    def __init__(self, responses, *, emit_notification: bool = False):
        self._responses = responses
        self._emit_notification = emit_notification
        self.messages = []

    def write(self, value):
        message = json.loads(value)
        self.messages.append(message)
        request_id = message.get("id")
        if request_id is None:
            return

        method = message["method"]
        if method == "initialize":
            result = {"serverInfo": {"name": "codex"}}
        elif method == "account/read":
            result = {"requiresOpenaiAuth": True, "account": None}
        elif method == "account/rateLimits/read":
            if self._emit_notification:
                self._responses.put(
                    {
                        "method": "account/rateLimits/updated",
                        "params": {
                            "rateLimits": {
                                "primary": {
                                    "usedPercent": 41,
                                    "windowDurationMins": 300,
                                }
                            }
                        },
                    }
                )
            result = {"rateLimits": {"primary": {"usedPercent": 25}}}
        else:
            result = {}
        self._responses.put({"id": request_id, "result": result})

    def flush(self):
        return None


class FakeStdout:
    def __init__(self, responses):
        self._responses = responses

    def __iter__(self):
        return self

    def __next__(self):
        item = self._responses.get(timeout=2)
        if item is None:
            raise StopIteration
        return json.dumps(item) + "\n"


class FakeProcess:
    def __init__(self, *, emit_notification: bool = False):
        self.responses = queue.Queue()
        self.stdin = FakeStdin(self.responses, emit_notification=emit_notification)
        self.stdout = FakeStdout(self.responses)
        self.stderr = None
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True
        self.responses.put(None)

    def wait(self, timeout=None):
        self.terminated = True
        return 0

    def kill(self):
        self.terminated = True
        self.responses.put(None)


def test_app_server_client_sends_initialize_and_account_requests(tmp_path):
    account = Account(
        name="Account A",
        slug="account-a",
        codex_home=str(tmp_path / "codex-home"),
        workspace_path=str(tmp_path / "workspace"),
    )
    created = {}

    def fake_factory(args, **kwargs):
        process = FakeProcess()
        created["args"] = args
        created["kwargs"] = kwargs
        created["process"] = process
        return process

    with CodexAppServerClient(
        account,
        command=("codex",),
        process_factory=fake_factory,
    ) as client:
        account_result = client.read_account()
        limits_result = client.read_rate_limits()

    assert created["args"] == ["codex", "app-server", "--listen", "stdio://"]
    assert created["kwargs"]["env"]["CODEX_HOME"] == str((tmp_path / "codex-home").resolve())
    assert created["kwargs"]["env"]["CODEX_API_KEY"] == ""
    assert created["kwargs"]["cwd"] == str((tmp_path / "workspace").resolve())
    assert account_result["requiresOpenaiAuth"] is True
    assert limits_result["rateLimits"]["primary"]["usedPercent"] == 25

    messages = created["process"].stdin.messages
    assert messages[0]["method"] == "initialize"
    assert messages[1] == {"method": "initialized"}
    assert messages[2]["method"] == "account/read"
    assert messages[2]["params"] == {"refreshToken": False}
    assert messages[3]["method"] == "account/rateLimits/read"
    assert "params" not in messages[3]


def test_app_server_client_dispatches_notifications_between_responses(tmp_path):
    account = Account(
        name="Account A",
        slug="account-a",
        codex_home=str(tmp_path / "codex-home"),
        workspace_path=str(tmp_path / "workspace"),
    )
    notifications = []

    def fake_factory(args, **kwargs):
        return FakeProcess(emit_notification=True)

    with CodexAppServerClient(
        account,
        command=("codex",),
        process_factory=fake_factory,
        notification_handler=notifications.append,
    ) as client:
        limits_result = client.read_rate_limits()

    assert limits_result["rateLimits"]["primary"]["usedPercent"] == 25
    assert len(notifications) == 1
    assert notifications[0].method == "account/rateLimits/updated"
    assert notifications[0].params["rateLimits"]["primary"]["usedPercent"] == 41
