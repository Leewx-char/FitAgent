from unittest.mock import patch


class TestUploadHealthDoc:

    def test_upload_without_auth(self, anon_client, image_file):
        with open(image_file, "rb") as f:
            response = anon_client.post(
                "/api/upload/health-doc",
                files={"file": ("test.jpg", f, "image/jpeg")},
            )
        assert response.status_code == 401

    def test_upload_unsupported_file_type(self, client):
        fake_txt = b"this is a text file, not an image or pdf"
        response = client.post(
            "/api/upload/health-doc",
            files={"file": ("test.txt", fake_txt, "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "不支持" in data["message"]

    def test_upload_image_success(self, client, image_file, mock_health_data):
        with patch("app.services.doc_parser._extract_with_vl", return_value=mock_health_data):
            with open(image_file, "rb") as f:
                response = client.post(
                    "/api/upload/health-doc",
                    files={"file": ("test.jpg", f, "image/jpeg")},
                )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["data"]["height_cm"] == 175.0
        assert data["data"]["weight_kg"] == 70.0
        assert data["data"]["bmi"] == 22.9
        assert data["data"]["heart_rate"] == 72

    def test_upload_text_pdf_success(self, client, text_pdf, mock_health_data):
        with patch("app.services.doc_parser._extract_with_llm", return_value=mock_health_data):
            with open(text_pdf, "rb") as f:
                response = client.post(
                    "/api/upload/health-doc",
                    files={"file": ("test.pdf", f, "application/pdf")},
                )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_upload_encrypted_pdf(self, client, encrypted_pdf):
        with open(encrypted_pdf, "rb") as f:
            response = client.post(
                "/api/upload/health-doc",
                files={"file": ("encrypted.pdf", f, "application/pdf")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "encrypted"
        assert "加密" in data["message"]
