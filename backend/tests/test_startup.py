"""Check old and new Uvicorn entry points in fresh Python processes."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("folder,target", [
    ("", "api:app"),
    ("backend", "api:app"),
    ("", "backend.app.main:app"),
])
def test_uvicorn_entry_point(folder, target):
    root = Path(__file__).resolve().parents[2]
    code = (
        "from uvicorn.importer import import_from_string; "
        f"app = import_from_string({target!r}); "
        "from backend.app.main import app as canonical; "
        "assert app is canonical; "
        "assert app.title == 'CompanyMind AI API'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root / folder,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
