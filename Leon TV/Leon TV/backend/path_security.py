import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

ALLOWED_REMOTE_SCHEMES = {"http", "https"}
STORAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_storage_id(value: str) -> str:
    """Keep legacy alphanumeric IDs while excluding path components and separators."""
    if not isinstance(value, str) or not STORAGE_ID_RE.fullmatch(value):
        raise ValueError("Invalid resource ID")
    return value


def contained_storage_path(root: str | Path, *parts: str) -> Path:
    """Resolve paths before writes/deletes, including existing symlink parents."""
    base = Path(root).resolve()
    candidate = base.joinpath(*parts).resolve()
    if candidate == base or not candidate.is_relative_to(base):
        raise ValueError("Storage path is outside the allowed directory")
    return candidate


def resolve_allowed_local_file(value: str, allowed_roots: Iterable[str]) -> str:
    """Resolve a client-provided path and require it to stay under an allowed root."""
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("文件路径不能为空")

    raw_path = Path(raw_value)
    parsed = urlparse(raw_value)
    if parsed.scheme and not raw_path.is_absolute():
        raise ValueError("此处只接受已上传的本地文件")

    roots = [Path(root).resolve() for root in allowed_roots]
    candidates = [raw_path] if raw_path.is_absolute() else [root / raw_path for root in roots]

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not resolved.is_file():
            continue
        if any(resolved.is_relative_to(root) for root in roots):
            return str(resolved)

    raise ValueError("文件不存在或不在允许的上传目录中")


def resolve_media_reference(
    value: str,
    allowed_roots: Iterable[str],
    *,
    allow_remote_urls: bool = True,
) -> str:
    """Accept an HTTP(S) URL or a local file contained by an allowed root."""
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("媒体引用不能为空")

    raw_path = Path(raw_value)
    parsed = urlparse(raw_value)
    if parsed.scheme and not raw_path.is_absolute():
        if allow_remote_urls and parsed.scheme.lower() in ALLOWED_REMOTE_SCHEMES and parsed.netloc:
            return raw_value
        raise ValueError("只允许 HTTP(S) URL 或已上传的本地文件")

    return resolve_allowed_local_file(raw_value, allowed_roots)
