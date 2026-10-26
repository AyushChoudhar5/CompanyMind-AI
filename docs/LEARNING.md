# How the employee-aware version works

## Start with the user experience

Alice signs in, selects her offer letter, and asks: "What is my notice period?"
The system must establish Alice's identity, restrict retrieval to documents she
may read, and then generate from that evidence. Showing sources afterward does
not fix an unauthorized retrieval that already sent private text to the LLM.

## Authentication: who are you?

`backend/app/core/auth.py` stores accounts in `runtime/identity.sqlite3`. SQLite is a relational
database: unlike Chroma's vector interface, this part does use SQL. The users
table stores an ID, username, role, active flag, and salted password hash.
It does not store the original password.

On login, scrypt hashes the supplied password with the stored random salt and
compares it securely. A successful login creates a random opaque token. The server
stores only its SHA-256 hash and expiry. The browser holds the token in React
memory and sends `Authorization: Bearer <token>` with protected requests.

FastAPI's `current_employee` dependency validates that token and looks up the
current account. Roles and IDs come from the database, not the request body.
Disabled accounts and expired or revoked tokens are denied. Local sign-in has
basic per-account and per-address rate limits. It is a teaching implementation;
a company rollout should use an approved identity provider and operational controls.

## Authorization: what may you read?

`backend/app/core/access.py` constructs the retrieval filter. A standard employee may read:

```text
visibility == company
OR
(visibility == employee AND owner_id == signed_in_employee_id)
```

The HR administrator role is deliberately granted shared, employee and HR-only documents.
Documents without a recognized visibility are denied. This is a single-company
model; it does not yet implement company tenants, department grants, or delegated
HR case access.

`backend/app/rag/retrieval.py` passes the filter into Chroma before nearest-neighbor retrieval.
Selecting a document adds another AND condition, so selecting Bob's document as
Alice cannot bypass the original rule. It then checks returned metadata again.
Any violation stops the request before generation and returning evidence.

The same policy also applies to the document list. Otherwise even a filename or
title might disclose that another employee has a particular agreement.

## Ingestion: prepare searchable evidence

`data/catalog.json` is the fictional seed list. The live list is
`runtime/catalog.json`, initialized once and ignored by Git. It is the explicit
list of approved local inputs maintained by the HR screen. It records the
document's identity, category, version, visibility, and optional employee owner.
This is metadata: information about the content. A filename alone is not an ACL.

`backend/app/rag/ingest.py` validates the catalog with Pydantic and reads TXT/PDF content. A PDF
becomes one LangChain Document per page. The page number starts at 1. A text file
has no page label. `backend/app/rag/chunking.py` splits those Documents while preserving their
metadata, including ownership. The familiar 300-character/50-overlap settings
remain so you can learn access control without changing every retrieval choice.

`backend/cli/manage.py` verifies that private owners have accounts before indexing. It reads
all input documents before starting replacement, then `backend/app/rag/vector_store.py` replaces
chunks for each document in `company_documents_v2`. Reusing a document ID replaces
its prior chunks; the count cannot silently accumulate stale trailing chunks.
An interrupted replacement is still non-atomic, an important production gap.

The embedding model remains local all-MiniLM-L6-v2. Chroma stores the chunk text,
384-number embedding, ID, and metadata. Relational identity data belongs in SQLite;
semantic document search belongs in Chroma. Each database has a different job.

## Generation: explain the evidence

`backend/app/main.py` calls permission-aware retrieval, then `backend/app/rag/generation.py` formats the allowed
passages with reference labels, titles, versions, and PDF pages. The Groq SDK
receives the question and that evidence. The LLM does not make permission decisions.

The prompt distinguishes general policies from personal terms, identifies
conflicts rather than inventing precedence, and directs unresolved contract
interpretation to HR. It cannot calculate remaining leave or a remaining bond
period without the underlying records. Those rules guide generation; tests and
independent answer evaluation are still needed to establish reliability.

Chroma being local does not make generation local. Relevant passages are sent to
Groq. Fictional samples let you develop this flow without using real records.

## Frontend: display the permitted workspace

React shows a sign-in screen first. After login it fetches only the user's allowed
document list. The source selector sends a document ID, not a user ID or role.
Signing out revokes the server session and unmounts the workspace, clearing its
answers and history before another user signs in. Tokens are not stored in
localStorage. Reloading the page requires signing in again.

## Exercise: predict, inspect, then test

1. Run `python -m backend.cli.manage setup-demo` and choose the three passwords yourself.
2. Predict which documents alice, bob, and hr should see before signing in.
3. As Alice, open the document selector. Find her offer and training agreement.
4. Ask about her offer's notice period with that document selected.
5. Select all permitted documents and ask how the offer differs from general policy.
6. Ask for Bob's salary. Inspect the evidence, not just the wording of the answer.
   No Bob passage should appear in Alice's evidence.
7. Sign out and in as Bob. Verify Alice's history and private documents are absent.
8. Read `document_filter` and trace the actual metadata values through the rule.
9. Open the permission tests. Find the test that makes the store return Bob's
   passage to Alice and explain why generation must not be called.

Questions to answer yourself: why is hiding a sidebar item insufficient? Why is
the employee ID read from a session? Why does the API filter before asking the LLM?
Why does adding an explicit document ID narrow permissions instead of widening them?


## New lesson: from HR upload to searchable evidence

Previously you edited a catalog and ran the indexing script. Now HR can do the
same work through a form. The underlying reader, splitter, embedding model, and
Chroma database are still the same components.

1. React creates a FormData object containing a file and a JSON metadata string.
   Regular JSON alone is not how this endpoint receives the file bytes.
2. The API checks the session and HR role before parsing the multipart upload.
   Hiding an HR button in React is only a UI convenience, not the permission check.
3. The service validates the audience and active owner. It saves bytes under a
   random server-generated name; it does not trust a client filename as a path.
4. The registered reader extracts text, preserving real PDF page numbers. The
   shared split_pages function breaks that text into Documents with metadata.
5. The embedding/indexing call writes chunks marked staging. Employees cannot
   retrieve those records while the upload is incomplete.
6. The catalog is saved atomically, then a Chroma metadata update marks the
   chunks published. Only then does the endpoint return success.
7. Returning to questions reloads the permitted document selector. A new private
   document appears for its owner and HR, not for other employees.

The LLM is not called during upload. Embeddings prepare search; generation happens
later when somebody asks a question. The code for this workflow is backend/app/documents/admin.py.
It is separated from backend/app/main.py to keep document operations readable.

A file lock serializes catalog updates from multiple HR requests and the CLI.
Atomic replacement means readers see either the old complete JSON or the new
complete JSON, rather than half-written content. It does not make the filesystem
and Chroma one transaction, which is why failure/rollback tests and future durable
jobs are important.

Try both fictional files in examples/. Before uploading, predict which accounts
will see them. Then inspect the source passage after asking a question. Finally,
remove one uploaded document and explain why it must disappear from the live
catalog as well as Chroma.


## Company-wide knowledge and follow-up questions

The next learning milestone is documented in COMPANY_CHAT.md in this folder.
The assistant now resolves follow-up topics using recent user questions, then
retrieves current evidence with the same employee permission checks. It does not
treat an earlier answer as a company fact or keep past source text as memory.


## Role-aware document workspaces

See ACCESS_WORKSPACES.md for the new HR-only audience, employee document library,
protected indexed-passage previews and a hands-on Alice/Bob/HR exercise.
