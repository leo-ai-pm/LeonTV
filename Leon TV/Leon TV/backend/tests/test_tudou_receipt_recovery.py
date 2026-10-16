"""Exercise paid-task recovery boundaries without contacting a provider."""

import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import requests
from fastapi import FastAPI

from api.routers import sandbox
from models.tudou_client import TudouClient


def response(payload):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.return_value = None
    return value


class TudouReceiptRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = ExitStack()
        self.directory = self.patches.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        jobs = self.root / "jobs"
        jobs.mkdir()
        for name, value in (
            ("SANDBOX_DIR", self.directory),
            ("SANDBOX_JOBS_DIR", jobs),
            ("SANDBOX_HISTORY_FILE", str(self.root / "history.json")),
            ("SANDBOX_JOB_TASKS", {}),
        ):
            self.patches.enter_context(patch.object(sandbox, name, value))
        self.patches.enter_context(patch("models.tudou_client.Config.TUDOU_API_KEY", "fixture-key"))
        self.patches.enter_context(patch.object(sandbox.logger, "exception"))
        app = FastAPI()
        app.include_router(sandbox.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        tasks = list(sandbox.SANDBOX_JOB_TASKS.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
        self.patches.close()

    def payload(self, job_id):
        return {
            "job_id": job_id,
            "tool": "video",
            "model": "seedance-2.0",
            "prompt": "雨夜咖啡馆",
            "ratio": "16:9",
            "duration": 5,
            "resolution": "720p",
        }

    async def settle(self, job_id):
        task = sandbox.SANDBOX_JOB_TASKS.get(job_id)
        if task:
            await task
        result = await self.client.get(f"/api/sandbox/jobs/{job_id}")
        self.assertEqual(result.status_code, 200)
        return result.json()

    async def test_accepted_task_recovers_after_one_receipt_write_failure(self):
        original_save = TudouClient._save_receipt
        failed_once = False

        def save(client, path, receipt):
            nonlocal failed_once
            if receipt.get("status") == "accepted" and not failed_once:
                failed_once = True
                raise OSError("fixture: one accepted receipt write failure")
            return original_save(client, path, receipt)

        job_id = "e" * 32
        with patch("models.tudou_client.requests.post", return_value=response({
            "data": {"id": "already-paid-task"}
        })) as submit, patch.object(TudouClient, "_save_receipt", save), \
             patch.object(TudouClient, "_poll", return_value={
                 "video_url": "https://media.example/out.mp4"
             }) as poll, patch.object(TudouClient, "_download", side_effect=
                 lambda url, path: Path(path).write_bytes(b"fixture-video")):
            submitted = await self.client.post("/api/sandbox/jobs", json=self.payload(job_id))
            self.assertEqual(submitted.status_code, 202)
            interrupted = await self.settle(job_id)
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["output"]["provider_task"]["task_id"], "already-paid-task")
            poll.assert_not_called()
            job = json.loads((sandbox.SANDBOX_JOBS_DIR / f"{job_id}.json").read_text())
            original_receipt = json.loads(Path(job["provider_receipt_path"]).read_text())
            self.assertIsNone(original_receipt["task_id"], "故障须发生在提交后回执尚未写入ID的边界")
            resumed = await self.client.post(f"/api/sandbox/jobs/{job_id}/resume")
            self.assertEqual(resumed.status_code, 202, resumed.json())
            completed = await self.settle(job_id)
            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["output"]["video_path"])
            poll.assert_called_once_with("already-paid-task")
            submit.assert_called_once()

    async def test_submission_timeout_without_task_id_cannot_resume_or_resubmit(self):
        job_id = "f" * 32
        with patch("models.tudou_client.requests.post", side_effect=requests.Timeout("fixture: response lost")) as submit, \
             patch("models.tudou_client.requests.get") as query:
            submitted = await self.client.post("/api/sandbox/jobs", json=self.payload(job_id))
            self.assertEqual(submitted.status_code, 202)
            unknown = await self.settle(job_id)
            self.assertEqual(unknown["status"], "interrupted")
            self.assertIsNone(unknown["output"]["provider_task"]["task_id"])
            self.assertEqual(unknown["output"]["provider_task"]["error_code"], "submit_unknown")
            resumed = await self.client.post(f"/api/sandbox/jobs/{job_id}/resume")
            self.assertEqual(resumed.status_code, 409)
            repeated = await self.client.post("/api/sandbox/jobs", json=self.payload(job_id))
            self.assertEqual(repeated.status_code, 202)
            self.assertEqual(repeated.json()["status"], "interrupted")
            submit.assert_called_once()
            query.assert_not_called()

    async def test_observed_terminal_failure_is_not_resumed_or_resubmitted(self):
        observed = json.loads((Path(__file__).parent / "fixtures/tudou_seedance_timeout.json").read_text())["observation"]
        job_id = "a" * 32
        with patch("models.tudou_client.requests.post", return_value=response({
            "data": {"id": observed["task_id"]}
        })) as submit, patch("models.tudou_client.requests.get", return_value=response({
            "data": observed
        })) as query, patch.object(TudouClient, "_download") as download:
            await self.client.post("/api/sandbox/jobs", json=self.payload(job_id))
            failed = await self.settle(job_id)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["output"]["provider_task"]["task_id"], observed["task_id"])
            self.assertEqual(failed["output"]["provider_task"]["error_code"], "upstream_failed")
            resumed = await self.client.post(f"/api/sandbox/jobs/{job_id}/resume")
            self.assertEqual(resumed.status_code, 409)
            repeated = await self.client.post("/api/sandbox/jobs", json=self.payload(job_id))
            self.assertEqual(repeated.status_code, 202)
            self.assertEqual(repeated.json()["status"], "failed")
            submit.assert_called_once()
            query.assert_called_once()
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
