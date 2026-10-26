# CompanyMind AI

An employee knowledge assistant built with Python, LangChain components, local
MiniLM embeddings, persistent Chroma, Groq, FastAPI, and React.

## Current milestone: employee and HR document workspaces

- Employee document library with permitted previews and document-specific questions.
- Shared, individual employee, and HR-only document audiences enforced on the server.
- HR document search by audience, title, category or employee.
- Company overview, products, teams, IT, remote work and finance demo knowledge.
- Session-scoped follow-up questions with fresh permission checks every turn.
- Local employee sign-in with salted scrypt password hashes.
- Expiring opaque bearer sessions; only token hashes are stored in SQLite.
- Server-enforced access: employees see company documents and their own private
  documents; an explicitly assigned HR administrator can also see confidential HR-only material.
- The same access check protects search, chat, document listing, and CLI chat.
- Metadata filters apply before retrieval. A second check blocks unexpected
  unauthorized passages before generation or returning sources.
- A registered catalog supports multiple TXT files and text-based PDFs.
- Source references include document title, version, and real PDF page numbers.
- Document selection narrows the search while preserving the permission filter.
- The React UI supports sign-in, sign-out, document selection, and evidence.
- HR-managed file uploads with shared, employee or HR-only audience and owner selection.
- Staged uploads stay hidden until indexing and registration succeed.
- HR can remove a document from search and the live catalog.
- All included employment terms and employee names are fictional test data.

This is a production-oriented learning prototype, not an approved deployment for
real employee records. Chroma is local, but retrieved passages are sent to Groq.
Use fictional/redacted documents until the company approves data processing.

## First run after the upgrade

From PowerShell:

```powershell
cd E:\companymind-ai
.\venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
python -m backend.cli.manage setup-demo
```

The setup command asks you to choose passwords of 12–128 characters for `alice`,
`bob`, and `hr`. Password entry is intentionally hidden. It then indexes the 13
registered sample documents (six original documents and seven company-library documents). Existing account IDs keep their existing passwords.
Use different passwords for the three accounts. No default passwords are shipped.

Start the backend in that terminal (from E:\companymind-ai):

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

The original `python -m uvicorn api:app --reload` command also works from either
`E:\companymind-ai` or `E:\companymind-ai\backend`. Both `api.py` files are small
compatibility entry points; the actual application stays in `backend/app/main.py`.

In a second terminal:

```powershell
cd E:\companymind-ai\frontend
npm install
npm run dev
```

Open http://localhost:5173 and sign in. API docs: http://127.0.0.1:8000/docs.
For Swagger, call `/auth/login`, copy the access token, and paste it into the
Authorize dialog. Sessions last one hour; refresh requires signing in again
because browser credentials are only held in memory.

The existing `GROQ_API_KEY` stays in `.env`. Groq's HTTP client uses the Windows
system certificate store through truststore, with certificate checking enabled.
If port 8000 is occupied, check whether CompanyMind is already running before
starting a second copy. The development frontend proxy targets port 8000.

The local Chroma collection browser is an administrator diagnostic tool and can
read all indexed sample records. Employee permissions are enforced at the API
boundary; employees must not have direct Chroma server or database-file access.
The existing Open Chroma launcher now targets `company_documents_v2` after setup.

## Company library and follow-up conversations

For an existing installation, add only the new fictional shared library:

```powershell
cd E:\companymind-ai
python -m backend.cli.manage add-company-demo
```

The command preserves existing accounts and registered documents, including
private uploads. Rerunning it skips complete matching entries. It does not reset
the database or silently replace conflicting documents. Fresh setup-demo installs
all 13 seed documents; existing catalogs are expanded only by the explicit command.

Sign in, select All permitted documents, and try:

- What does TechNova do, and where is its headquarters?
- What does TechDesk Growth cost?
- What is the remote working policy?
- Follow up: Does that apply during probation?
- Who handles expense reimbursements?

Each successful /chat response now includes a conversation_id. Pass it with the
next /chat question to continue the topic. The server remembers up to four recent
resolved user questions for that login session and exact document selection.
Previous answers and source passages are not reused as factual evidence. A
follow-up is rewritten into a standalone question, then searched and answered
from currently permitted passages. A follow-up uses one additional LLM request;
the configured provider's limits still apply.

New conversation or changing Search in starts a new topic. Recent sidebar entries
can resume their own conversation until it expires. Resuming an older answer
continues that conversation's latest topic rather than branching from that turn.
DELETE /conversations/{conversation_id} discards an owned server conversation.
Logout clears server memory for that session and the browser's question history.

Topic memory expires after 30 minutes of inactivity and clears on backend restart.
It is bounded to eight conversations per session and 256 in the server process;
older idle topics may be evicted. This development implementation needs a shared
conversation store before multiple API workers are used. It does not remember
specific wording from earlier assistant answers. Ask a complete question if a
reference is unclear. See docs/COMPANY_CHAT.md for an explanation and walkthrough.

