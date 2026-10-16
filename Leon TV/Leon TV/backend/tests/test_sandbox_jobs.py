import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from api.routers import sandbox


class SandboxJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_generation_returns_immediately_and_repeat_does_not_resubmit(self):
        finished = asyncio.Event()
        calls = []

        async def slow_generation(request):
            calls.append(request.prompt)
            await finished.wait()
            return {"success": True, "result": ["result/reference.png"]}

        app = FastAPI()
        app.include_router(sandbox.router)
        payload = {"job_id": "a" * 32, "tool": "t2i", "model": "gpt-image-2-all", "prompt": "海边邮差"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sandbox, "SANDBOX_JOBS_DIR", Path(directory)), \
             patch.object(sandbox, "SANDBOX_JOB_TASKS", {}), \
             patch.object(sandbox, "sandbox_t2i", slow_generation):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                submitted = await asyncio.wait_for(client.post("/api/sandbox/jobs", json=payload), timeout=0.5)
                self.assertEqual(submitted.status_code, 202)
                task = sandbox.SANDBOX_JOB_TASKS[payload["job_id"]]
                running = await client.get(f"/api/sandbox/jobs/{payload['job_id']}")
                self.assertEqual(running.json()["status"], "running")
                repeated = await client.post("/api/sandbox/jobs", json=payload)
                self.assertEqual(repeated.status_code, 202)
                finished.set()
                await task
                completed = await client.get(f"/api/sandbox/jobs/{payload['job_id']}")
                self.assertEqual(completed.json()["output"]["result"], ["result/reference.png"])
                self.assertEqual(calls, ["海边邮差"])
                changed = await client.post("/api/sandbox/jobs", json={**payload, "prompt": "其他文案"})
                self.assertEqual(changed.status_code, 409)

class SandboxVideoDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_controls_survive_async_job_boundary(self):
        from api.schemas.sandbox import SandboxJobRequest
        from models.video_client import VideoClient
        payload = SandboxJobRequest(job_id='c' * 32, tool='video', model='seedance-2.0',
                                    prompt='雨夜', ratio='9:16', duration=8, resolution='1080p')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sandbox, 'SANDBOX_JOBS_DIR', Path(directory) / 'jobs'), \
             patch.object(sandbox, 'SANDBOX_DIR', directory), \
             patch.object(sandbox, 'SANDBOX_HISTORY_FILE', str(Path(directory) / 'history.json')), \
             patch.object(sandbox, 'SANDBOX_JOB_TASKS', {}), \
             patch.object(VideoClient, 'generate_video', return_value='https://media.example/out.mp4') as generate:
            sandbox.SANDBOX_JOBS_DIR.mkdir()
            await sandbox.sandbox_start_job(payload)
            await sandbox.SANDBOX_JOB_TASKS[payload.job_id]
            self.assertEqual(generate.call_args.kwargs.get('video_ratio'), '9:16')
            self.assertEqual(generate.call_args.kwargs.get('duration'), 8)
            self.assertEqual(generate.call_args.kwargs.get('resolution'), '1080p')

    async def test_poll_timeout_can_resume_original_task_without_resubmission(self):
        from unittest.mock import Mock

        from api.schemas.sandbox import SandboxJobRequest
        from models.tudou_client import TudouClient
        submit_response = Mock()
        submit_response.raise_for_status.return_value = None
        submit_response.json.return_value = {'data': {'id': 'original-paid-task'}}
        payload = SandboxJobRequest(job_id='b' * 32, tool='video', model='seedance-2.0', prompt='雨夜')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sandbox, 'SANDBOX_JOBS_DIR', Path(directory) / 'jobs'), \
             patch.object(sandbox, 'SANDBOX_DIR', directory), \
             patch.object(sandbox, 'SANDBOX_HISTORY_FILE', str(Path(directory) / 'history.json')), \
             patch.object(sandbox, 'SANDBOX_JOB_TASKS', {}), \
             patch('models.tudou_client.Config.TUDOU_API_KEY', 'test-key'), \
             patch('models.tudou_client.requests.post', return_value=submit_response) as submit, \
             patch.object(TudouClient, '_poll', side_effect=[TimeoutError('local wait expired'), {'video_url': 'https://media.example/out.mp4'}]), \
             patch.object(TudouClient, '_download', side_effect=lambda url, path: Path(path).write_bytes(b'video')):
            sandbox.SANDBOX_JOBS_DIR.mkdir()
            app = FastAPI()
            app.include_router(sandbox.router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                await client.post('/api/sandbox/jobs', json=payload.model_dump())
                await sandbox.SANDBOX_JOB_TASKS[payload.job_id]
                timed_out = (await client.get(f'/api/sandbox/jobs/{payload.job_id}')).json()
                self.assertEqual(timed_out['status'], 'interrupted', '本地等待结束不代表上游失败')
                self.assertEqual(timed_out['output']['provider_task']['task_id'], 'original-paid-task')
                resumed = await client.post(f'/api/sandbox/jobs/{payload.job_id}/resume')
                self.assertEqual(resumed.status_code, 202)
                await sandbox.SANDBOX_JOB_TASKS[payload.job_id]
                completed = (await client.get(f'/api/sandbox/jobs/{payload.job_id}')).json()
                self.assertEqual(completed['status'], 'completed')
                self.assertTrue(completed['output']['video_path'])
                submit.assert_called_once()

    async def test_restart_recovers_task_without_needing_original_local_image(self):
        import json

        from api.schemas.sandbox import SandboxJobRequest
        from models.tudou_client import TudouClient
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sandbox, 'SANDBOX_JOBS_DIR', Path(directory) / 'jobs'), \
             patch.object(sandbox, 'SANDBOX_DIR', directory), \
             patch.object(sandbox, 'SANDBOX_HISTORY_FILE', str(Path(directory) / 'history.json')), \
             patch.object(sandbox, 'SANDBOX_JOB_TASKS', {}), \
             patch.object(TudouClient, '_poll', return_value={'video_url': 'https://media.example/out.mp4'}), \
             patch.object(TudouClient, '_download', side_effect=lambda url, path: Path(path).write_bytes(b'video')), \
             patch('models.tudou_client.requests.post') as submit:
            sandbox.SANDBOX_JOBS_DIR.mkdir()
            video = Path(directory) / 'videos/original.mp4'
            video.parent.mkdir()
            receipt_path = Path(f'{video}.tudou-task.json')
            receipt_path.write_text(json.dumps({'provider': 'tudou', 'base_url': 'https://api.ai-tudou.net',
                'task_id': 'already-paid-task', 'status': 'accepted', 'save_path': str(video),
                'request': {'model': 'seedance-2.0', 'prompt': '雨夜', 'duration': 5,
                            'aspect_ratio': '16:9', 'resolution': '720p'}}))
            request = SandboxJobRequest(job_id='d' * 32, tool='video', model='seedance-2.0',
                                        prompt='雨夜', image='result/deleted-reference.png')
            sandbox._write_job({'id': request.job_id, 'status': 'running', 'input': request.model_dump(),
                'provider_receipt_path': str(receipt_path), 'provider_task': {'task_id': 'already-paid-task'}, 'output': None})
            job = await sandbox.sandbox_get_job(request.job_id)
            self.assertEqual(job['status'], 'interrupted')
            await sandbox.sandbox_resume_job(request.job_id)
            await sandbox.SANDBOX_JOB_TASKS[request.job_id]
            job = await sandbox.sandbox_get_job(request.job_id)
            self.assertEqual(job['status'], 'completed')
            submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
