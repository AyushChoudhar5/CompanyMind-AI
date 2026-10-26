"""Storage locations anchored to the repository, independent of working directory.

Moving Python modules must never create fresh identity or vector databases.
Keep all persistent data and the existing .env at their original project paths.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
ENV_FILE = PROJECT_ROOT / ".env"
