from unittest.mock import MagicMock
import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.core.deps import get_agent
from app.main import app
from app.core.auth import get_current_user
from app.models import User

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def memory_backend(monkeypatch):
    """测试不向真实 mem0/LLM 写入个人信息，适配器自身测试可显式注入 SDK。"""
    from app.services import memory_service
    from app.tests.memory_fakes import FakeMemoryBackend

    backend = FakeMemoryBackend()
    monkeypatch.setattr(memory_service, "get_memory_backend", lambda: backend)
    yield backend


@pytest.fixture(scope="session", autouse=True)
def cleanup_after_all():
    """在测试会话结束后删除运行期间生成的临时夹具文件。"""
    yield
    for generated_fixture in ("test.jpg", "test_encrypted.pdf"):
        fixture_path = FIXTURES_DIR / generated_fixture
        if fixture_path.exists():
            fixture_path.unlink()


@pytest.fixture
def client():
    """提供将当前用户依赖替换为固定测试用户的 HTTP 客户端。"""
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="testuser")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    """提供不覆盖鉴权依赖的匿名 HTTP 客户端。"""
    app.dependency_overrides.clear()
    yield TestClient(app)


@pytest.fixture
def mock_health_data():
    """返回上传健康报告解析成功时使用的标准指标响应。"""
    return {
        "code": 0,
        "messages": [],
        "data": {
            "height_cm": {"value": 175.0, "unit": "cm"},
            "weight_kg": {"value": 70.0, "unit": "kg"},
            "bmi": {"value": 22.9, "unit": ""},
            "body_fat": {"value": 18.0, "unit": "%"},
            "heart_rate": {"value": 72, "unit": "bpm"},
            "blood_pressure": {"value": "120/80", "unit": ""},
            "blood_sugar": {"value": 5.2, "unit": "mmol/L"},
            "cholesterol": {"value": 4.5, "unit": "mmol/L"},
            "alt": {"value": 25.0, "unit": "U/L"},
            "uric_acid": {"value": 320.0, "unit": "μmol/L"},
        },
    }


@pytest.fixture
def image_file():
    """生成并返回用于上传测试的红色 JPEG 临时文件。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / "test.jpg"
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="red")
    img.save(path, "JPEG")
    return path


@pytest.fixture
def text_pdf():
    """返回仓库中可被正常解析的文本型健康报告 PDF。"""
    return FIXTURES_DIR / "text_health_report.pdf"


@pytest.fixture
def encrypted_pdf():
    """生成并返回带密码的 PDF，用于验证加密文件拒绝逻辑。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / "test_encrypted.pdf"
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("123456")
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def auth_client():
    """走真实 register → login，带 Bearer token 的 client。
    用于测试需要真实鉴权的端点（auth/fitness/chat）。"""
    app.dependency_overrides.clear()
    client = TestClient(app)
    username = f"testuser_{int(time.time() * 1000)}"  # 唯一用户名避免冲突
    client.post("/api/auth/register", json={"username": username, "password": "testpass123"})
    res = client.post("/api/auth/login", data={"username": username, "password": "testpass123"})
    token = res.json()["data"]["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def agent_mock(auth_client):
    """override get_agent，返回 mock ReactAgent。
    execute_stream 返回固定 SSE 事件序列，避免真调 LLM。"""

    async def stream_events():
        """生成默认的异步 Agent 事件。"""

        yield '{"type": "text", "content": "你好，我是健身助手"}'

    mock = MagicMock()
    mock.execute_stream.side_effect = lambda *_args, **_kwargs: stream_events()
    app.dependency_overrides[get_agent] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_agent, None)
