# CompanyMind: company knowledge and follow-up questions

CompanyMind can now search more than HR policies and continue the topic of a
conversation. The included company, people, products, contact addresses and
prices are fictional learning data. Real company knowledge must come from
approved company sources.

## Try it

Start the backend from the project root with your virtual environment active:

```powershell
cd E:\companymind-ai
python -m uvicorn api:app --reload
```

The original command also works from the backend folder. The package command
`python -m uvicorn backend.app.main:app --reload` works from the project root.

In a second terminal:

```powershell
cd E:\companymind-ai\frontend
npm run dev
```

Open http://localhost:5173 and sign in with your existing account. Choose
All permitted documents or select the fictional remote working policy.

1. Ask: What is the remote working policy?
2. Then ask: Does that apply during probation?
3. Check the citations in both answers. Each answer should use current passages.
4. Select New conversation, then ask: What does TechNova do?
5. Try product, team, IT and reimbursement questions as well.

Changing Search in starts a new topic. Old sidebar entries restore their own
document selection and conversation. Selecting an earlier answer continues the
latest topic of its conversation; it does not rewind the server's memory.

If the new shared documents are missing on another existing installation, run:

```powershell
cd E:\companymind-ai
python -m backend.cli.manage add-company-demo
```

This adds seven shared documents to the live catalog and Chroma. Rerunning skips
complete matching documents. Existing private documents are not read or reindexed
by this command. Its file hashes detect accidental edits to the bundled examples;
upload your own new documents through Manage documents instead of editing this
manifest and expecting automatic synchronization.

## What happens to a first question?

The browser sends the question and selected document to POST /chat. FastAPI
checks the signed-in employee, searches Chroma using that employee's permissions,
then asks the LLM to explain the retrieved passages. The answer includes citations
and a conversation_id: a random label for this conversation.

The browser holds that label in memory. It is not a password and it grants no
access by itself. The server also checks the login session that owns it.

## What happens to a follow-up?

Suppose the first question was "What is the remote working policy?" and the next
one is "Does that apply during probation?" The word "that" needs a topic.

```text
Latest question + recent question topics
                    |
                    v
LLM rewrites a standalone question
"Does the remote working policy apply during probation?"
                    |
                    v
Chroma search with CURRENT employee permissions
                    |
                    v
LLM explains the newly retrieved evidence with citations
```

The extra LLM step rewrites the question; it does not answer it. The normal RAG
step still finds evidence. Follow-ups use one additional provider request, so
provider limits and response times still matter.

## Why we remember questions rather than old answers

An answer could contain a mistake. A document could be removed after that answer.
An employee could lose permission to read it. Reusing its text as evidence would
carry an old mistake or old permission into the next response.

The server therefore remembers only the last four resolved user questions. It
never stores previous retrieved passages or assistant answers as evidence. It
uses those questions to resolve the topic and searches again on every turn.
This also means "explain point 2 of your last answer" may need clarification;
full transcript memory has not been implemented.

## Where the important code lives

- `backend/app/core/conversations.py`: remembers bounded question topics, checks
  session ownership and document scope, expires idle topics, prevents concurrent
  turns from overwriting each other, and clears memory on logout.
- `backend/app/rag/generation.py`: `rephrase_question` resolves the topic;
  `generate_answer` explains only the current retrieved passages.
- `backend/app/main.py`: coordinates authentication, memory, retrieval and answer
  generation. A failed turn is not added to future question history.
- `frontend/src/App.jsx`: sends and restores conversation labels, starts fresh
  topics and displays citations. It does not send a client-controlled transcript.
- `backend/cli/library.py`: installs only the bundled shared company documents,
  stages their chunks until publication, and preserves other catalog entries.

## Memory lifetime and limits

Memory belongs to one login session and one document selection. Another user,
another login by the same user, or a changed selection cannot resume that topic.
Idle topics expire after 30 minutes. Backend restarts clear all topic memory.
There are at most eight topics per session and 256 per server process; older idle
topics may be evicted. New conversation leaves old sidebar topics resumable until
expiry; signing out clears server memory for that login.

This is a single-process development design. Multiple backend workers need a
shared store, such as an appropriately protected database or cache, so requests
reach the same conversation history.

## What this milestone does not add

It does not invent company knowledge, read scanned PDFs, synchronize external
company systems, calculate live leave balances, or submit requests to HR. Those
need their own sources, integrations and tests. Conversation memory does not
replace the need for reliable company documents.
