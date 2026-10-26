"""Bounded, session-scoped topic memory for the single-process development app.

Only recent resolved user questions are remembered. Answers and retrieved
passages are never reused as evidence: every turn must search with today's ACL.
Restarting the backend clears memory. Multiple workers need a shared store.
"""
from dataclasses import dataclass, field
import hashlib
from threading import RLock
import time
from uuid import uuid4

HISTORY_LIMIT = 4
TTL_SECONDS = 1800
MAX_CONVERSATIONS = 256
MAX_PER_SESSION = 8


class ConversationError(RuntimeError):
    def __init__(self, detail, status_code=409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


EXPIRED_MESSAGE = "This conversation is unavailable or expired. Start a new conversation and ask a complete question."


def session_owner(employee, credentials):
    # Authentication is enforced by current_employee before this is called.
    # Include identity and role so a changed role cannot revive old context.
    token = credentials.credentials if credentials else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"{employee.user_id}:{employee.role}:{digest}"


@dataclass
class Conversation:
    conversation_id: str
    owner: str
    document_id: str | None
    updated_at: float
    questions: list[str] = field(default_factory=list)
    busy: bool = False


class ConversationStore:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._records = {}
        self._lock = RLock()

    def _prune(self):
        now = self._clock()
        for key, item in list(self._records.items()):
            if not item.busy and now - item.updated_at >= TTL_SECONDS:
                del self._records[key]

    def begin(self, owner, conversation_id=None, document_id=None):
        """Reserve a turn; reject concurrent writes and out-of-scope resumes."""
        with self._lock:
            self._prune()
            if conversation_id:
                item = self._records.get(str(conversation_id))
                if item is None or item.owner != owner:
                    raise ConversationError(EXPIRED_MESSAGE)
                if item.document_id != document_id:
                    raise ConversationError("The document selection changed. Start a new conversation and ask a complete question.")
                if item.busy:
                    raise ConversationError("An answer is still being prepared for this conversation. Wait before trying again.")
            else:
                owned = [item for item in self._records.values() if item.owner == owner]
                if len(owned) >= MAX_PER_SESSION:
                    self._evict(owned)
                if len(self._records) >= MAX_CONVERSATIONS:
                    self._evict(list(self._records.values()))
                item = Conversation(str(uuid4()), owner, document_id, self._clock())
                self._records[item.conversation_id] = item
            item.busy = True
            return item, list(item.questions)

    def _evict(self, candidates):
        idle = [item for item in candidates if not item.busy]
        if not idle:
            raise ConversationError("Too many answers are being prepared. Try again shortly.", 429)
        oldest = min(idle, key=lambda item: item.updated_at)
        self._records.pop(oldest.conversation_id, None)

    def commit(self, item, question):
        with self._lock:
            if self._records.get(item.conversation_id) is not item:
                # A concurrent logout/delete must never resurrect the thread.
                raise ConversationError(EXPIRED_MESSAGE)
            item.questions = [*item.questions, question][-HISTORY_LIMIT:]
            item.updated_at = self._clock()
            item.busy = False

    def abort(self, item):
        with self._lock:
            if self._records.get(item.conversation_id) is item:
                item.busy = False
                if not item.questions:
                    self._records.pop(item.conversation_id, None)

    def delete(self, owner, conversation_id):
        with self._lock:
            self._prune()
            item = self._records.get(str(conversation_id))
            if item is None or item.owner != owner:
                raise ConversationError("Conversation not found.", 404)
            del self._records[item.conversation_id]

    def discard_owner(self, owner):
        with self._lock:
            for key, item in list(self._records.items()):
                if item.owner == owner:
                    del self._records[key]
