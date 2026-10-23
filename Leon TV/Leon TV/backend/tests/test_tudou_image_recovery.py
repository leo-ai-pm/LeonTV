"""Image submission/recovery faults with synthetic files and no provider traffic."""

import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from core.harness.store import lock_file
from models.tudou_client import TudouClient, TudouPollTimeout, TudouTaskError

RESULT = {"images": [{"url": ["https://media.example/frame.png"], "expires_at": 2000000000}]}


def response(payload):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.return_value = None
    return value


def download(_url, path):
    Path(path).write_bytes(b"synthetic-image")


class TudouImageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = (Path(self.directory.name) / "shot-01.png").resolve()
        self.receipt_path = Path(f"{self.path}.tudou-task.json")
        self.client = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.network_mock.assert_not_called()

    def generate(self, client=None, prompt="synthetic scene"):
        return (client or self.client).generate_image(prompt, None, "9:16", save_path=str(self.path))

    def receipt(self):
        return json.loads(self.receipt_path.read_text(encoding="utf-8"))

    def test_records_submission_and_acceptance_before_following_steps(self):
        def submit(url, **kwargs):
            saved = self.receipt()
            self.assertEqual((saved["kind"], saved["status"], saved["task_id"]), ("image", "submitting", None))
            self.assertEqual(saved["save_path"], str(self.path))
            self.assertEqual(url, "https://relay.example/v1/images/generations/async")
            self.assertEqual(kwargs["json"], {"model": "gpt-image-2-all", "prompt": "synthetic scene",
                "size": "9:16", "resolution": "1k", "quality": "medium"})
            return response({"data": {"id": "image-task"}})

        def poll(task_id):
            saved = self.receipt()
            self.assertEqual((task_id, saved["status"], saved["task_id"]), ("image-task", "accepted", "image-task"))
            return copy.deepcopy(RESULT)

        def deliver(url, path):
            saved = self.receipt()
            self.assertEqual((saved["status"], saved["result"]), ("completed", RESULT))
            download(url, path)

        with patch("models.tudou_client.requests.post", side_effect=submit) as post, \
             patch.object(self.client, "_poll", side_effect=poll), \
             patch.object(self.client, "_download", side_effect=deliver):
            self.assertEqual(self.generate(), str(self.path))
        post.assert_called_once()
        saved = self.receipt()
        self.assertTrue(saved["downloaded"] and saved["source_saved"])
        self.assertNotIn("synthetic-key", self.receipt_path.read_text())
        self.assertEqual(json.loads(Path(f"{self.path}.tudou-source.json").read_text()),
                         {"url": RESULT["images"][0]["url"][0], "expires_at": 2000000000})

    def test_unknown_submission_is_persistent_and_never_posted_again(self):
        failures = [requests.Timeout("synthetic timeout"), response({"data": {}}),
                    requests.HTTPError(response=Mock(status_code=503))]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                self.path = (Path(directory) / "image.png").resolve()
                self.receipt_path = Path(f"{self.path}.tudou-task.json")
                with patch("models.tudou_client.requests.post", side_effect=[failure]) as post, \
                     patch.object(self.client, "_poll") as poll:
                    with self.assertRaises(TudouTaskError) as first:
                        self.generate()
                    self.assertEqual((first.exception.code, self.receipt()["status"]), ("submit_unknown", "unknown"))
                    reopened = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
                    with self.assertRaises(TudouTaskError):
                        self.generate(reopened)
                    with self.assertRaises(TudouTaskError):
                        reopened.resume_image(str(self.receipt_path))
                    post.assert_called_once()
                    poll.assert_not_called()

    def test_submitting_write_failure_prevents_post(self):
        with patch.object(self.client, "_save_receipt", side_effect=OSError("synthetic write failure")), \
             patch.object(self.client, "_submit") as post:
            with self.assertRaises(OSError):
                self.generate()
            post.assert_not_called()

    def test_accepted_write_failure_keeps_uncertain_intent_blocked(self):
        original = self.client._save_receipt

        def save(path, receipt):
            if receipt["status"] == "accepted":
                raise OSError("synthetic accepted write failure")
            original(path, receipt)

        with patch.object(self.client, "_submit", return_value="paid-image") as post, \
             patch.object(self.client, "_save_receipt", side_effect=save), \
             patch.object(self.client, "_poll") as poll:
            with self.assertRaises(TudouTaskError) as caught:
                self.generate()
            self.assertEqual((caught.exception.code, caught.exception.task_id), ("receipt_failed", "paid-image"))
            self.assertEqual((self.receipt()["status"], self.receipt()["task_id"]), ("submitting", None))
            with self.assertRaises(TudouTaskError):
                self.generate()
            post.assert_called_once()
            poll.assert_not_called()

    def test_accepted_task_survives_interruption_and_reopens_without_post(self):
        with patch.object(self.client, "_submit", return_value="paid-image") as post, \
             patch.object(self.client, "_poll", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.generate()
            self.assertEqual((self.receipt()["status"], self.receipt()["task_id"]), ("accepted", "paid-image"))
        reopened = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
        with patch.object(reopened, "_submit") as new_post, \
             patch.object(reopened, "_poll", return_value=copy.deepcopy(RESULT)) as poll, \
             patch.object(reopened, "_download", side_effect=download):
            self.assertEqual(reopened.resume_image(str(self.receipt_path)), str(self.path))
        post.assert_called_once()
        new_post.assert_not_called()
        poll.assert_called_once_with("paid-image")

    def test_callback_failure_after_accepted_write_keeps_the_saved_task(self):
        def notify(update):
            if update["status"] == "accepted":
                raise OSError("synthetic callback failure")

        client = TudouClient(api_key="synthetic-key", base_url="https://relay.example", task_callback=notify)
        with patch.object(client, "_submit", return_value="paid-image") as post, \
             patch.object(client, "_poll", return_value=copy.deepcopy(RESULT)) as poll, \
             patch.object(client, "_download", side_effect=download):
            with self.assertRaises(TudouTaskError) as caught:
                self.generate(client)
            self.assertEqual(caught.exception.code, "receipt_failed")
            self.assertEqual((self.receipt()["status"], self.receipt()["task_id"]), ("accepted", "paid-image"))
            self.assertEqual(self.generate(client), str(self.path))
            post.assert_called_once()
            poll.assert_called_once_with("paid-image")

    def test_poll_timeout_and_query_failure_resume_the_same_task(self):
        for failure in (TudouPollTimeout("paid-image"), requests.ConnectionError("synthetic query failure")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                self.path = (Path(directory) / "image.png").resolve()
                self.receipt_path = Path(f"{self.path}.tudou-task.json")
                with patch.object(self.client, "_submit", return_value="paid-image") as post, \
                     patch.object(self.client, "_poll", side_effect=[failure, copy.deepcopy(RESULT)]) as poll, \
                     patch.object(self.client, "_download", side_effect=download):
                    with self.assertRaises((TudouPollTimeout, TudouTaskError)):
                        self.generate()
                    self.assertEqual((self.receipt()["status"], self.receipt()["task_id"]), ("unknown", "paid-image"))
                    self.assertEqual(self.generate(), str(self.path))
                    post.assert_called_once()
                    self.assertEqual(poll.call_args_list[0].args, ("paid-image",))
                    self.assertEqual(poll.call_args_list[1].args, ("paid-image",))

    def test_terminal_upstream_failure_does_not_query_or_submit_again(self):
        with patch.object(self.client, "_submit", return_value="failed-image") as post, \
             patch.object(self.client, "_poll", side_effect=TudouTaskError("synthetic failed task", "failed-image", "upstream_failed", "failed")) as poll:
            with self.assertRaises(TudouTaskError):
                self.generate()
            with self.assertRaises(TudouTaskError):
                self.generate()
            with self.assertRaises(TudouTaskError):
                self.client.resume_image(str(self.receipt_path))
            post.assert_called_once()
            poll.assert_called_once()

    def test_explicit_submission_rejection_is_not_reposted(self):
        rejected = Mock(status_code=400)
        with patch("models.tudou_client.requests.post", side_effect=requests.HTTPError(response=rejected)) as post:
            with self.assertRaises(TudouTaskError) as caught:
                self.generate()
            self.assertEqual((caught.exception.status, self.receipt()["status"]), ("rejected", "rejected"))
            with self.assertRaises(TudouTaskError):
                self.generate()
            post.assert_called_once()

    def test_download_failure_uses_persisted_completion_on_resume(self):
        with patch.object(self.client, "_submit", return_value="paid-image") as post, \
             patch.object(self.client, "_poll", return_value=copy.deepcopy(RESULT)) as poll, \
             patch.object(self.client, "_download", side_effect=OSError("synthetic download failure")):
            with self.assertRaises(TudouTaskError) as caught:
                self.generate()
            self.assertEqual((caught.exception.code, self.receipt()["status"]), ("delivery_failed", "completed"))
        reopened = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
        with patch.object(reopened, "_submit") as new_post, patch.object(reopened, "_poll") as new_poll, \
             patch.object(reopened, "_download", side_effect=download):
            self.assertEqual(reopened.resume_image(str(self.receipt_path)), str(self.path))
        post.assert_called_once()
        poll.assert_called_once()
        new_post.assert_not_called()
        new_poll.assert_not_called()

    def test_sidecar_failure_preserves_download_and_repairs_only_delivery(self):
        with patch.object(self.client, "_submit", return_value="paid-image") as post, \
             patch.object(self.client, "_poll", return_value=copy.deepcopy(RESULT)) as poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery, \
             patch.object(self.client, "_save_image_source", side_effect=OSError("synthetic sidecar failure")):
            with self.assertRaises(TudouTaskError):
                self.generate()
            self.assertEqual(self.path.read_bytes(), b"synthetic-image")
            self.assertTrue(self.receipt()["downloaded"])
        with patch.object(self.client, "_submit") as new_post, patch.object(self.client, "_poll") as new_poll, \
             patch.object(self.client, "_download") as new_delivery:
            self.assertEqual(self.generate(), str(self.path))
        post.assert_called_once()
        poll.assert_called_once()
        delivery.assert_called_once()
        new_post.assert_not_called()
        new_poll.assert_not_called()
        new_delivery.assert_not_called()
        self.assertTrue(Path(f"{self.path}.tudou-source.json").is_file())

    def test_reused_path_rejects_request_address_and_media_kind_changes(self):
        with patch.object(self.client, "_submit", return_value="paid-image"), \
             patch.object(self.client, "_poll", return_value=copy.deepcopy(RESULT)), \
             patch.object(self.client, "_download", side_effect=download):
            self.generate()
        with patch.object(self.client, "_submit") as post, patch.object(self.client, "_poll") as poll:
            with self.assertRaises(ValueError):
                self.generate(prompt="different request")
            with self.assertRaises(ValueError):
                self.client.resume_video(str(self.receipt_path))
            original = self.receipt()
            for field, value in (("kind", "video"), ("base_url", "https://changed.example"),
                                 ("save_path", str(self.path.with_name("different.png")))):
                changed = {**original, field: value}
                self.receipt_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.client.resume_image(str(self.receipt_path))
            post.assert_not_called()
            poll.assert_not_called()

    def test_same_output_path_is_exclusive_across_clients(self):
        first_post, release, second_lock = threading.Event(), threading.Event(), threading.Event()
        count_lock, attempts = threading.Lock(), 0

        def acquire(path):
            nonlocal attempts
            with count_lock:
                attempts += 1
                if attempts == 2:
                    second_lock.set()
            return lock_file(path)

        def submit(_endpoint, _body):
            first_post.set()
            if not release.wait(5):
                raise AssertionError("Synthetic caller was not released")
            return "one-paid-image"

        other = TudouClient(api_key="synthetic-key", base_url="https://relay.example")
        with patch("models.tudou_client.lock_file", side_effect=acquire), \
             patch.object(TudouClient, "_submit", side_effect=submit) as post, \
             patch.object(TudouClient, "_poll", return_value=copy.deepcopy(RESULT)), \
             patch.object(TudouClient, "_download", side_effect=download), \
             ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.generate)
            try:
                self.assertTrue(first_post.wait(5))
                second = pool.submit(self.generate, other)
                self.assertTrue(second_lock.wait(5))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), str(self.path))
            self.assertEqual(second.result(timeout=5), str(self.path))
            post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
