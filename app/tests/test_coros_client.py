"""Tests for the serial stdio MCP transport without a real Coros account."""

from __future__ import annotations

import io
import json
import subprocess

import pytest
from fastapi import HTTPException

from app.core import deps
from app.services.coros_client import CorosClient


class FakeProcess:
    def __init__(self, responses: list[dict]):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(json.dumps(item) + "\n" for item in responses))
        self.stderr = io.StringIO()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_coros_client_completes_mcp_handshake_and_tool_call_serially():
    process = FakeProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": '{"activities": []}'}]},
            },
        ]
    )
    client = CorosClient(process_factory=lambda *args, **kwargs: process)

    assert client.list_activities("20260810", "20260817") == {"activities": []}
    sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [item.get("id") for item in sent] == [1, None, 2]
    assert sent[-1]["method"] == "tools/call"
    client.close()
    assert process.terminated


def test_coros_client_passes_only_readonly_mcp_configuration():
    process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {}}])
    captured: dict = {}

    def process_factory(*args, **kwargs):
        captured.update(kwargs)
        return process

    client = CorosClient(
        process_factory=process_factory,
        environment={
            "COROS_MCP_TOOLSET": "readonly",
            "COROS_MCP_HIDE_AUTH_TOOLS": "1",
            "FITAGENT_COROS_MCP_CACHE_DIR": "C:/fitagent/coros-cache",
        },
    )

    assert captured["env"]["COROS_MCP_TOOLSET"] == "readonly"
    assert captured["env"]["COROS_MCP_HIDE_AUTH_TOOLS"] == "1"
    assert captured["env"]["FITAGENT_COROS_MCP_CACHE_DIR"] == "C:/fitagent/coros-cache"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["encoding"] == "utf-8"
    client.close()


def test_coros_client_syncs_private_cache_with_requested_range():
    process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {}}])
    captured: dict = {}

    def command_runner(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"partial": true, "failed_sources": ["sleep"]}',
            stderr="",
        )

    client = CorosClient(
        process_factory=lambda *args, **kwargs: process,
        command_runner=command_runner,
        sync_command=("provider-python", "-m", "runner", "sync"),
        environment={"FITAGENT_COROS_MCP_CACHE_DIR": "C:/fitagent/coros-cache"},
        working_directory="C:/fitagent",
    )

    summary = client.sync_cache("20260812", "20260818")

    assert captured["args"][0] == [
        "provider-python",
        "-m",
        "runner",
        "sync",
        "--from",
        "20260812",
        "--to",
        "20260818",
    ]
    assert captured["kwargs"]["env"]["FITAGENT_COROS_MCP_CACHE_DIR"] == "C:/fitagent/coros-cache"
    assert captured["kwargs"]["cwd"] == "C:/fitagent"
    assert summary == {"partial": True, "failed_sources": ["sleep"]}
    client.close()


def test_coros_client_rejects_provider_tool_error_payload():
    process = FakeProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": '{"error": "upstream"}'}]},
            },
        ]
    )
    client = CorosClient(process_factory=lambda *args, **kwargs: process)

    with pytest.raises(RuntimeError, match="get_sleep_data"):
        client.get_sleep_data()

    client.close()


class BlockingOutput:
    def __init__(self, initialize_response: str):
        self._first = initialize_response
        self._served = False

    def readline(self):
        if not self._served:
            self._served = True
            return self._first
        # The daemon reader is intentionally left blocked; the client must tear down this
        # poisoned protocol stream instead of reusing it for the next request.
        import threading

        threading.Event().wait()
        return ""

    def close(self):
        return None


def test_coros_client_resets_process_when_response_times_out():
    process = FakeProcess([])
    process.stdout = BlockingOutput(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n")
    client = CorosClient(
        process_factory=lambda *args, **kwargs: process,
        response_timeout_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="超时"):
        client.get_daily_metrics()
    assert process.terminated


def test_get_coros_returns_actionable_error_when_local_command_is_missing(monkeypatch):
    deps._coros_singleton.cache_clear()

    def unavailable_client():
        raise FileNotFoundError("coros-mcp.exe")

    monkeypatch.setattr(deps, "_coros_singleton", unavailable_client)

    with pytest.raises(HTTPException) as error:
        deps.get_coros()

    assert error.value.status_code == 503
    assert "install_coros_mcp.ps1" in str(error.value.detail)
