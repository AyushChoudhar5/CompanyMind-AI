# Policy revision reviews

CompanyMind can help HR answer: **If we replace this policy, which of these employee answers would change?** It compares a chosen set of questions before a replacement becomes searchable.

## Try it in the web app

1. Sign in with your existing **hr** account. Open **Manage documents**.
2. On a current document, select **Review policy revision**.
3. Enter a new version label and upload its replacement TXT or text-based PDF. Select **Save draft revision**. The audience and employee owner are inherited; a private offer letter cannot become shared through this workflow.
4. Enter 1–8 questions, one per line. Select **Run impact review**. The server runs the review even if you leave the page; return using **Review draft**.
5. Inspect the current answer, proposed answer, references, and supporting passages. Results are **Changed**, **Unchanged**, or **Needs review**. You can also expand the indexed-text comparison.
6. Review the actual document and resolve or accept any gaps. Tick the approval checkbox. Choose **Publish now** or **Schedule an effective date**, then approve.

The previous version stops supplying current answers when the replacement takes effect. Scheduled dates are entered in your browser's local time and sent as UTC timestamps. Both versions keep their review history for HR. This workflow records approval of chatbot content; it does not send employee notices or complete formal company approval procedures.

## A safe fictional example

Use files in `examples/policy_revision/`. These are sample files, not automatically installed policies.

1. Upload `remote_work_v1.txt` using **Add a document**, title **Revision demo: remote work**, version **1.0**, audience **All employees**.
2. Open that document's **Review policy revision** action and upload `remote_work_v2.txt` as **2.0**.
3. Paste the questions from `questions.txt`.

Expected examples (AI wording may differ):

| Question | Current version | Proposed version | Expected result |
|---|---|---|---|
| How many remote days are allowed each week? | Two | One | Changed |
| Who approves remote work? | Reporting manager | Reporting manager | Unchanged |
| Is remote work available during probation? | Available only after probation | Not stated | Needs review |

Run this under a separate demo title to avoid replacing an unrelated real policy.

## What the code does and why

**A draft** is an indexed document that is not yet approved for employee answers. Chroma stores its passages, but the normal retrieval filter excludes it. Storing the draft lets HR compare evidence without changing employee search.

**A version** has its own document ID, a version label and a link to the version it replaces. Version labels describe the document; IDs identify it reliably. Older content remains available through the HR review screen.

**An impact review** retrieves up to five passages from each version for each question. One request to the configured Groq model asks for two independently grounded answers and a classification. Source labels are checked; missing citations force Needs review. Eight questions can use eight model requests, subject to the account's existing quota.

**Publication** stores an effective interval: start time <= now < end time. The original ends exactly when the replacement begins. Search, chat and document previews use the same date rules, so a scheduled switch does not need a timer process. An already-open page or old chat answer can still display an earlier result; new requests use the current version.

**A signature** is a hash of the catalog metadata and indexed passages. The review saves both signatures. Publication refuses a stale report when either version changed after review. A new review must then run against the current evidence.

| File | Responsibility |
|---|---|
| `backend/app/documents/revisions.py` | Protected HR routes, background review, saved reports, approval and publication |
| `backend/app/rag/impact.py` | Prompt, paired answers, structured result and citation validation |
| `backend/app/core/publication.py` | Date/status rules shared with permission checks |
| `backend/app/core/access.py` | Combines publication rules with employee permissions |
| `frontend/src/PolicyRevisions.jsx` | Upload, review report, evidence and approval screen |
| `backend/tests/test_policy_revisions.py` | Access, drafts, cutover, stale reports, failures and citation checks |

## Scope and operational limits

- Results cover only the selected questions and retrieved passages. They do not prove that every affected question was found or that every answer is correct. The reviewer should inspect the citations and full documents.
- The comparison isolates the two policy versions. It does not automatically discover all historical employee questions, compare every offer letter, decide contract precedence, or send notifications.
- This implementation runs one review at a time in one backend process. Do not run multiple Uvicorn workers for this workflow. A server restart interrupts a running review; reopen it and run again. Completed reports persist under `runtime/revision_reviews/`.
- Catalog JSON and Chroma are separate stores. Publication stages records, saves the catalog, then exposes the date-gated versions. Recoverable errors restore the prior state; storage failure or process termination can require operator reconciliation. This is not a distributed transaction system. Back up the catalog, source files and Chroma together; after checking the catalog, the existing reindex command can rebuild index metadata.
- Linked policy versions cannot be removed through ordinary document deletion because that would break history or a scheduled replacement. Unpublished drafts can be removed. A scheduled revision cannot yet be cancelled through the UI.
- Provider errors, incomplete output and stale reports block publication. Needs review is a reviewer decision, not an automatic rejection; explicit HR acknowledgment is required.
- Only HR can access review reports. Normal employee search continues to enforce shared/own-document permissions. Relevant passages are sent to the configured AI provider, as with existing chat.

## API routes

All routes below require an authenticated HR account.

- `GET /admin/documents/{id}/revisions`: baseline and direct revisions.
- `POST /admin/documents/{id}/revisions`: multipart `file` and `metadata={"version":"2.0"}`; returns a draft.
- `GET /admin/revisions/{id}`: draft/version details, text diff and latest report.
- `POST /admin/revisions/{id}/review`: `{"questions":["Who approves remote work?"]}`; returns 202, then poll the detail route.
- `POST /admin/revisions/{id}/publish`: latest `review_id`, `acknowledge_review: true`, and optional timezone-aware `effective_at`.

## Running locally when port 8000 is occupied

Backend terminal:

```powershell
cd E:\companymind-ai
.\venv\Scripts\Activate.ps1
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001
```

Frontend terminal:

```powershell
cd E:\companymind-ai\frontend
$env:COMPANYMIND_API_TARGET='http://127.0.0.1:8001'
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173. No account reset or setup-demo rerun is needed for this upgrade.
