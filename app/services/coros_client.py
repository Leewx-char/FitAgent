"""用于 Coros 数据的串行、可重启标准输入输出 MCP 客户端。
客户端独占本地子进程并串行处理请求与响应；Windows 下以读取线程实现管道超时。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Mapping

from dotenv import load_dotenv

from app.utils.logger_handler import logger

load_dotenv()


class CorosClient:
    """管理 ``coros-mcp serve`` 生命周期和串行 JSON-RPC 通信。
该社区服务仅以只读工具集启动，认证须在启动子进程前通过服务方 CLI 完成。
"""

    _RESPONSE_TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        *,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        command: tuple[str, ...] = ("coros-mcp", "serve"),
        sync_command: tuple[str, ...] = ("coros-mcp", "sync"),
        environment: Mapping[str, str] | None = None,
        working_directory: str | None = None,
        response_timeout_seconds: float = _RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        """配置 Coros MCP 子进程工厂、命令和并发访问状态。"""
        self._process_factory = process_factory
        self._command_runner = command_runner
        self._command = command
        self._sync_command = sync_command
        self._environment = dict(environment or {})
        self._working_directory = working_directory
        self._response_timeout_seconds = response_timeout_seconds
        self._lock = threading.RLock()
        self._req_id = 0
        self.proc: subprocess.Popen | None = None
        self._closed = False
        self._start_process()

    def _build_environment(self) -> dict[str, str]:
        """将 Coros 配置显式传递给隔离的 MCP 子进程。"""

        env = os.environ.copy()
        for key in ("COROS_EMAIL", "COROS_PASSWORD", "COROS_REGION"):
            if key not in env:
                env[key] = os.getenv(key, "")
        env.update(self._environment)
        # MCP 标准输入输出使用 UTF-8 JSON，避免 Windows 继承 GBK 后无法处理中文或状态符号。
        env["PYTHONUTF8"] = "1"
        return env

    def _start_process(self) -> None:
        """终止旧进程后启动 UTF-8 的 MCP 子进程并完成初始化。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("Coros MCP 客户端已关闭")
            self._terminate_process()
            try:
                self.proc = self._process_factory(
                    list(self._command),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    env=self._build_environment(),
                    cwd=self._working_directory,
                )
                self._initialize()
                logger.info("Coros MCP 子进程已启动并完成握手")
            except Exception:
                self._terminate_process()
                raise

    def _ensure_running(self) -> None:
        """确保客户端未关闭且 MCP 子进程可用，必要时重启。"""
        if self._closed:
            raise RuntimeError("Coros MCP 客户端已关闭")
        if self.proc is None or self.proc.poll() is not None:
            logger.warning("检测到 Coros MCP 子进程不可用，正在重建连接")
            self._start_process()

    def _initialize(self) -> None:
        """完成 JSON-RPC initialize 握手并发送初始化通知。"""
        self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fitagent", "version": "2.1.0"},
            },
            ensure_running=False,
        )
        assert self.proc is not None and self.proc.stdin is not None
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.proc.stdin.write(json.dumps(notification) + "\n")
        self.proc.stdin.flush()

    @staticmethod
    def _readline_in_thread(stdout, output: queue.Queue) -> None:
        """在线程中读取一行进程输出，并将结果或异常放入队列。"""
        try:
            output.put(("line", stdout.readline()))
        except Exception as error:  # pragma: no cover - platform process failure
            output.put(("error", error))

    def _read_response_line(self) -> str:
        """在可移植超时机制下读取一行标准输出；超时后重置不安全的协议流。"""

        assert self.proc is not None and self.proc.stdout is not None
        output: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        reader = threading.Thread(
            target=self._readline_in_thread,
            args=(self.proc.stdout, output),
            daemon=True,
            name="coros-mcp-stdout-reader",
        )
        reader.start()
        try:
            kind, payload = output.get(timeout=self._response_timeout_seconds)
        except queue.Empty as error:
            self._terminate_process()
            raise RuntimeError("coros-mcp 响应超时，连接已重置，请稍后重试") from error
        if kind == "error":
            raise RuntimeError(f"读取 coros-mcp 响应失败：{payload}")
        line = str(payload).strip()
        if not line:
            return_code = self.proc.poll() if self.proc else "unknown"
            raise RuntimeError(f"coros-mcp 未返回响应，returncode={return_code}")
        return line

    def _send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        ensure_running: bool = True,
    ) -> dict[str, Any]:
        """在进程锁保护下发送 JSON-RPC 请求并等待其匹配响应。"""

        with self._lock:
            if ensure_running:
                self._ensure_running()
            if self.proc is None or self.proc.poll() is not None:
                raise RuntimeError("coros-mcp 子进程不可用")
            if self.proc.stdin is None:
                raise RuntimeError("coros-mcp stdin 不可用")

            self._req_id += 1
            request_id = self._req_id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
            try:
                self.proc.stdin.write(json.dumps(request) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                self._terminate_process()
                raise RuntimeError("coros-mcp 连接已断开，连接已重置") from error

            deadline = time.monotonic() + self._response_timeout_seconds
            while time.monotonic() < deadline:
                response = json.loads(self._read_response_line())
                if response.get("id") != request_id:
                    # JSON-RPC 通知没有标识，不能破坏有序请求响应交换，但仍保留可观测性。
                    logger.debug("忽略 Coros MCP 非匹配消息：method=%s", response.get("method", ""))
                    continue
                if "error" in response:
                    error = response["error"]
                    raise RuntimeError(error.get("message", str(error)))
                result = response.get("result", {})
                return result if isinstance(result, dict) else {}
            self._terminate_process()
            raise RuntimeError("coros-mcp 未返回匹配响应，连接已重置")

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具并解析首个 JSON 文本结果。"""
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        if content and isinstance(content[0], dict):
            text = content[0].get("text", "{}")
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                if parsed.get("error"):
                    raise RuntimeError(f"coros-mcp 工具 {name} 返回上游错误")
                return parsed
        return {}

    def get_daily_metrics(self, weeks: int = 4) -> list[dict[str, Any]]:
        """读取指定周数的日指标记录。"""
        return self._call_tool("get_daily_metrics", {"weeks": weeks}).get("records", [])

    def get_sleep_data(self, weeks: int = 4) -> list[dict[str, Any]]:
        """读取指定周数的睡眠记录。"""
        return self._call_tool("get_sleep_data", {"weeks": weeks}).get("records", [])

    def list_activities(self, start_day: str, end_day: str, size: int = 50) -> dict[str, Any]:
        """读取日期范围内、数量受限的活动记录。"""
        return self._call_tool(
            "list_activities",
            {"start_day": start_day, "end_day": end_day, "size": size},
        )

    def sync_cache(self, start_day: str, end_day: str) -> dict[str, Any]:
        """同步服务方私有缓存；由运动 API 显式触发，且先停进程保证单一写入者。"""

        with self._lock:
            self._terminate_process()
            command = [*self._sync_command, "--from", start_day, "--to", end_day]
            try:
                result = self._command_runner(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self._build_environment(),
                    cwd=self._working_directory,
                    timeout=self._response_timeout_seconds * 4,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("coros-mcp 数据同步超时，请稍后重试") from error
            except OSError as error:
                raise RuntimeError("无法启动 coros-mcp 数据同步命令") from error
            if result.returncode != 0:
                # 服务方输出可能包含上游实现细节，不写入 API 响应或日志；用户可运行认证状态命令排查。
                raise RuntimeError("coros-mcp 数据同步失败，请检查认证状态后重试")
            try:
                summary = json.loads(result.stdout.strip() or "{}")
            except json.JSONDecodeError:
                logger.warning("Coros MCP 缓存同步未返回可解析摘要")
                return {}
            return summary if isinstance(summary, dict) else {}

    def _terminate_process(self) -> None:
        """终止并关闭当前 MCP 子进程及其标准流。"""
        process, self.proc = self.proc, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except OSError:
            pass
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass

    def close(self) -> None:
        """显式释放子进程；已关闭客户端不能静默重启。"""

        with self._lock:
            self._closed = True
            self._terminate_process()
