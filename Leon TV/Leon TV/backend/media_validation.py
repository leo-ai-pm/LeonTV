"""Validate uploaded bytes rather than trusting client filenames or MIME headers."""

import subprocess
import zipfile
from pathlib import Path

from PIL import Image

from media_tools import ffmpeg_executable

IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP"}


def validate_uploaded_file(path: str, extension: str) -> None:
    source = Path(path)
    if not source.stat().st_size:
        raise ValueError("上传文件不能为空")
    try:
        if extension in IMAGE_FORMATS:
            with Image.open(source) as image:
                if image.format != IMAGE_FORMATS[extension]:
                    raise ValueError("图片内容与扩展名不一致")
                image.verify()
        elif extension in {".docx", ".doc"}:
            with zipfile.ZipFile(source) as document:
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(document.namelist()):
                    raise ValueError("不是有效的 Word 文档；请使用 .docx")
        elif extension == ".pdf":
            with source.open("rb") as file:
                if not file.read(1024).lstrip().startswith(b"%PDF-"):
                    raise ValueError("不是有效的 PDF 文件")
        elif extension in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            with source.open("rb") as file:
                header = file.read(16)
            signatures = (b"ftyp", b"moov", b"mdat", b"wide", b"free")
            container_ok = (header[4:8] in signatures or header.startswith(b"\x1a\x45\xdf\xa3")
                            or header.startswith(b"RIFF") and header[8:12] == b"AVI ")
            if not container_ok:
                raise ValueError("不是支持的视频容器")
            result = subprocess.run(
                [ffmpeg_executable(), "-v", "error", "-i", path, "-map", "0:v:0",
                 "-frames:v", "1", "-f", "null", "-"],
                capture_output=True, timeout=15,
            )
            if result.returncode:
                raise ValueError("视频中没有可读取的视频画面")
        elif extension in {".txt", ".md"}:
            raw = source.read_bytes()
            for encoding in ("utf-8-sig", "gbk", "utf-16"):
                try:
                    raw.decode(encoding)
                    return
                except UnicodeError:
                    continue
            raise ValueError("文本编码不受支持，请使用 UTF-8")
    except (OSError, SyntaxError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        raise ValueError("文件内容无法解析或与扩展名不一致") from exc
