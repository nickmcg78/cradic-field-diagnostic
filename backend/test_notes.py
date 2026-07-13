"""Unit tests for technician-notes storage + email.

Covers the email send path (mocked HTTP) and the /notes endpoint contract:
503 when DATABASE_URL is unset, validation, and the full store+email flow with
storage and Resend mocked. Run: python -m unittest test_notes
"""
import os
import unittest
from unittest import mock

# Auth token app.py captures at import; set before importing app.
os.environ.setdefault("APP_PASSWORD", "testpass")
os.environ["STREAMING"] = "0"  # keep endpoint import/paths simple for tests

import notes  # noqa: E402


class TestSendNoteEmail(unittest.TestCase):
    def setUp(self):
        for k in ("RESEND_API_KEY", "NOTES_FROM", "NOTES_TO"):
            os.environ.pop(k, None)

    def test_missing_config_returns_reason(self):
        ok, reason = notes.send_note_email(
            "Nick", "Luv A Duck", "Trave 340", None, "belt worn"
        )
        self.assertFalse(ok)
        self.assertIn("RESEND_API_KEY", reason)

    @mock.patch("notes.requests.post")
    def test_success_calls_resend_correctly(self, mock_post):
        os.environ["RESEND_API_KEY"] = "re_test"
        os.environ["NOTES_FROM"] = "field-notes@cradicai.com"
        os.environ["NOTES_TO"] = "manager@example.com"
        mock_post.return_value = mock.Mock(status_code=200, text="ok")

        ok, reason = notes.send_note_email(
            "Nick", "Luv A Duck", "Trave 340", "A/12/A-00629", "belt worn"
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "sent")
        _, kwargs = mock_post.call_args
        self.assertEqual(mock_post.call_args[0][0], "https://api.resend.com/emails")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer re_test")
        self.assertEqual(
            kwargs["json"]["from"],
            "Cradic Field Diagnostic <field-notes@cradicai.com>",
        )
        self.assertEqual(kwargs["json"]["to"], ["manager@example.com"])
        self.assertIn("Trave 340", kwargs["json"]["subject"])
        self.assertIn("belt worn", kwargs["json"]["text"])

    @mock.patch("notes.requests.post")
    def test_http_error_returns_reason(self, mock_post):
        os.environ["RESEND_API_KEY"] = "re_test"
        os.environ["NOTES_FROM"] = "field-notes@cradicai.com"
        os.environ["NOTES_TO"] = "manager@example.com"
        mock_post.return_value = mock.Mock(status_code=422, text="bad domain")

        ok, reason = notes.send_note_email("Nick", "C", "M", None, "note")
        self.assertFalse(ok)
        self.assertIn("422", reason)


class TestNotesEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def _auth(self):
        # app.py loads .env with override=True, so use whatever password it
        # actually loaded rather than assuming our test default survived.
        token = self.app_module.APP_PASSWORD
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def test_unauthorized(self):
        res = self.client.post("/notes", json={"tech_name": "Nick", "note_text": "x"})
        self.assertEqual(res.status_code, 401)

    def test_validation_rejects_empty(self):
        res = self.client.post(
            "/notes", headers=self._auth(), json={"tech_name": "", "note_text": ""}
        )
        self.assertEqual(res.status_code, 400)

    def test_503_when_db_unconfigured(self):
        os.environ.pop("DATABASE_URL", None)
        res = self.client.post(
            "/notes",
            headers=self._auth(),
            json={
                "tech_name": "Nick",
                "note_text": "belt worn",
                "machine": "Trave 340",
                "customer": "Luv A Duck",
            },
        )
        self.assertEqual(res.status_code, 503)
        self.assertIn("DATABASE_URL", res.get_json()["error"])

    def test_full_store_and_email(self):
        n = self.app_module.notes
        with mock.patch.object(n, "database_configured", return_value=True), \
                mock.patch.object(n, "store_note", return_value=7) as m_store, \
                mock.patch.object(n, "send_note_email", return_value=(True, "sent")) as m_email, \
                mock.patch.object(n, "mark_emailed") as m_mark:
            res = self.client.post(
                "/notes",
                headers=self._auth(),
                json={
                    "tech_name": "Nick",
                    "note_text": "belt worn",
                    "machine": "Trave 340",
                    "customer": "Luv A Duck",
                    "serial": "A/12/A-00629",
                },
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"stored": True, "emailed": True})
        m_store.assert_called_once()
        m_email.assert_called_once()
        m_mark.assert_called_once_with(7)

    def test_stored_but_email_unconfigured(self):
        n = self.app_module.notes
        with mock.patch.object(n, "database_configured", return_value=True), \
                mock.patch.object(n, "store_note", return_value=8), \
                mock.patch.object(
                    n, "send_note_email",
                    return_value=(False, "email not configured (missing RESEND_API_KEY)"),
                ):
            res = self.client.post(
                "/notes",
                headers=self._auth(),
                json={
                    "tech_name": "Nick",
                    "note_text": "belt worn",
                    "machine": "Trave 340",
                },
            )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["stored"])
        self.assertFalse(data["emailed"])
        self.assertIn("RESEND_API_KEY", data["reason"])


if __name__ == "__main__":
    unittest.main()
