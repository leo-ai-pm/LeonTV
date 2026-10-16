"""Exercise the relay's documented async contract without spending credits."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from models.tudou_client import TudouClient


def response(payload):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.return_value = None
    return value


class TudouClientTests(unittest.TestCase):
    def test_ark_seedance_id_keeps_its_existing_provider_route(self):
        from models.video_client import VideoClient
        with patch("models.video_client.TudouClient") as relay, \
             patch.object(VideoClient, "_generate_seedance", return_value="ark-video") as ark:
            self.assertEqual(VideoClient().generate_video(
                "rain", "https://media.example/frame.png", "/tmp/ark.mp4",
                model="doubao-seedance-2-0-260128", duration=5,
            ), "ark-video")
            ark.assert_called_once()
            relay.assert_not_called()

    def test_failed_seedance_task_is_not_submitted_again(self):
        client = TudouClient(api_key="test-key")
        with tempfile.TemporaryDirectory() as directory, \
             patch("models.tudou_client.requests.post", return_value=response({"data":{"id":"seedance-task"}})) as submit, \
             patch("models.tudou_client.requests.get", return_value=response({"data":{"status":"failed","error":{"message":"upstream failed"}}})), \
             patch.object(client, "_download") as download:
            with self.assertRaisesRegex(RuntimeError, "upstream failed"):
                client.generate_video("rain", "https://media.example/frame.png", str(Path(directory) / "out.mp4"),
                                      model="seedance-2.0", duration=5, ratio="16:9", resolution="720p")
            submit.assert_called_once()
            body = submit.call_args.kwargs["json"]
            self.assertEqual(submit.call_args.args[0], "https://api.ai-tudou.net/v1/videos/generations")
            self.assertEqual((body["model"], body["duration"], body["aspect_ratio"], body["resolution"]),
                             ("seedance-2.0", 5, "16:9", "720p"))
            self.assertEqual(body["images"], ["https://media.example/frame.png"])
            self.assertNotIn("extra", body, "Seedance最小请求不应沿用Wan的extra扩展字段")
            download.assert_not_called()

    def test_seedance_standard_routes_to_tudou_without_using_ark(self):
        from models.config_model import get_model_config
        from models.video_client import VideoClient

        self.assertEqual(get_model_config("seedance-2.0")["provider"], "tudou")
        with patch("models.video_client.TudouClient") as relay, \
             patch.object(VideoClient, "_generate_seedance") as ark:
            relay.return_value.generate_video.return_value = "https://media.example/clip.mp4"
            result = VideoClient().generate_video(
                "雨夜进店", "https://media.example/frame.png", "/tmp/clip.mp4",
                model="seedance-2.0", duration=5, video_ratio="16:9", resolution="720P",
            )
            self.assertEqual(result, "https://media.example/clip.mp4")
            ark.assert_not_called()
            relay.return_value.generate_video.assert_called_once_with(
                prompt="雨夜进店", image_path="https://media.example/frame.png",
                save_path="/tmp/clip.mp4", model="seedance-2.0", duration=5,
                ratio="16:9", resolution="720p",
            )

    def test_live_wan_video_result_uses_generic_images_array(self):
        result = {"images": [{"url": ["https://videos.example/generated/clip.mp4?expires=123"]}]}
        self.assertEqual(TudouClient._first_url(result, "video"),
                         "https://videos.example/generated/clip.mp4?expires=123")
        with self.assertRaisesRegex(RuntimeError, "图片链接"):
            TudouClient._first_url({"images": [{"url": ["https://media.example/frame.png"]}]}, "video")

    def test_image_result_is_saved_and_public_url_feeds_video(self):
        with tempfile.TemporaryDirectory() as directory:
            submitted = []

            def submit(url, **kwargs):
                submitted.append((url, kwargs["json"]))
                return response({"data": {"id": f"task-{len(submitted)}"}})

            results = [
                {"data": {"status": "completed", "result": {"images": [{
                    "url": ["https://media.example/reference.png"], "expires_at": time.time() + 3600,
                }]}}},
                {"data": {"status": "completed", "result": {
                    "video_url": "https://media.example/clip.mp4",
                }}},
            ]

            def download(url, path):
                Path(path).write_bytes(b"test-media-bytes")

            client = TudouClient(api_key="test-key")
            with patch("models.tudou_client.requests.post", side_effect=submit), \
                 patch("models.tudou_client.requests.get", side_effect=[response(item) for item in results]), \
                 patch.object(client, "_download", side_effect=download):
                image_path = client.generate_image("海边邮差", None, "16:9", directory)
                video_path = str(Path(directory) / "clip.mp4")
                video_url = client.generate_video("邮差打开信", image_path, video_path, duration=5)

            self.assertEqual(submitted[0][0], "https://api.ai-tudou.net/v1/images/generations/async")
            self.assertEqual(submitted[0][1]["model"], "gpt-image-2-all")
            self.assertEqual(submitted[1][0], "https://api.ai-tudou.net/v1/videos/generations")
            self.assertEqual(submitted[1][1]["images"], ["https://media.example/reference.png"])
            self.assertEqual(submitted[1][1]["duration"], 5)
            self.assertEqual(submitted[1][1]["extra"], {"aspect_ratio": "16:9", "resolution": "720p"})
            self.assertEqual(video_url, "https://media.example/clip.mp4")
            self.assertTrue(Path(video_path).is_file())
            self.assertEqual(json.loads(Path(f"{image_path}.tudou-source.json").read_text())["url"],
                             "https://media.example/reference.png")

    def test_local_image_without_public_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "private.png"
            image.write_bytes(b"png")
            with self.assertRaisesRegex(ValueError, "需要公网参考图"):
                TudouClient(api_key="test-key").generate_video("test", str(image),
                                                               str(Path(directory) / "out.mp4"))


if __name__ == "__main__":
    unittest.main()
