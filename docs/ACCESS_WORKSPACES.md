# Employee and HR workspaces

CompanyMind now has a document library as well as a chat screen. The documents
shown to a user and the evidence sent to the AI follow the same server rules.

## Who can use what?

| Document audience | Alice | Bob | HR administrator |
|---|---|---|---|
| Everyone / shared policy | Yes | Yes | Yes |
| One employee: Alice | Yes | No | Yes |
| One employee: Bob | No | Yes | Yes |
| HR only | No | No | Yes |
| Unclassified or still being indexed | No | No | No |

Employees can browse their permitted documents, preview indexed passages, and
ask questions. Only HR can upload documents, assign their audience and owner,
list employee accounts, or remove documents from search. Creating accounts is
still a local administrator command; this change does not add self-registration.

## Try it as Alice

1. Sign in using Alice's existing account.
2. Open **My documents** in the sidebar.
3. Choose **My private documents** to see documents assigned to Alice, or
   **Shared policies** to see company-wide material.
4. Search by title, filename or category.
5. Select **Preview passages** to inspect the text indexed for search.
6. Select **Ask about this document**. The chat opens with that document selected
   and a suggested question. You can edit the question before sending it.

Bob's document titles and HR-only titles are not included in Alice's server
response. Typing or guessing their IDs does not grant access. Preview is limited
to 20 indexed passages and is clearly marked when incomplete; it is not a full
PDF reader or original-file download.

## Try it as HR

1. Sign in using the HR account.
2. Open **Document library** to browse all classified, published documents.
3. Open **Manage documents** to add material.
4. Choose the audience deliberately:
   - **All employees** for policies and other shared company knowledge.
   - **One employee + HR** for an individual's offer letter or agreement;
     select the existing employee who owns it.
   - **HR only** for confidential HR procedures and internal notes.
5. Choose a TXT or text-based PDF, then select **Upload and index document**.
6. Use the audience filter or search by title, employee name, employee ID,
   filename or category to find registered documents.

The sample at examples/hr_only_review_guide.txt is fictional and can be uploaded
with **HR only** to practise. After uploading, sign in as Alice and check that it
is absent from her library and document selector. It must also be absent from
her chat sources. The example is not automatically registered or uploaded.

## How the code enforces this

**Authentication** answers "Who signed in?" The backend looks up the bearer token
and gets the employee ID and role from its own account database.

**Authorization** answers "What may that person read or change?" The rules are in
backend/app/core/access.py. Role-based access gives HR management capabilities.
Ownership adds the rule that an employee may read a private document only when
its owner_id matches the signed-in employee's user_id.

The Chroma metadata field visibility can now be company, employee or hr.
An employee document must have an owner. A shared or HR-only document must not
have an owner, because its audience is determined by its classification.

```text
Login token -> server looks up employee ID and role
                          |
                          v
              Build permitted-document filter
                          |
                          v
      Read document list / preview / retrieve chat evidence
                          |
                          v
         Check returned metadata before exposing text
```

The AI does not decide permissions. Asking it to ignore rules cannot remove the
database filter. Hiding buttons is a convenience, while server dependencies
protect the management endpoints even when a request bypasses the interface.

backend/app/documents/library.py implements GET /documents/{document_id}/preview.
It reads indexed text through Chroma using the signed-in user's filter. It never
opens a user-supplied filesystem path. Unknown and inaccessible IDs have the same
404 response, so a caller cannot use preview responses to identify private files.

frontend/src/DocumentLibrary.jsx displays only the server's allowed list. Its
filters organize those already-permitted results. They are not security checks.
frontend/src/DocumentAdmin.jsx provides all three upload audiences and HR filters.

## Running alongside another local project

Port 8000 may already belong to another application. If so, start CompanyMind
on another port rather than stopping an unrelated server:

```powershell
cd E:\companymind-ai
.\venv\Scripts\Activate.ps1
python -m uvicorn api:app --reload --port 8001
```

In the frontend terminal:

```powershell
cd E:\companymind-ai\frontend
$env:COMPANYMIND_API_TARGET = 'http://127.0.0.1:8001'
npm run dev
```

Open http://localhost:5173. The override is read by the Vite development server,
not exposed as a browser credential. Without it, the existing default is port8000.

## What the checks establish

Backend tests exercise Alice, Bob and HR against lists, previews, search, chat,
uploads and removal using isolated Chroma and account databases. They check
unknown roles, guessed IDs, changed document ownership, inactive sessions, staged
documents, and a deliberately broken store adapter. Test LLM answers are mocked
so access control cannot accidentally depend on model obedience.

The browser checks use a separate real API with dummy accounts and local test
data. Existing user passwords and documents are not used for those checks.

This remains a local single-company project. Company SSO, department-level roles,
shared conversation storage and deployment controls remain future work.
