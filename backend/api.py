"""Allow `uvicorn api:app` when launched from the backend folder."""
from pathlib import Path
import sys

# Python needs the project root to find the backend package from this folder.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from backend.app.main import app
