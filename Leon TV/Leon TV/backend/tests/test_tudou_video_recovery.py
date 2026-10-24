"""Synthetic video recovery regressions; no provider requests or private state."""

import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from core.harness.store import lock_file
from models.tudou_client import TudouClient, TudouTaskError


RESULT = {"video_url": "https://media.example/accepted-video.mp4"}
REFRESHED_RESULT = {"video_url": "https://media.example/refreshed-video.mp4"}


def response(payload):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.return_value = None
    return value


def download(_url, path):
    Path(path).write_bytes(b"synthetic-video")


def media_http_error(status):
    failed = response({})
    failed.status_code = status
    return requests.HTTPError(f"synthetic media HTTP {status}", response=failed)


def completed_response(**changes):
    item = {"id": "accepted-video", "status": "completed", "result": REFRESHED_RESULT}
    item.update(changes)
    return response({"code": 0, "data": item})


class TudouVideoRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = (Path(self.directory.name) / "clip.mp4").resolve()
        self.receipt_path = Path(f"{self.path}.tudou-task.json")
        self.client = TudouClient(api_key="synthetic-key", base_url="https://relay.example")

    def generate(self, client=None):
        return (client or self.client).generate_video(
            "synthetic prompt", "", str(self.path), model="minimax-h3",
            duration=15, ratio="9:16", resolution="720p",
        )

    def receipt(self):
        return json.loads(self.receipt_path.read_text(encoding="utf-8"))

    def write_receipt(self, **changes):
        receipt = {
            "provider": "tudou", "base_url": "https://relay.example",
            "task_id": "accepted-video", "status": "accepted", "save_path": str(self.path),
            "request": {"model": "minimax-h3", "prompt": "synthetic prompt", "duration": 15,
                        "aspect_ratio": "9:16", "resolution": "720p"},
        }
        receipt.update(changes)
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def test_completed_delivery_is_reused_by_generate_and_resume(self):
        delivered = b"already-delivered-video"
        self.write_receipt(status="completed", result=RESULT, downloaded=True,
                           delivery_sha256=hashlib.sha256(delivered).hexdigest(), delivery_size_bytes=len(delivered))
        self.path.write_bytes(delivered)
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download") as delivery:
            self.assertEqual(self.generate(), RESULT["video_url"])
            self.assertEqual(self.client.resume_video(str(self.receipt_path)), RESULT["video_url"])
        post.assert_not_called()
        poll.assert_not_called()
        delivery.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"already-delivered-video")

    def test_successful_delivery_repairs_a_same_size_nonempty_overwrite(self):
        with patch.object(self.client, "_submit", return_value="accepted-video") as post, \
             patch.object(self.client, "_poll", return_value=RESULT) as poll, \
             patch.object(self.client, "_download", side_effect=download) as first_delivery:
            self.generate()
        expected = self.path.read_bytes()
        overwritten = b"corrupted-video"
        self.assertEqual(len(overwritten), len(expected))
        self.path.write_bytes(overwritten)
        with patch.object(self.client, "_submit") as new_post, \
             patch.object(self.client, "_poll") as new_poll, \
             patch.object(self.client, "_download", side_effect=download) as repair_delivery:
            self.assertEqual(self.client.resume_video(str(self.receipt_path)), RESULT["video_url"])
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(self.receipt()["delivery_sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.receipt()["delivery_size_bytes"], len(expected))
        post.assert_called_once()
        poll.assert_called_once()
        first_delivery.assert_called_once()
        new_post.assert_not_called()
        new_poll.assert_not_called()
        repair_delivery.assert_called_once_with(RESULT["video_url"], self.path)

    def test_legacy_downloaded_receipt_redownloads_before_binding_digest(self):
        self.write_receipt(status="completed", result=RESULT, downloaded=True)
        self.path.write_bytes(b"untrusted-local-video")
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery:
            self.assertEqual(self.client.resume_video(str(self.receipt_path)), RESULT["video_url"])
        delivery.assert_called_once_with(RESULT["video_url"], self.path)
        post.assert_not_called()
        poll.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"synthetic-video")
        self.assertEqual(self.receipt()["delivery_sha256"], hashlib.sha256(b"synthetic-video").hexdigest())
        self.assertEqual(self.receipt()["delivery_size_bytes"], len(b"synthetic-video"))

    def test_cached_delivery_requires_matching_digest_and_byte_count(self):
        delivered = b"synthetic-video"
        digest = hashlib.sha256(delivered).hexdigest()
        for fields in ({"delivery_sha256": "0" * 64, "delivery_size_bytes": len(delivered)},
                       {"delivery_sha256": digest, "delivery_size_bytes": len(delivered) + 1}):
            with self.subTest(fields=fields):
                self.write_receipt(status="completed", result=RESULT, downloaded=True, **fields)
                self.path.write_bytes(delivered)
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch.object(self.client, "_download", side_effect=download) as delivery:
                    self.assertEqual(self.client.resume_video(str(self.receipt_path)), RESULT["video_url"])
                delivery.assert_called_once_with(RESULT["video_url"], self.path)
                post.assert_not_called()
                poll.assert_not_called()
                self.assertEqual(self.receipt()["delivery_sha256"], digest)
                self.assertEqual(self.receipt()["delivery_size_bytes"], len(delivered))

    def test_expired_cached_url_refreshes_same_task_once_before_retrying_delivery(self):
        for status in (403, 404, 410):
            with self.subTest(status=status):
                self.path.unlink(missing_ok=True)
                self.write_receipt(status="completed", result=RESULT, downloaded=False)
                attempts = []

                def deliver(url, path):
                    attempts.append(url)
                    if len(attempts) == 1:
                        raise media_http_error(status)
                    self.assertEqual(self.receipt()["result"], REFRESHED_RESULT,
                                     "refreshed evidence must persist before another media download")
                    self.assertEqual(self.receipt()["previous_completed_result"], RESULT)
                    download(url, path)

                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch("models.tudou_client.requests.get", return_value=completed_response()) as query, \
                     patch.object(self.client, "_download", side_effect=deliver):
                    self.assertEqual(self.client.resume_video(str(self.receipt_path)), REFRESHED_RESULT["video_url"])
                self.assertEqual(attempts, [RESULT["video_url"], REFRESHED_RESULT["video_url"]])
                query.assert_called_once()
                self.assertEqual(query.call_args.args[0], "https://relay.example/v1/tasks/accepted-video")
                post.assert_not_called()
                poll.assert_not_called()
                self.assertEqual(self.receipt()["status"], "completed")
                self.assertIsNone(self.receipt()["error"])

    def test_unsuccessful_refresh_preserves_original_completed_result(self):
        http_failure = completed_response()
        http_failure.raise_for_status.side_effect = requests.HTTPError("synthetic query HTTP 503")
        bad_responses = (
            http_failure,
            response({"code": 503, "data": {"id": "accepted-video", "status": "completed", "result": REFRESHED_RESULT}}),
            completed_response(id="different-task"),
            completed_response(id=None),
            completed_response(status="processing"),
            completed_response(status="failed"),
            completed_response(result={"video_url": "file:///invalid.mp4"}),
            completed_response(result=None),
        )
        for index, observed in enumerate(bad_responses):
            with self.subTest(case=index):
                self.write_receipt(status="completed", result=RESULT, downloaded=False)
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch("models.tudou_client.requests.get", return_value=observed) as query, \
                     patch.object(self.client, "_download", side_effect=media_http_error(403)) as delivery:
                    with self.assertRaises(TudouTaskError) as caught:
                        self.client.resume_video(str(self.receipt_path))
                self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
                self.assertEqual(self.receipt()["status"], "completed")
                self.assertEqual(self.receipt()["result"], RESULT)
                self.assertNotIn("previous_completed_result", self.receipt())
                query.assert_called_once()
                delivery.assert_called_once()
                post.assert_not_called()
                poll.assert_not_called()

    def test_refreshed_download_failure_does_not_refresh_again_or_lose_evidence(self):
        self.write_receipt(status="completed", result=RESULT, downloaded=False)
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch("models.tudou_client.requests.get", return_value=completed_response()) as query, \
             patch.object(self.client, "_download", side_effect=[media_http_error(403), media_http_error(404)]) as delivery:
            with self.assertRaises(TudouTaskError) as caught:
                self.client.resume_video(str(self.receipt_path))
        self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
        self.assertEqual(delivery.call_count, 2)
        query.assert_called_once()
        post.assert_not_called()
        poll.assert_not_called()
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["result"], REFRESHED_RESULT)
        self.assertEqual(receipt["previous_completed_result"], RESULT)

    def test_local_delivery_errors_and_timeouts_never_refresh_or_downgrade_completion(self):
        for failure in (OSError("synthetic disk failure"), requests.Timeout("synthetic media timeout"),
                        media_http_error(500), TudouTaskError("synthetic delivery callback", "accepted-video", "query_failed", "unknown")):
            with self.subTest(failure=type(failure).__name__):
                self.write_receipt(status="completed", result=RESULT, downloaded=False)
                with patch("models.tudou_client.requests.get") as query, \
                     patch.object(self.client, "_download", side_effect=failure):
                    with self.assertRaises(TudouTaskError) as caught:
                        self.client.resume_video(str(self.receipt_path))
                self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
                self.assertEqual(self.receipt()["status"], "completed")
                self.assertEqual(self.receipt()["result"], RESULT)
                query.assert_not_called()

    def test_first_completion_does_not_refresh_a_delivery_failure(self):
        with patch.object(self.client, "_submit", return_value="accepted-video") as post, \
             patch.object(self.client, "_poll", return_value=RESULT) as poll, \
             patch("models.tudou_client.requests.get") as query, \
             patch.object(self.client, "_download", side_effect=media_http_error(403)):
            with self.assertRaises(TudouTaskError) as caught:
                self.generate()
        self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
        self.assertEqual(self.receipt()["result"], RESULT)
        query.assert_not_called()
        post.assert_called_once()
        poll.assert_called_once()

    def test_completed_result_delivers_missing_or_empty_media_without_repolling(self):
        for existing in (None, b""):
            with self.subTest(existing=existing):
                self.path.unlink(missing_ok=True)
                if existing is not None:
                    self.path.write_bytes(existing)
                self.write_receipt(status="completed", result=RESULT, downloaded=True)
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch.object(self.client, "_download", side_effect=download) as delivery:
                    self.assertEqual(self.generate(), RESULT["video_url"])
                post.assert_not_called()
                poll.assert_not_called()
                delivery.assert_called_once()
                self.assertEqual(self.path.read_bytes(), b"synthetic-video")

    def test_download_failure_retains_completed_result_and_resumes_only_delivery(self):
        with patch.object(self.client, "_submit", return_value="accepted-video") as post, \
             patch.object(self.client, "_poll", return_value=RESULT) as poll, \
             patch.object(self.client, "_download", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(TudouTaskError) as caught:
                self.generate()
        self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
        self.assertEqual(self.receipt()["result"], RESULT)
        with patch.object(self.client, "_submit") as new_post, \
             patch.object(self.client, "_poll", side_effect=AssertionError("must reuse completed result")) as new_poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery:
            self.assertEqual(self.client.resume_video(str(self.receipt_path)), RESULT["video_url"])
        post.assert_called_once()
        poll.assert_called_once()
        new_post.assert_not_called()
        new_poll.assert_not_called()
        delivery.assert_called_once()
        self.assertTrue(self.receipt()["downloaded"])
        self.assertIsNone(self.receipt()["error"])

    def test_every_terminal_state_preserves_receipt_without_query_or_resubmit(self):
        for status in ("failed", "error", "expired", "cancelled", "rejected"):
            for task_id in ("terminal-video", None):
                with self.subTest(status=status, task_id=task_id):
                    self.write_receipt(status=status, task_id=task_id,
                                       error={"code": "terminal-evidence", "message": "synthetic terminal"})
                    original = self.receipt_path.read_bytes()
                    with patch.object(self.client, "_submit") as post, \
                         patch.object(self.client, "_poll") as poll, \
                         patch.object(self.client, "_download") as delivery:
                        for invoke in (self.generate, lambda: self.client.resume_video(str(self.receipt_path))):
                            with self.assertRaises(TudouTaskError) as caught:
                                invoke()
                            self.assertEqual((caught.exception.status, caught.exception.task_id), (status, task_id))
                    post.assert_not_called()
                    poll.assert_not_called()
                    delivery.assert_not_called()
                    self.assertEqual(self.receipt_path.read_bytes(), original)

    def test_unknown_submission_stays_unknown_without_task_or_resubmit(self):
        with patch("models.tudou_client.requests.post", side_effect=requests.Timeout("synthetic response lost")) as post:
            for _ in range(2):
                with self.assertRaises(TudouTaskError) as caught:
                    self.generate()
                self.assertEqual((caught.exception.status, caught.exception.task_id), ("unknown", None))
        post.assert_called_once()
        self.assertEqual(self.receipt()["status"], "unknown")

    def test_http_200_business_failure_does_not_accept_embedded_task_id(self):
        failures = (
            {"code": 503, "message": "synthetic business failure", "data": {"id": "upstream-only"}},
            {"code": "fail_to_fetch_task", "id": "upstream-only"},
            {"code": False, "data": {"id": "upstream-only"}},
        )
        for payload in failures:
            with self.subTest(payload=payload):
                self.receipt_path.unlink(missing_ok=True)
                with patch("models.tudou_client.requests.post", return_value=response(payload)) as post, \
                     patch.object(self.client, "_poll") as poll:
                    for _ in range(2):
                        with self.assertRaises(TudouTaskError) as caught:
                            self.generate()
                        self.assertEqual((caught.exception.status, caught.exception.task_id), ("unknown", None))
                post.assert_called_once()
                poll.assert_not_called()
                self.assertIsNone(self.receipt()["task_id"])

    def test_http_502_keeps_unknown_and_ignores_nested_upstream_id(self):
        failed = response({"error": {"code": "fail_to_fetch_task", "message": json.dumps({
            "status": "failed", "id": "upstream-only", "http_status": 503,
        })}})
        failed.status_code = 502
        failed.raise_for_status.side_effect = requests.HTTPError("synthetic gateway failure", response=failed)
        with patch("models.tudou_client.requests.post", return_value=failed) as post, \
             patch.object(self.client, "_poll") as poll:
            for _ in range(2):
                with self.assertRaises(TudouTaskError) as caught:
                    self.generate()
                self.assertEqual((caught.exception.status, caught.exception.task_id), ("unknown", None))
        post.assert_called_once()
        poll.assert_not_called()
        self.assertIsNone(self.receipt()["task_id"])
        self.assertEqual(self.receipt()["status"], "unknown")

    def test_resume_rejects_receipt_copied_to_a_different_output_lock(self):
        self.write_receipt(status="completed", result=RESULT, downloaded=True)
        copied = self.path.with_name("copied.tudou-task.json")
        copied.write_bytes(self.receipt_path.read_bytes())
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download") as delivery:
            with self.assertRaises(ValueError):
                self.client.resume_video(str(copied))
        post.assert_not_called()
        poll.assert_not_called()
        delivery.assert_not_called()

    def test_legacy_success_envelopes_keep_their_task_ids(self):
        for payload in ({"id": "accepted-video"}, {"task_id": "accepted-video"},
                        {"data": {"id": "accepted-video"}},
                        {"code": 0, "data": {"task_id": "accepted-video"}},
                        {"code": 200, "data": {"id": "accepted-video"}}):
            with self.subTest(payload=payload), \
                 patch("models.tudou_client.requests.post", return_value=response(payload)):
                self.assertEqual(self.client._submit("/v1/videos/generations", {}), "accepted-video")

    def test_simultaneous_generations_share_one_submission_and_delivery(self):
        first_save, second_attempt, release = threading.Event(), threading.Event(), threading.Event()
        counter_lock = threading.Lock()
        saves, locks = 0, 0
        original_save = TudouClient._save_receipt

        def save(client, path, receipt):
            nonlocal saves
            if receipt["status"] == "submitting":
                with counter_lock:
                    saves += 1
                    number = saves
                if number == 1:
                    first_save.set()
                    if not release.wait(5):
                        raise AssertionError("synthetic first caller was not released")
                else:
                    second_attempt.set()
            return original_save(client, path, receipt)

        def acquire(path):
            nonlocal locks
            with counter_lock:
                locks += 1
                if locks == 2:
                    second_attempt.set()
            return lock_file(path)

        other = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
        with patch("models.tudou_client.lock_file", side_effect=acquire), \
             patch.object(TudouClient, "_save_receipt", save), \
             patch.object(TudouClient, "_submit", return_value="accepted-video") as post, \
             patch.object(TudouClient, "_poll", return_value=RESULT) as poll, \
             patch.object(TudouClient, "_download", side_effect=download) as delivery, \
             ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.generate)
            try:
                self.assertTrue(first_save.wait(5))
                second = pool.submit(self.generate, other)
                self.assertTrue(second_attempt.wait(5))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), RESULT["video_url"])
            self.assertEqual(second.result(timeout=5), RESULT["video_url"])
        post.assert_called_once()
        poll.assert_called_once()
        delivery.assert_called_once()

    def test_resume_and_generate_share_one_delivery_lock(self):
        first_poll, second_attempt, release = threading.Event(), threading.Event(), threading.Event()
        counter_lock = threading.Lock()
        polls, locks = 0, 0

        def poll(_task_id):
            nonlocal polls
            with counter_lock:
                polls += 1
                number = polls
            if number == 1:
                first_poll.set()
                if not release.wait(5):
                    raise AssertionError("synthetic first caller was not released")
            else:
                second_attempt.set()
            return RESULT

        def acquire(path):
            nonlocal locks
            with counter_lock:
                locks += 1
                if locks == 2:
                    second_attempt.set()
            return lock_file(path)

        other = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
        with patch("models.tudou_client.lock_file", side_effect=acquire), \
             patch.object(TudouClient, "_submit", return_value="accepted-video") as post, \
             patch.object(TudouClient, "_poll", side_effect=poll) as queries, \
             patch.object(TudouClient, "_download", side_effect=download) as delivery, \
             ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.generate)
            try:
                self.assertTrue(first_poll.wait(5))
                second = pool.submit(other.resume_video, str(self.receipt_path))
                self.assertTrue(second_attempt.wait(5))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), RESULT["video_url"])
            self.assertEqual(second.result(timeout=5), RESULT["video_url"])
        post.assert_called_once()
        queries.assert_called_once()
        delivery.assert_called_once()