## Try the permission boundary

1. As `alice`, select Alice's offer letter and ask the notice-period question.
2. Search all permitted documents and compare the offer and general policy.
3. Ask for Bob's salary. Bob's offer must not appear in Alice's document list,
   retrieved sources, or the context sent to the LLM.
4. Sign out, then sign in as `bob`. Alice's offer and training agreement must not
   be available. The old question history should be gone.
5. Sign in as `hr` to inspect the sample documents assigned to that role.

Asking the LLM to hide a document is not authorization. The API derives identity
from a validated session and constructs the Chroma filter itself. A client cannot
choose a different employee ID or elevate its role in a question or request body.

## Document administration

`data/catalog.json` is the fictional seed registry. On first use it initializes
`runtime/catalog.json`, the live registry used by HR uploads and indexing. The
`runtime/` directory is ignored by Git so live titles and owner IDs stay out of
the sample repository. Subsequent restarts do not reseed an existing live registry.
The live registry is trusted operator-maintained configuration. Each entry defines
an ID, path relative to `data/`, title, category, version, and visibility.
Employee documents also need an owner ID from the identity database.

```json
{
  "document_id": "alice-offer",
  "path": "demo/alice_offer.txt",
  "title": "Alice's offer letter (fictional)",
  "category": "offer_letter",
  "version": "1.0",
  "visibility": "employee",
  "owner_id": "EMP001"
}
```

Create an additional account with a password prompt:

```powershell
python -m backend.cli.manage create-user employee3 --id EMP003
```

Only trusted local operators can assign the `--role hr_admin` option. There is no
public registration or role-change endpoint. Demo IDs are EMP001 (alice), EMP002
(bob), and HR001 (hr). They are not supplied by the frontend during retrieval.

After editing a registered document or its metadata:

```powershell
python -m backend.cli.manage index
```

Reindexing replaces chunks for each registered document, removing obsolete tail
chunks. Replacement is synchronous and non-atomic: a failure may leave that
document unavailable or partially indexed. For production, use staged immutable
index versions and an atomic activation pointer. Unchanged documents are currently
re-embedded; content-hash-based incremental indexing is a future improvement.

Explicitly remove a retired document from the index:

```powershell
python -m backend.cli.manage delete-document alice-bond
```

The command removes the live catalog entry and indexed chunks together. The
original source file is retained locally for recovery. The HR screen provides
the same removal operation with a confirmation step. Manually removing a JSON
entry alone still does not remove indexed records. For real employee files,
use the ignored `data/private/` directory, subject to an approved data policy.
Never mark an offer letter as `company` just to make a query work.

The upgraded API reads `company_documents_v2`, excluding unclassified records in
the original demonstration collection. The original `company_documents` data is
preserved. It is not automatically migrated into an employee-accessible collection.

PDF support uses text extraction, not OCR. A page with no extracted text causes a
clear error rather than silently omitting part of a contract. Encrypted PDFs and
files larger than 20 MiB are rejected. Tables and complex layouts still need
extraction-quality review. TXT documents have no invented page numbers.

## API

| Route | Purpose | Sign-in |
|---|---|---|
| GET /health | Basic process health; not provider readiness | No |
| POST /auth/login | Exchange username/password for a session | No |
| GET /auth/me | Current server-resolved identity | Yes |
| POST /auth/logout | Revoke the session | Yes |
| GET /documents | List permitted indexed documents | Yes |
| POST /search | Retrieve permitted passages | Yes |
| POST /chat | Retrieve permitted passages and generate | Yes |
| GET /admin/employees | List active accounts for owner selection | HR only |
| GET /admin/documents | Inspect registered documents and index status | HR only |
| POST /admin/documents | Upload and index a file | HR only |
| DELETE /admin/documents/{id} | Remove from catalog and employee search | HR only |

The search/chat body accepts `question`, `top_k` (1–10), and an optional
`document_id`. A document ID narrows access; it never overrides the account's
permissions. Unknown body fields such as `employee_id` are rejected.

`backend/cli/chat.py` now acts as an authenticated terminal API client. Run the backend first.
All local administration commands assume trusted filesystem access and should not
be exposed through a public server. Development Vite proxies are not a production
reverse proxy; deployment requires explicit routing and HTTPS.

