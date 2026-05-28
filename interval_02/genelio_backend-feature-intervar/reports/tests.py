"""Happy-path tests for report upload & listing.

WES / WGS now route to the real ``_genomic`` analyzer (BioAro template),
so these tests upload sanitised fixture PDFs from ``postman/fixtures/``
and assert ``parsed_data.status == "ok"`` plus a sanity check on the
parsed shape, instead of the old ``"stub"`` placeholder.
"""
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import User

# Real BioAro WES / WGS fixtures. Sanitised samples; parser must extract
# at least one variant + counts block.
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "postman" / "fixtures"
_WES_FIXTURE = _FIXTURES_DIR / "wes.pdf"
_WGS_FIXTURE = _FIXTURES_DIR / "wgs.pdf"


def _upload_file(path: Path, name: str | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name or path.name, path.read_bytes(), "application/pdf",
    )


class ReportsFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com", password="Sup3rSecret!",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_types_catalogue_authenticated(self):
        resp = self.client.get("/chatbot/report/types/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = {t["value"] for t in resp.data}
        self.assertEqual(
            values,
            {"wgs", "wes", "oral", "gut", "skin", "vaginal",
             "clinical_csv", "intervar"},
        )

    def test_types_catalogue_public_no_auth_required(self):
        """Report types are a public catalogue — no token needed."""
        anon = APIClient()
        resp = anon.get("/chatbot/report/types/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = {t["value"] for t in resp.data}
        self.assertEqual(
            values,
            {"wgs", "wes", "oral", "gut", "skin", "vaginal",
             "clinical_csv", "intervar"},
        )

    def test_types_catalogue_bad_token_returns_401(self):
        """
        DRF runs authentication before checking permissions.
        Sending a malformed/expired token still produces 401 — the fix
        for a stale token is to call /auth/jwt/refresh/ or simply omit
        the Authorization header entirely (the endpoint is AllowAny).
        """
        bad_token_client = APIClient()
        bad_token_client.credentials(HTTP_AUTHORIZATION="Bearer bad.token.here")
        resp = bad_token_client.get("/chatbot/report/types/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_upload_list_wes_real(self):
        """End-to-end smoke for the real WES analyzer.

        Uploads a sanitised BioAro WES PDF and asserts the structured
        analyzer ran (``status == "ok"``) and produced at least one
        variant — the smallest contract the chatbot pipeline depends on.
        """
        resp = self.client.post(
            "/chatbot/report/data/",
            {"report_type": "wes", "file": _upload_file(_WES_FIXTURE)},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["report_type"], "wes")
        self.assertEqual(resp.data["status"], "ready")

        parsed = resp.data["parsed_data"]
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["site"], "wes")
        report = parsed["report"]
        self.assertEqual(report["test"]["type"], "WES")
        self.assertIn("counts", report)
        self.assertGreaterEqual(len(report.get("variants", [])), 1)

        resp = self.client.get("/chatbot/report/data/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)

    def test_upload_wgs_real(self):
        """Same contract as the WES smoke, against the WGS template.

        WGS rows in the BioAro template lack the ``inherited_from``
        column, so this guards against the parser regressing on the
        column-count branch in ``_genomic._parse_variants``.
        """
        resp = self.client.post(
            "/chatbot/report/data/",
            {"report_type": "wgs", "file": _upload_file(_WGS_FIXTURE)},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["status"], "ready")

        parsed = resp.data["parsed_data"]
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["site"], "wgs")
        self.assertEqual(parsed["report"]["test"]["type"], "WGS")
        self.assertGreaterEqual(len(parsed["report"].get("variants", [])), 1)

    def test_other_user_cannot_see_report(self):
        other = User.objects.create_user(email="o@e.com", password="Sup3rSecret!")
        self.client.post(
            "/chatbot/report/data/",
            {"report_type": "wgs", "file": _upload_file(_WGS_FIXTURE, "s.pdf")},
            format="multipart",
        )

        self.client.force_authenticate(other)
        resp = self.client.get("/chatbot/report/data/")
        self.assertEqual(resp.data["count"], 0)
