# Finding your way around CompanyMind

All paths below are relative to the repository root, E:\companymind-ai on your
computer. Folders group related responsibilities; the application behavior is the
same after this reorganization.

## Backend application

| Location | Purpose |
|---|---|
| backend/app/main.py | Creates FastAPI, loads shared resources, declares chat/auth routes |
| backend/app/core/auth.py | Password hashes, SQLite identities, sessions, current-user dependency |
| backend/app/core/access.py | Employee identity and document permission filters |
| backend/app/core/conversations.py | Bounded session-scoped topic memory |
| backend/app/core/paths.py | Anchors secrets and storage paths to the repository root |
| backend/app/rag/ingest.py | Validates catalogs and reads TXT/PDF documents |
| backend/app/rag/chunking.py | Splits text into searchable passages with metadata |
| backend/app/rag/embeddings.py | Loads the local MiniLM embedding model |
| backend/app/rag/vector_store.py | Opens Chroma, indexes chunks, lists permitted documents |
| backend/app/rag/retrieval.py | Searches with permissions and checks returned passages |
| backend/app/rag/generation.py | Formats evidence and calls Groq to generate answers |
| backend/app/documents/library.py | Permission-checked indexed document previews |
| backend/app/documents/admin.py | HR upload routes, file validation, catalog publication and removal |

## Commands, tests, and frontend

- backend/cli/manage.py contains local administration commands.
- backend/cli/library.py adds the shared fictional company library safely.
- data/demo/company/ contains the seven fictional company-library documents.
- docs/COMPANY_CHAT.md explains conversational retrieval and how to try it.
- backend/cli/chat.py is the authenticated terminal chat client.
- backend/tests contains API, access, PDF, upload, and storage-path tests.
- backend/requirements.txt lists the backend packages; requirements-dev.txt adds pytest.
- frontend/src contains the React application. frontend/package.json owns npm commands.
- docs/LEARNING.md explains the pipeline and provides exercises.
- examples contains fictional upload files for learning.

## Data is separate from code

data/ contains source files. runtime/ contains identities, sessions, and the live
catalog. chroma_db/ contains the persistent vector database. These paths were not
moved. The shared paths module prevents a module relocation or a different working
directory from accidentally selecting a fresh database. .env remains at the root
and must never be committed.

## How Python finds the moved code

A folder containing __init__.py is an explicit Python package. An import such as:

```python
from backend.app.rag.retrieval import retrieve
```

means: find the backend package, its app package, its rag package, then import
retrieve from retrieval.py. Imports now use the full package path.

For the backend command:

```powershell
python -m uvicorn backend.app.main:app --reload
```

backend.app.main identifies backend/app/main.py; :app selects its FastAPI object.
The -m option runs an importable Python module. Run these commands from the root,
with the existing virtual environment activated.

## Everyday commands

```powershell
# Backend
python -m uvicorn backend.app.main:app --reload

# Reindex the registered documents
python -m backend.cli.manage index

# Create an employee; the password is entered privately at the prompt
python -m backend.cli.manage create-user employee3 --id EMP003

# Terminal chat (start the backend first)
python -m backend.cli.chat

# Backend tests, discovered through pytest.ini
python -m pytest -q

# Optional inspection exercises
python -m backend.app.rag.ingest
python -m backend.app.rag.chunking
```

In another terminal:

```powershell
cd frontend
npm run dev
```

Install dependencies from the repository root when setting up another machine:

```powershell
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/requirements-dev.txt
```

Compatibility entry points at api.py and backend/api.py keep the original
python -m uvicorn api:app --reload command working from either folder.

The old root-level Python scripts were archived outside the project during this
move. The empty accidental npm lockfile at the root was archived too; the actual
frontend lockfile remains in frontend/. Generated caches belong to the runtime
or package folders and are ignored by Git.


The document library UI is frontend/src/DocumentLibrary.jsx. docs/ACCESS_WORKSPACES.md explains the access matrix.

## Policy revisions

- backend/app/core/publication.py: effective dates and current-version checks.
- backend/app/documents/revisions.py: HR draft/review/publish workflow and background jobs.
- backend/app/rag/impact.py: paired answer comparison and citation validation.
- frontend/src/PolicyRevisions.jsx: HR review screen.
- runtime/revision_reviews/: private saved reports; never commit them.
- examples/policy_revision/: fictional old/new policies and test questions.
- docs/POLICY_REVISIONS.md: walkthrough and implementation explanation.