## Checks

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m pytest -q
cd frontend
npm run lint
npm run build
```

Tests use temporary identity databases and a real temporary Chroma collection
with deterministic test embeddings. They do not send documents to Groq or measure
real model answer quality. Coverage includes cross-employee isolation, document
scope bypass attempts, denied unclassified records, token expiry/revocation,
input validation, PDF page metadata, and document replacement.

## Next milestones

1. HR document approval, policy effective dates, durable ingestion jobs, OCR,
   staged document versions, and document access grants finer than two roles.
2. A labeled retrieval/answer evaluation set; measure recall, groundedness,
   citation support, abstention, cost, and latency before tuning retrieval.
3. Hybrid retrieval and reranking, conflict handling, and verified structured
   fields for questions needing calculations or live HR-system data.
4. A permission-controlled HR escalation workflow with confirmation and an audit
   trail for actions that change external systems.
5. Company SSO/OIDC, production database and storage, encryption/retention policy,
   request quotas, audit events without document text, monitoring, and backups.
6. CI, reproducible packaging, staging deployment, load tests, and operational
   readiness review. Do not claim production security or scale from local tests.

See [the learning guide](docs/LEARNING.md) for explanations and exercises, and
[the folder guide](docs/PROJECT_STRUCTURE.md) for the code layout.


## HR upload exercise

Restart the backend after updating code/dependencies, refresh the browser, sign
in as `hr`, and open **Manage documents** in the sidebar. Existing account
passwords and existing document records are preserved.

1. Choose `examples/remote_work_policy.txt`, title it "Remote working policy",
   select **All employees**, then click **Upload and index document**.
2. Return to questions, select that document, and ask how many remote working
   days employees may request. The fictional policy says up to two per week.
3. Upload `examples/alice_equipment_agreement.txt` as **One employee + HR**,
   selecting `alice · EMP001`. Alice should see it; Bob should not.
4. Try removing the newly uploaded example. Confirming removes it from the live
   registry and employee search; cancelling keeps it unchanged.

The file request uses multipart/form-data. The `file` part contains bytes and
the `metadata` part contains JSON with title, category, version, visibility, and
optional owner_id. The browser sets the multipart boundary automatically.

Only TXT/text-based PDF files up to 20 MiB are accepted, with at most 1,000
chunks per synchronous upload. The request body is also bounded when Content-Length
is missing. File storage uses a server-generated ID under the ignored data/uploads
directory; the original filename is display metadata. UTF-8, readable PDF pages,
active owners and duplicate content/title/version/audience are checked.

Chunks initially carry upload_status=staging. Both retrieval filters and the
defensive metadata check reject staging records. After registration, a native
Chroma metadata update publishes them without re-embedding. This uses the pinned
LangChain adapter's _collection boundary, covered by real-Chroma integration tests.

Catalog changes use a cross-process file lock and atomic file replacement. CLI
indexing shares that lock. Chroma and the filesystem are still separate stores:
rollback is best-effort, and a crash can leave staging records or recovery files
needing reconciliation. Removal hides records before cleanup; a cleanup failure
is reported as pending and logged by document ID, without document text.

Uploads run synchronously in the backend worker thread. The UI reports progress
and warns that a timed-out request may still complete. Durable jobs, quotas,
malware scanning, resource-isolated extraction, atomic version activation, and
automatic source-file retention/purging remain production work. The route buffers
a bounded request in memory; production should enforce edge limits as well.

A new version label creates a separate document. It does not automatically retire
the previous version. Verify the replacement and remove the obsolete document.
The browser integration checks use isolated accounts, real HTTP/auth/Chroma/file
uploads and deterministic test embeddings/generation; no live employee files or
Groq calls are used during those checks.


## Project layout

```text
companymind-ai/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI application
│   │   ├── core/             # Authentication, permissions, shared paths
│   │   ├── rag/              # Read, chunk, embed, retrieve, generate
│   │   └── documents/        # HR upload and document-management API
│   ├── cli/                  # Administration commands and terminal chat
│   ├── tests/                # Backend and permission tests
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/                 # React and Vite
├── docs/                     # Learning notes and project layout
├── examples/                 # Fictional files for upload exercises
├── data/                     # Source documents and fictional seed catalog
├── runtime/                  # Local identities and live catalog (ignored)
├── chroma_db/                # Persistent vectors (ignored)
├── venv/                     # Local Python environment (ignored)
├── .env                      # Local secrets (ignored)
├── pytest.ini
└── README.md
```

Run Python commands from the repository root. Package modules are started with
`python -m ...`; the dotted path corresponds to the folders containing the module.
The old root-level Python entry points have moved. The API URLs and frontend
proxy have not changed. Existing identities, documents, catalog, and vector data
stay at the same storage paths; no reindex or account setup is required for this move.



## Employee and HR document workspaces

Employees open My documents to browse shared policies and their assigned private
documents, preview indexed passages, and start a question about one document.
HR opens Document library for all classified documents, and Manage documents to
upload for Everyone, One employee + HR, or HR only. Employee accounts cannot
manage documents or access another employee's private or HR-only evidence.

See docs/ACCESS_WORKSPACES.md for the access matrix, code walkthrough and manual
Alice/Bob/HR checks. examples/hr_only_review_guide.txt is an optional fictional
file to upload with the HR-only audience. Existing records keep their audiences.

The frontend defaults to a backend on port 8000. If another application uses that
port, run Uvicorn on port 8001 and set COMPANYMIND_API_TARGET=http://127.0.0.1:8001
in the frontend terminal before starting Vite. The access guide has exact commands.
