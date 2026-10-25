"""Check the actual distributed package, rather than only synthetic closures."""
import hashlib
from pathlib import Path

from core.execution.control import CODE_CLOSURE_DIRS, REQUIRED_CODE_FILES, _code_closure


def test_public_distribution_contains_the_required_code_authority_package_files():
    backend = Path(__file__).resolve().parents[1]
    names = set(REQUIRED_CODE_FILES)
    for directory in CODE_CLOSURE_DIRS:
        names.update(path.relative_to(backend).as_posix() for path in (backend / directory).rglob("*.py"))
    missing = sorted(name for name in names if not (backend / name).is_file())
    assert not missing, missing
    manifest = {name: hashlib.sha256((backend / name).read_bytes()).hexdigest() for name in names}
    _code_closure(backend, manifest)
