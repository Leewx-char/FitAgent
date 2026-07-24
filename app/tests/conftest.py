from unittest.mock import MagicMock
import time
import pytest
from pathlib import Path
from datetime import date, timedelta
from fastapi.testclient import TestClient
from app.core.database import SessionLocal
from app.core.deps import get_agent, get_coros
from app.main import app
from app.core.auth import get_current_user
from app.models import User, FitnessData

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def cleanup_after_all():
    yield
    for generated_fixture in ("test.jpg", "test_encrypted.pdf"):
        fixture_path = FIXTURES_DIR / generated_fixture
        if fixture_path.exists():
            fixture_path.unlink()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="testuser")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    app.dependency_overrides.clear()
    yield TestClient(app)


@pytest.fixture
def mock_health_data():
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
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / "test.jpg"
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="red")
    img.save(path, "JPEG")
    return path


@pytest.fixture
def text_pdf():
    return FIXTURES_DIR / "text_health_report.pdf"


@pytest.fixture
def encrypted_pdf():
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
def coros_mock(auth_client):
    """override get_coros，返回 mock 的 CorosClient。
    依赖 auth_client 保证在其之后执行（auth_client 会 clear overrides）。"""
    mock = MagicMock()
    mock.get_daily_metrics.return_value = []
    mock.get_sleep_data.return_value = []
    mock.list_activities.return_value = {"activities": []}
    app.dependency_overrides[get_coros] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_coros, None)


@pytest.fixture
def agent_mock(auth_client):
    """override get_agent，返回 mock ReactAgent。
    execute_stream 返回固定 SSE 事件序列，避免真调 LLM。"""
    mock = MagicMock()
    mock.execute_stream.return_value = iter(
        [
            '{"type": "text", "content": "你好，我是健身助手"}',
        ]
    )
    app.dependency_overrides[get_agent] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_agent, None)


@pytest.fixture
def seed_fitness_data(auth_client):
    """造 3 天 daily_metrics 数据，用于测试 GET /api/fitness/* 端点。
    用 today-N 动态日期，保证数据始终在近 4 周内（daily 端点默认查近 4 周）。
    teardown 时清理，避免垃圾数据累积。"""
    me = auth_client.get("/api/auth/me").json()
    user_id = me["data"]["id"]
    db = SessionLocal()
    today = date.today()
    records = [
        FitnessData(
            user_id=user_id,
            date=today - timedelta(days=1),
            data_type="daily_metrics",
            data='{"training_load": 50, "avg_sleep_hrv": 60, "rhr": 55}',
        ),
        FitnessData(
            user_id=user_id,
            date=today - timedelta(days=2),
            data_type="daily_metrics",
            data='{"training_load": 55, "avg_sleep_hrv": 58, "rhr": 56}',
        ),
        FitnessData(
            user_id=user_id,
            date=today - timedelta(days=3),
            data_type="daily_metrics",
            data='{"training_load": 60, "avg_sleep_hrv": 62, "rhr": 54}',
        ),
    ]
    try:
        db.add_all(records)
        db.commit()
        yield records
        for r in records:
            db.delete(r)
        db.commit()
    finally:
        db.close()
