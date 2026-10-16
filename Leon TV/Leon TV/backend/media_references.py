"""Conservative deletion guards over the existing file-based metadata stores."""

import json
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


class MediaReferenceError(RuntimeError):
    """Deletion cannot safely proceed; callers should return a conflict."""


def local_media_paths(value: str, code_dir: str | Path, temp_dir: str | Path) -> set[Path]:
    """Normalize filesystem and /code URL paths without reading or fetching media."""
    if not value or len(value) > 4096 or "\0" in value:
        return set()
    code, temp = Path(code_dir).resolve(), Path(temp_dir).resolve()
    try:
        parsed = urlsplit(value)
        if parsed.scheme:
            if parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1"}:
                value = unquote(parsed.path)
                if not value.startswith("/code/"):
                    return set()
            elif parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
                value = unquote(parsed.path)
            else:
                return set()
        if value.startswith(("/code/", "code/")):
            relative = unquote(value.split("code/", 1)[1])
            candidates = [code / relative]
        elif Path(value).is_absolute():
            candidates = [Path(value)]
        else:
            candidates = [code / value, temp / value, code.parent / value]
        return {resolved for candidate in candidates
                if (resolved := candidate.resolve()).is_relative_to(code)
                or resolved.is_relative_to(temp)}
    except (OSError, ValueError, RuntimeError):
        return set()


class MediaReferences:
    def __init__(self, paths: set[Path]):
        self.paths = paths

    def contains(self, target: str | Path) -> bool:
        target = Path(target).resolve()
        return any(path == target or path.is_relative_to(target) for path in self.paths)

    def require_unreferenced(self, *targets: str | Path) -> None:
        if any(self.contains(target) for target in targets):
            raise MediaReferenceError("素材仍被其他项目、会话或任务引用，未删除；请先解除引用。")


def collect_media_references(
    code_dir: str | Path,
    temp_dir: str | Path,
    *,
    metadata_roots: Iterable[str | Path] = (),
    excluded_files: Iterable[str | Path] = (),
    documents: Iterable[Any] = (),
) -> MediaReferences:
    """Scan metadata before mutation; unreadable indexes block the entire deletion."""
    excluded = {Path(path).resolve() for path in excluded_files}
    visited: set[Path] = set()
    paths: set[Path] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str):
            paths.update(local_media_paths(value, code_dir, temp_dir))

    def read_index(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited or resolved in excluded:
            return
        if path.is_symlink():
            raise OSError("Metadata aliases cannot be checked safely")
        visited.add(resolved)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, (dict, list)):
            raise ValueError("Metadata must be an object or array")
        collect(data)

    def walk_error(error: OSError) -> None:
        raise error

    roots = [Path(code_dir) / "data", Path(code_dir) / "result" / "sandbox",
             *(Path(path) for path in metadata_roots)]
    try:
        for root in roots:
            if root.is_symlink():
                raise OSError("Metadata root aliases cannot be checked safely")
            if not root.exists():
                continue
            if root.is_file():
                read_index(root)
                continue
            for directory, folders, files in os.walk(root, onerror=walk_error, followlinks=False):
                if any((Path(directory) / folder).is_symlink() for folder in folders):
                    raise OSError("Metadata directory aliases cannot be checked safely")
                for name in files:
                    if name.endswith(".json"):
                        read_index(Path(directory) / name)
        for document in documents:
            collect(document)
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        raise MediaReferenceError("项目或任务索引无法安全读取，未删除任何素材；请先检查索引。") from exc
    return MediaReferences(paths)
