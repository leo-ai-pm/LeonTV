"""Default runnable native acceptance; public runner creates its own authority."""
import subprocess
import sys
from pathlib import Path


def test_public_formal_persistence_runner(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(backend / "tests/run_production_image_persistence.py"),
                             "--output", str(tmp_path / "isolated")], cwd=backend, capture_output=True, text=True, timeout=1250)
    assert result.returncode == 0, result.stdout + result.stderr
