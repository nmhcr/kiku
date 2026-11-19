import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from test_service import FakeEngine
from webapi.app import create_app

BOOK = b"Chapter One\nHello world.\n\nChapter Two\nSecond text here.\n"


class SlowEngine(FakeEngine):
    def synthesize(self, text):
        time.sleep(0.2)
        return super().synthesize(text)


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = TestClient(create_app(self.root / "projects"))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def create(self, filename="book.txt", content=BOOK, data=None):
        response = self.client.post(
            "/api/projects",
            files={"file": (filename, content, "text/plain")},
            data=data or {"title": "API Book"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def run_job(self, project_id, options=None):
        # Keep the patch active while the background task actually runs.
        with patch("audiobook_creator.service.create_synthesizer", return_value=FakeEngine()):
            response = self.client.post(
                f"/api/projects/{project_id}/synthesize", json=options or {}
            )
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()["job_id"]
            for _ in range(200):
                job = self.client.get(f"/api/projects/{project_id}/jobs/{job_id}").json()
                if job["state"] in ("done", "failed"):
                    break
                time.sleep(0.01)
        return job_id, job

    def test_index_and_docs(self):
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("Kiku", index.text)
        self.assertIn("/static/app.js", index.text)
        for path in ("/static/app.js", "/static/style.css", "/static/index.html", "/docs"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_get_settings(self):
        project_id = self.create()
        response = self.client.get(f"/api/projects/{project_id}/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "API Book")
        self.assertEqual(response.json()["voice"]["voice"], "F2")

    def test_create_and_status(self):
        project_id = self.create()
        self.assertTrue((self.root / "projects" / project_id / "book.toml").is_file())
        status = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(status["title"], "API Book")
        self.assertEqual(len(status["chapters"]), 2)
        self.assertEqual(status["done"], 0)

    def test_create_rejects_unsupported_file(self):
        response = self.client.post(
            "/api/projects",
            files={"file": ("book.pdf", b"x", "application/pdf")},
            data={"title": "Nope"},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_settings(self):
        project_id = self.create()
        response = self.client.patch(
            f"/api/projects/{project_id}",
            json={
                "voice": "M2",
                "chapters_per_file": 2,
                "name_pattern": "{title} - {chapter}",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["changed"])
        settings = response.json()["settings"]
        self.assertEqual(settings["voice"]["voice"], "M2")
        self.assertEqual(settings["output_options"]["chapters_per_file"], 2)
        status = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(status["output_plan"]["chapters_per_file"], 2)

    def test_synthesize_job_and_events(self):
        project_id = self.create()
        job_id, job = self.run_job(project_id)
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["result"]["synthesized"], 2)
        self.assertEqual(job["result"]["failed"], 0)
        event_response = self.client.get(f"/api/projects/{project_id}/events?job_id={job_id}")
        self.assertIn("data:", event_response.text)
        status = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(status["done"], 2)

    def test_duplicate_synthesis_rejected(self):
        project_id = self.create()
        with patch("audiobook_creator.service.create_synthesizer", return_value=SlowEngine()):
            first = self.client.post(f"/api/projects/{project_id}/synthesize", json={})
            self.assertEqual(first.status_code, 200)
            second = self.client.post(f"/api/projects/{project_id}/synthesize", json={})
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.json()["detail"]["job_id"], first.json()["job_id"])

    def test_pause_resume_and_stop(self):
        project_id = self.create()
        with patch("audiobook_creator.service.create_synthesizer", return_value=SlowEngine()):
            job_id = self.client.post(
                f"/api/projects/{project_id}/synthesize", json={}
            ).json()["job_id"]
            time.sleep(0.05)
            paused = self.client.post(f"/api/projects/{project_id}/jobs/{job_id}/pause")
            self.assertEqual(paused.status_code, 200)
            time.sleep(0.15)
            state = self.client.get(f"/api/projects/{project_id}/jobs/{job_id}").json()
            self.assertEqual(state["state"], "paused")
            resumed = self.client.post(f"/api/projects/{project_id}/jobs/{job_id}/resume")
            self.assertEqual(resumed.status_code, 200)
            for _ in range(100):
                state = self.client.get(f"/api/projects/{project_id}/jobs/{job_id}").json()
                if state["state"] in ("done", "failed", "stopped"):
                    break
                time.sleep(0.02)
            self.assertEqual(state["state"], "done")

        with patch("audiobook_creator.service.create_synthesizer", return_value=SlowEngine()):
            job_id = self.client.post(
                f"/api/projects/{project_id}/synthesize",
                json={"force": True},
            ).json()["job_id"]
            stopped = self.client.post(f"/api/projects/{project_id}/jobs/{job_id}/stop")
            self.assertEqual(stopped.status_code, 200)
            for _ in range(100):
                state = self.client.get(f"/api/projects/{project_id}/jobs/{job_id}").json()
                if state["state"] in ("done", "failed", "stopped"):
                    break
                time.sleep(0.02)
            self.assertEqual(state["state"], "stopped")
            self.assertTrue(state["result"]["stopped"])

    def test_assemble_and_audio(self):
        project_id = self.create()
        self.run_job(project_id)
        response = self.client.post(
            f"/api/projects/{project_id}/assemble",
            json={"overrides": {"chapters_per_file": 1, "name_pattern": "{title} - {chapter}"}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        files = response.json()
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["chapter_count"], 1)
        audio = self.client.get(
            f"/api/projects/{project_id}/audio/{quote(files[0]['path'].split('/')[-1])}"
        )
        self.assertEqual(audio.status_code, 200)
        self.assertTrue(audio.headers["content-type"].startswith("audio/"))

    def test_preview(self):
        project_id = self.create()
        with patch("audiobook_creator.service.create_synthesizer", return_value=FakeEngine()):
            response = self.client.post(
                f"/api/projects/{project_id}/preview",
                json={"text": "Hello there", "voice": "M2"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("audio/"))

    def test_unknown_project(self):
        response = self.client.get("/api/projects/missing")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
