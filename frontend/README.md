# CompanyMind frontend

React and Vite provide the employee question interface and HR document-management
screen. The browser connects to the FastAPI backend through Vite's /api proxy.

## Run locally

From E:\companymind-ai, with the Python environment activated:

```powershell
python -m uvicorn backend.app.main:app --reload
```

In a second terminal:

```powershell
cd E:\companymind-ai\frontend
npm run dev
```

Open http://localhost:5173 and sign in. Existing accounts are preserved after the
backend folder reorganization. For initial setup on a new installation, follow
the repository README and run python -m backend.cli.manage setup-demo.

## What the interface does

- Sign-in exchanges credentials for a server session. Tokens stay in React memory.
- The document selector displays only documents permitted for the signed-in user.
- Questions POST to /api/chat with question, top_k, optional document_id, and
  conversation_id when continuing a conversation. No client history is sent.
- Answers show source passages, versions, and PDF page numbers when available.
- Follow-up questions use a server conversation ID bound to the signed-in session
  and chosen document scope. The server uses recent questions to resolve context,
  then searches the current permitted documents again for every answer.
- New conversation and changing Search in start a fresh topic. Sidebar entries
  restore their own conversation ID and document scope. Expired conversation IDs
  are cleared after a 409 response; the UI asks for a full question and does not
  automatically resend the request.
- Recent questions and conversation IDs stay only in React memory (up to 12
  entries). Reloading or signing out clears this tab's history. The server also
  expires inactive conversations. New conversation drops the current ID without
  deleting old conversations, allowing sidebar entries to resume until expiry.
- Employees see My documents for shared/own documents; HR sees Document library for all classified documents.
- Previews call the protected /documents/{id}/preview endpoint; Ask about this document starts a fresh scoped conversation.
- HR sees Manage documents for shared, employee-owned and HR-only uploads, audience/search filters, and removal.
- Returning from Manage documents clears the conversation and refreshes the list.
- Sign-out revokes the session and clears the workspace's question history.

The backend verifies permissions on every protected request. React's role-based
navigation is not the security boundary. Conversation memory is not evidence;
answers still require a fresh permission-filtered document search. Earlier
answers can be selected to inspect their citations, but follow-ups always use
the conversation's latest topic, rather than branching from an older turn.

## Development connection

Vite forwards /api to http://127.0.0.1:8000 and removes the prefix. Python package
names do not change the HTTP routes. API keys stay in the backend's root .env;
never put a provider key in a public VITE_ environment variable.

The health indicator checks the backend process, not provider readiness. Responses
and source passages render as React text rather than injected HTML. The UI handles
loading, timeouts, provider errors, citation navigation, and removal confirmation.
After a timeout or lost response, the UI clears that conversation ID because the
server may already have processed the question. It never automatically repeats
an uncertain request; the user can edit and submit a full question explicitly.

## Checks

```powershell
npm run build
npm run lint
```

Backend tests run from the repository root with python -m pytest -q. See the
repository README for the scope of automated and browser integration checks.

## Deployment

Vite's proxy is for development and local preview. Production needs explicit
routing to FastAPI and HTTPS. The beige interface uses DM Sans through Google Fonts, with local system-font fallbacks. The backend's Groq client uses the Windows system
certificate store through truststore without disabling HTTPS verification.

For a backend on another port, set COMPANYMIND_API_TARGET in the Vite process environment. See docs/ACCESS_WORKSPACES.md for commands.

## Policy revision review

HR opens Manage documents > Review policy revision. PolicyRevisions.jsx provides draft upload, question selection, report polling, evidence, explicit approval and effective dates. Status filters distinguish drafts, scheduled, published and archived versions. See ../docs/POLICY_REVISIONS.md.
