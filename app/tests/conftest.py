import pytest
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app
from app.core.auth import get_current_user
from app.models import User

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def cleanup_after_all():
    yield
    if FIXTURES_DIR.exists():
        shutil.rmtree(FIXTURES_DIR)


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
        "status": "ok",
        "doc_type": "health_report",
        "height_cm": 175.0,
        "weight_kg": 70.0,
        "bmi": 22.9,
        "body_fat": 18.0,
        "heart_rate": 72,
        "blood_pressure": "120/80",
        "blood_sugar": 5.2,
        "cholesterol": 4.5,
        "alt": 25.0,
        "uric_acid": 320.0,
        "other_findings": [],
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
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / "test_text.pdf"
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=12)
    text = "This is a health check report containing user height weight BMI body fat and other health data. " * 10
    pdf.multi_cell(0, 10, text)
    pdf.output(str(path))
    return path


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
