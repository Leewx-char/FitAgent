import json
import select # 用于给 stdin/stdout 加超时
import subprocess # 启动和管理子进程
import os
from dotenv import load_dotenv

load_dotenv()

# 请求是_send发的
class CorosClient:
    def __init__(self):
        """
        构造环境变量字典，传给子进程
        coros-mcp 作为子进程，它有自己独立的环境变量空间。
        当前进程读到了 .env，但子进程不一定有。所以显式拷贝一份传过去。
        """
        env = os.environ.copy()
        env.setdefault("COROS_EMAIL", os.getenv("COROS_EMAIL", ""))
        env.setdefault("COROS_PASSWORD", os.getenv("COROS_PASSWORD", ""))
        env.setdefault("COROS_REGION", os.getenv("COROS_REGION", ""))

        # 启动一个独立的进程
        self.proc = subprocess.Popen(
            ["coros-mcp", "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        """
        _req_id 是自增计数器，每个请求的 ID 不能重复，
        这是 MCP 协议的要求。然后立即调用 _initialize() 完成握手。
        """
        self._req_id = 0
        self._initialize()

    """
    MCP 协议规定：客户端必须先在 initialize 时自报家门，
    服务端才知道你是什么程序。_send("initialize", ...) 
    发了这个请求，得到一个回应。然后还要发一个 notifications/initialized 通知
    （不需要等回复），告诉服务端"我准备好了"。这两步少任何一步服务端都不理你。
    """
    def _initialize(self):
        resp = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "fitagent", "version": "1.0.0"},
        })
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.proc.stdin.write(json.dumps(notification) + "\n")
        self.proc.stdin.flush()

    def _send(self, method: str, params: dict | None = None) -> dict:
        # 1. 进程存活检查
        if self.proc.poll() is not None:
            raise RuntimeError(f"coros-mcp 子进程已退出，returncode={self.proc.returncode}")

        self._req_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params or {},
        }

        # 2. 写入 stdin
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()

        # 3. 读 stdout（带 30s 超时，防止子进程崩溃时永久阻塞）
        ready, _, _ = select.select([self.proc.stdout], [], [], 30.0)
        if not ready:
            raise RuntimeError(f"coros-mcp 响应超时（30s），method={method}")
        if self.proc.poll() is not None:
            raise RuntimeError(f"coros-mcp 子进程在响应前退出，returncode={self.proc.returncode}")

        response = json.loads(self.proc.stdout.readline())
        if "error" in response:
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        return response.get("result", {})

    # 对_send的二次封装
    def _call_tool(self, name: str, arguments: dict) -> dict:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        if content and isinstance(content[0], dict):
            # result.content[0].text → 这才是 JSON 字符串
            return json.loads(content[0].get("text", "{}"))
        return {}

    # 过去4周的 HRV、静息心率、训练负荷、VO2max
    def get_daily_metrics(self, weeks: int = 4) -> list:
        return self._call_tool("get_daily_metrics", {"weeks": weeks}).get("records", [])

    # 过去4周的深睡、浅睡、REM时长
    def get_sleep_data(self, weeks: int = 4) -> list:
        return self._call_tool("get_sleep_data", {"weeks": weeks}).get("records", [])

    # 这个时间段的运动记录
    def list_activities(self, start_day: str, end_day: str, size: int = 50) -> dict:
        return self._call_tool("list_activities", {
            "start_day": start_day,
            "end_day": end_day,
            "size": size,
        })

    def close(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)