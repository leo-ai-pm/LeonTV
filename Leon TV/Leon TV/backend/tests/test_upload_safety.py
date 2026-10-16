import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from api.routers import files


@pytest.fixture
def upload_app(tmp_path, monkeypatch):
    config = SimpleNamespace(TEMP_DIR=str(tmp_path / "temp"), CODE_DIR=str(tmp_path / "code"))
    monkeypatch.setattr(files, "settings", config)
    app = FastAPI()
    app.include_router(files.router)
    return app, config


@pytest.mark.asyncio
async def test_fake_image_extension_is_rejected(upload_app):
    app, _ = upload_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/upload_media", files={"file": ("fake.png", b"not an image", "image/png")})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cache_cleanup_preserves_project_referenced_upload(upload_app):
    from pathlib import Path
    app, config = upload_app
    temp = Path(config.TEMP_DIR)
    temp.mkdir()
    referenced = temp / "reference.png"
    referenced.write_bytes(b"asset")
    unused = temp / "unused.txt"
    unused.write_text("cache")
    projects = Path(config.CODE_DIR) / "data" / "studio_projects"
    projects.mkdir(parents=True)
    (projects / "project.json").write_text(json.dumps({"nodes": [{"data": {"mediaPath": str(referenced)}}]}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/cache/temp")
    assert response.status_code == 200
    assert referenced.exists(), "清缓存不能删除画布持久引用的上传素材"
    assert not unused.exists()


@pytest.mark.asyncio
async def test_new_media_upload_survives_cache_cleanup_and_is_previewable(upload_app):
    from io import BytesIO
    from pathlib import Path

    from PIL import Image
    app, config = upload_app
    image = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post("/api/upload_media", files={"file": ("frame.png", image.getvalue(), "image/png")})
        assert upload.status_code == 200
        path = Path(upload.json()["file_path"])
        assert path.is_relative_to(Path(config.CODE_DIR))
        await client.delete("/api/cache/temp")
        assert path.exists()
