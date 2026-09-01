import json
import time
from urllib.error import URLError

from app.services.agent_tools import _with_circuit_breaker, _with_retry


def _make_flaky(fail_flag: dict, threshold=2, recovery=0.2):
    """构造一个受 fail_flag['on'] 控制的函数：True 抛网络异常，False 返回 'OK'。
    calls['n'] 记录真实调用次数，用于验证 OPEN 时是否被跳过。"""
    calls = {"n": 0}

    @_with_circuit_breaker(name="test", failure_threshold=threshold, recovery_timeout=recovery)
    @_with_retry(max_retries=0)
    def flaky():
        """按失败标记模拟网络异常或返回正常结果。"""
        calls["n"] += 1
        if fail_flag["on"]:
            raise URLError("service down")
        return "OK"

    return flaky, calls


class TestCircuitBreaker:
    def test_opens_after_threshold_then_fast_fails(self):
        """连续失败达阈值 → OPEN；OPEN 后不再真调（快速失败）"""
        fail = {"on": True}
        flaky, calls = _make_flaky(fail, threshold=2)

        r1 = json.loads(flaky())
        r2 = json.loads(flaky())
        assert r1["status"] == "error" and r2["status"] == "error"
        assert calls["n"] == 2  # 两次都真调了

        # 已 OPEN：第三次应快速失败，不真调
        r3 = json.loads(flaky())
        assert r3["status"] == "error"
        assert "熔断" in r3["message"]
        assert calls["n"] == 2  # 调用次数没涨 → 确实跳过了真实调用

    def test_recovers_after_cooldown(self):
        """冷却超时后 HALF_OPEN 放行试探，成功则 CLOSED 恢复"""
        fail = {"on": True}
        flaky, calls = _make_flaky(fail, threshold=2, recovery=0.2)
        flaky()
        flaky()  # 打到 OPEN
        assert calls["n"] == 2

        time.sleep(0.25)  # 超过冷却期
        fail["on"] = False  # 下游恢复正常
        assert flaky() == "OK"  # HALF_OPEN 试探成功
        assert calls["n"] == 3  # 真调了
        assert flaky() == "OK"  # 已 CLOSED，继续正常

    def test_halfopen_failure_reopens(self):
        """HALF_OPEN 试探仍失败 → 立刻重新 OPEN"""
        fail = {"on": True}
        flaky, calls = _make_flaky(fail, threshold=2, recovery=0.2)
        flaky()
        flaky()  # OPEN
        time.sleep(0.25)
        r = json.loads(flaky())  # HALF_OPEN 试探，仍失败
        assert r["status"] == "error"
        assert calls["n"] == 3  # 试探真调了一次
        # 重新 OPEN：下一次又快速失败，不真调
        json.loads(flaky())
        assert calls["n"] == 3

    def test_business_return_does_not_trip(self):
        """正常返回（非网络异常）不计入失败，熔断器保持 CLOSED"""
        breaker_calls = {"n": 0}

        @_with_circuit_breaker(name="ok", failure_threshold=2)
        @_with_retry(max_retries=0)
        def healthy():
            """返回正常业务结果以验证不会触发熔断。"""
            breaker_calls["n"] += 1
            return "正常结果"

        for _ in range(5):
            assert healthy() == "正常结果"
        assert breaker_calls["n"] == 5  # 全部真调，没被熔断
