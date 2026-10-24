"""Synthetic image byte-identity/delivery recovery; no provider or visual acceptance."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from models.tudou_client import TudouClient, TudouTaskError

RESULT = {"images": [{"url": ["https://media.example/accepted-image.png"], "expires_at": 2000000000}]}
REFRESHED_RESULT = {
    "images": [{"url": ["https://media.example/refreshed-image.png"], "expires_at": 2100000000}],
}
IMAGE = b"synthetic-image"


def response(payload):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.return_value = None
    return value


def download(_url, path):
    Path(path).write_bytes(IMAGE)


def media_http_error(status):
    failed = response({})
    failed.status_code = status
    return requests.HTTPError(f"synthetic media HTTP {status}", response=failed)


def completed_response(**changes):
    item = {"id": "accepted-image", "status": "completed", "result": REFRESHED_RESULT}
    item.update(changes)
    return response({"code": 0, "data": item})


class TudouImageDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = (Path(self.directory.name) / "frame.png").resolve()
        self.receipt_path = Path(f"{self.path}.tudou-task.json")
        self.source_path = Path(f"{self.path}.tudou-source.json")
        self.client = TudouClient(api_key="synthetic-key", base_url="https://relay.example")

    def generate(self):
        return self.client.generate_image("synthetic prompt", None, "1:1", save_path=str(self.path))

    def receipt(self):
        return json.loads(self.receipt_path.read_text(encoding="utf-8"))

    def write_receipt(self, **changes):
        receipt = {
            "provider": "tudou", "kind": "image", "base_url": "https://relay.example",
            "task_id": "accepted-image", "status": "completed", "save_path": str(self.path),
            "request": {"model": "gpt-image-2-all", "prompt": "synthetic prompt", "size": "1:1",
                        "resolution": "1k", "quality": "medium"},
            "result": RESULT, "downloaded": False, "source_saved": False,
        }
        receipt.update(changes)
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def assert_bound(self):
        self.assertEqual(self.receipt()["delivery_sha256"], hashlib.sha256(IMAGE).hexdigest())
        self.assertEqual(self.receipt()["delivery_size_bytes"], len(IMAGE))
        self.assertTrue(self.receipt()["downloaded"])

    def test_first_delivery_binds_bytes_and_both_entry_points_reuse_them(self):
        with patch.object(self.client, "_submit", return_value="accepted-image") as post, \
             patch.object(self.client, "_poll", return_value=RESULT) as poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery:
            self.assertEqual(self.generate(), str(self.path))
        self.assert_bound()
        self.assertEqual(self.receipt()["request"]["size"], "1:1")
        with patch.object(self.client, "_submit") as new_post, \
             patch.object(self.client, "_poll") as new_poll, \
             patch.object(self.client, "_download") as new_delivery, \
             patch("models.tudou_client.requests.get") as query:
            self.assertEqual(self.generate(), str(self.path))
            self.assertEqual(self.client.resume_image(str(self.receipt_path)), str(self.path))
        post.assert_called_once()
        poll.assert_called_once_with("accepted-image")
        delivery.assert_called_once_with(RESULT["images"][0]["url"][0], self.path)
        new_post.assert_not_called()
        new_poll.assert_not_called()
        new_delivery.assert_not_called()
        query.assert_not_called()
        self.assertEqual(self.path.read_bytes(), IMAGE)
        self.assertEqual(json.loads(self.source_path.read_text()), {
            "url": RESULT["images"][0]["url"][0], "expires_at": 2000000000,
        })

    def test_same_size_replacement_is_redelivered_from_original_task(self):
        self.write_receipt(downloaded=True, delivery_sha256=hashlib.sha256(IMAGE).hexdigest(),
                           delivery_size_bytes=len(IMAGE))
        replaced = b"X" * len(IMAGE)
        self.path.write_bytes(replaced)
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery:
            self.assertEqual(self.generate(), str(self.path))
        self.assertEqual(self.path.read_bytes(), IMAGE)
        self.assert_bound()
        delivery.assert_called_once_with(RESULT["images"][0]["url"][0], self.path)
        post.assert_not_called()
        poll.assert_not_called()

    def test_legacy_unbound_nonempty_cache_must_be_redelivered(self):
        self.write_receipt(downloaded=True, source_saved=True)
        self.path.write_bytes(b"untrusted-local-image")
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery:
            self.assertEqual(self.client.resume_image(str(self.receipt_path)), str(self.path))
        self.assertEqual(self.path.read_bytes(), IMAGE)
        self.assert_bound()
        delivery.assert_called_once()
        post.assert_not_called()
        poll.assert_not_called()

    def test_invalid_digest_or_byte_count_cannot_authorize_cached_file(self):
        digest = hashlib.sha256(IMAGE).hexdigest()
        bindings = (
            {"delivery_sha256": "0" * 64, "delivery_size_bytes": len(IMAGE)},
            {"delivery_sha256": digest, "delivery_size_bytes": len(IMAGE) + 1},
            {"delivery_sha256": digest, "delivery_size_bytes": True},
            {"delivery_sha256": digest, "delivery_size_bytes": 0},
            {"delivery_sha256": digest, "delivery_size_bytes": -1},
            {"delivery_sha256": digest, "delivery_size_bytes": str(len(IMAGE))},
            {"delivery_sha256": "invalid", "delivery_size_bytes": len(IMAGE)},
            {"delivery_sha256": None, "delivery_size_bytes": len(IMAGE)},
        )
        for fields in bindings:
            with self.subTest(fields=fields):
                self.write_receipt(downloaded=True, **fields)
                self.path.write_bytes(IMAGE)
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch.object(self.client, "_download", side_effect=download) as delivery:
                    self.assertEqual(self.client.resume_image(str(self.receipt_path)), str(self.path))
                self.assert_bound()
                delivery.assert_called_once()
                post.assert_not_called()
                poll.assert_not_called()

    def test_missing_or_empty_cached_file_is_redelivered_without_repolling(self):
        for contents in (None, b""):
            with self.subTest(contents=contents):
                self.path.unlink(missing_ok=True)
                if contents is not None:
                    self.path.write_bytes(contents)
                self.write_receipt(downloaded=True, delivery_sha256=hashlib.sha256(IMAGE).hexdigest(),
                                   delivery_size_bytes=len(IMAGE))
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch.object(self.client, "_download", side_effect=download) as delivery:
                    self.assertEqual(self.generate(), str(self.path))
                self.assert_bound()
                delivery.assert_called_once()
                post.assert_not_called()
                poll.assert_not_called()

    def test_unreadable_cache_is_redelivered_before_binding(self):
        self.write_receipt(downloaded=True, delivery_sha256=hashlib.sha256(IMAGE).hexdigest(),
                           delivery_size_bytes=len(IMAGE))
        self.path.write_bytes(IMAGE)
        with patch.object(self.client, "_media_file_digest", side_effect=[OSError("synthetic read failure"),
                          (hashlib.sha256(IMAGE).hexdigest(), len(IMAGE))]), \
             patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download", side_effect=download) as delivery:
            self.assertEqual(self.client.resume_image(str(self.receipt_path)), str(self.path))
        self.assert_bound()
        delivery.assert_called_once()
        post.assert_not_called()
        poll.assert_not_called()

    def test_expired_completed_url_refreshes_only_the_original_task(self):
        for status in (403, 404, 410):
            with self.subTest(status=status):
                self.path.unlink(missing_ok=True)
                self.write_receipt()
                attempts = []

                def deliver(url, path):
                    attempts.append(url)
                    if len(attempts) == 1:
                        raise media_http_error(status)
                    self.assertEqual(self.receipt()["result"], REFRESHED_RESULT)
                    self.assertEqual(self.receipt()["previous_completed_result"], RESULT)
                    download(url, path)

                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch("models.tudou_client.requests.get", return_value=completed_response()) as query, \
                     patch.object(self.client, "_download", side_effect=deliver):
                    self.assertEqual(self.generate(), str(self.path))
                self.assertEqual(attempts, [RESULT["images"][0]["url"][0],
                                            REFRESHED_RESULT["images"][0]["url"][0]])
                query.assert_called_once()
                self.assertEqual(query.call_args.args[0], "https://relay.example/v1/tasks/accepted-image")
                post.assert_not_called()
                poll.assert_not_called()
                self.assert_bound()
                self.assertEqual(json.loads(self.source_path.read_text()), {
                    "url": REFRESHED_RESULT["images"][0]["url"][0], "expires_at": 2100000000,
                })
                self.assertIsNone(self.receipt()["error"])

    def test_invalid_refresh_never_replaces_original_completed_evidence(self):
        http_failure = completed_response()
        http_failure.raise_for_status.side_effect = requests.HTTPError("synthetic query HTTP 503")
        observations = (
            http_failure,
            response({"code": 503, "data": {"id": "accepted-image", "status": "completed",
                                            "result": REFRESHED_RESULT}}),
            response({"code": False, "data": {"id": "accepted-image", "status": "completed",
                                              "result": REFRESHED_RESULT}}),
            response([]),
            response({"code": 0, "data": ["not-a-task"]}),
            completed_response(id="different-image"),
            completed_response(id=None),
            completed_response(task_id="different-image"),
            completed_response(id=123),
            completed_response(status="processing"),
            completed_response(status="failed"),
            completed_response(result={"images": [{"url": ["file:///invalid.png"]}]}),
            completed_response(result={"images": [{"url": ["https:///invalid.png"]}]}),
            completed_response(result=None),
        )
        for index, observed in enumerate(observations):
            with self.subTest(case=index):
                self.write_receipt()
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch("models.tudou_client.requests.get", return_value=observed) as query, \
                     patch.object(self.client, "_download", side_effect=media_http_error(403)) as delivery:
                    with self.assertRaises(TudouTaskError) as caught:
                        self.client.resume_image(str(self.receipt_path))
                self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
                self.assertEqual(self.receipt()["status"], "completed")
                self.assertEqual(self.receipt()["result"], RESULT)
                self.assertNotIn("previous_completed_result", self.receipt())
                query.assert_called_once()
                delivery.assert_called_once()
                post.assert_not_called()
                poll.assert_not_called()

    def test_refreshed_download_failure_retains_evidence_for_delivery_only_retry(self):
        self.write_receipt()
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch("models.tudou_client.requests.get", return_value=completed_response()) as query, \
             patch.object(self.client, "_download", side_effect=[media_http_error(403), media_http_error(404)]) as delivery:
            with self.assertRaises(TudouTaskError) as caught:
                self.client.resume_image(str(self.receipt_path))
        self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
        query.assert_called_once()
        self.assertEqual(delivery.call_count, 2)
        post.assert_not_called()
        poll.assert_not_called()
        self.assertEqual(self.receipt()["result"], REFRESHED_RESULT)
        self.assertEqual(self.receipt()["previous_completed_result"], RESULT)
        with patch.object(self.client, "_submit") as new_post, \
             patch.object(self.client, "_poll") as new_poll, \
             patch("models.tudou_client.requests.get") as new_query, \
             patch.object(self.client, "_download", side_effect=download) as new_delivery:
            self.assertEqual(self.client.resume_image(str(self.receipt_path)), str(self.path))
        new_post.assert_not_called()
        new_poll.assert_not_called()
        new_query.assert_not_called()
        new_delivery.assert_called_once_with(REFRESHED_RESULT["images"][0]["url"][0], self.path)
        self.assert_bound()

    def test_non_expiry_delivery_errors_preserve_completion_without_query(self):
        errors = (OSError("synthetic disk failure"), requests.Timeout("synthetic media timeout"),
                  media_http_error(500),
                  TudouTaskError("synthetic callback failure", "accepted-image", "query_failed", "unknown"))
        for failure in errors:
            with self.subTest(error=type(failure).__name__):
                self.write_receipt()
                with patch.object(self.client, "_submit") as post, \
                     patch.object(self.client, "_poll") as poll, \
                     patch("models.tudou_client.requests.get") as query, \
                     patch.object(self.client, "_download", side_effect=failure):
                    with self.assertRaises(TudouTaskError) as caught:
                        self.client.resume_image(str(self.receipt_path))
                self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
                self.assertEqual(self.receipt()["status"], "completed")
                self.assertEqual(self.receipt()["result"], RESULT)
                query.assert_not_called()
                post.assert_not_called()
                poll.assert_not_called()

    def test_initial_completion_does_not_refresh_download_failure(self):
        with patch.object(self.client, "_submit", return_value="accepted-image") as post, \
             patch.object(self.client, "_poll", return_value=RESULT) as poll, \
             patch("models.tudou_client.requests.get") as query, \
             patch.object(self.client, "_download", side_effect=media_http_error(403)):
            with self.assertRaises(TudouTaskError) as caught:
                self.generate()
        self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
        self.assertEqual(self.receipt()["result"], RESULT)
        self.assertFalse(self.receipt()["downloaded"])
        query.assert_not_called()
        post.assert_called_once()
        poll.assert_called_once()

    def test_empty_delivery_cannot_be_bound_or_declared_downloaded(self):
        self.write_receipt()
        with patch.object(self.client, "_submit") as post, \
             patch.object(self.client, "_poll") as poll, \
             patch.object(self.client, "_download", side_effect=lambda _url, path: path.write_bytes(b"")):
            with self.assertRaises(TudouTaskError) as caught:
                self.client.resume_image(str(self.receipt_path))
        self.assertEqual((caught.exception.status, caught.exception.code), ("completed", "delivery_failed"))
        self.assertFalse(self.receipt()["downloaded"])
        self.assertNotIn("delivery_sha256", self.receipt())
        self.assertNotIn("delivery_size_bytes", self.receipt())
        self.assertFalse(self.source_path.exists())
        post.assert_not_called()
        poll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
