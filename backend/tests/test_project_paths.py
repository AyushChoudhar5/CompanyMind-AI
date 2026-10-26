"""Guard the state locations during package moves; never read secrets or records."""
from pathlib import Path

from backend.app.core import auth, paths
from backend.app.rag import generation, ingest, vector_store


def test_persistent_paths_remain_at_project_root_from_another_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected_root = Path(__file__).resolve().parents[2]
    assert paths.PROJECT_ROOT == expected_root
    assert auth.DEFAULT_DB == expected_root / "runtime" / "identity.sqlite3"
    assert ingest.CATALOG_PATH == expected_root / "runtime" / "catalog.json"
    assert ingest.DATA_DIR == expected_root / "data"
    assert vector_store.DATABASE_DIR == expected_root / "chroma_db"
    assert generation.ENV_FILE == expected_root / ".env"
