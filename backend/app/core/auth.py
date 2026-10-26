"""Local learning authentication. Replace with company SSO before real deployment.

Passwords use salted scrypt hashes. Sessions store only SHA-256 token hashes.
SQLite, rather than a process-global dictionary, allows sessions across workers.
"""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.access import Employee

from backend.app.core.paths import RUNTIME_DIR

DEFAULT_DB = RUNTIME_DIR / "identity.sqlite3"
SESSION_SECONDS = 3600
bearer = HTTPBearer(auto_error=False)


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return salt + ":" + digest.hex()


def password_matches(password: str, encoded: str) -> bool:
    salt, _ = encoded.split(":", 1)
    return hmac.compare_digest(password_hash(password, salt), encoded)


class IdentityStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("COMPANYMIND_IDENTITY_DB", DEFAULT_DB))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('employee','hr_admin')),
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    bucket TEXT PRIMARY KEY, started_at REAL NOT NULL, attempts INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, username: str, password: str, role: str = "employee", user_id: str | None = None):
        username = username.strip().casefold()
        if not username or len(username) > 100 or role not in {"employee", "hr_admin"}:
            raise ValueError("Use a username of 1–100 characters and a valid role.")
        if not 12 <= len(password) <= 128:
            raise ValueError("Choose a password of 12–128 characters.")
        employee = Employee(user_id or str(uuid.uuid4()), username, role)
        with self.connect() as db:
            db.execute("INSERT INTO users(user_id,username,role,password_hash) VALUES (?,?,?,?)",
                       (employee.user_id, employee.username, employee.role, password_hash(password)))
        return employee

    def user_exists(self, user_id: str):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM users WHERE user_id=? AND active=1", (user_id,)).fetchone() is not None

    def _limit_attempts(self, username: str, address: str):
        now = time.time()
        # Both per-account and per-address limits. Client IP comes from the server,
        # never a user-provided X-Forwarded-For header.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE started_at < ?", (now - 60,))
            for bucket, maximum in [("user:" + username, 5), ("ip:" + address, 30)]:
                row = db.execute("SELECT attempts FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
                if row and row["attempts"] >= maximum:
                    raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.", headers={"Retry-After": "60"})
                db.execute("INSERT INTO login_attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1", (bucket, now))

    def login(self, username: str, password: str, address: str):
        username = username.strip().casefold()
        self._limit_attempts(username, address)
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        # Perform the same expensive password operation for unknown usernames.
        valid = password_matches(password, row["password_hash"] if row else _DUMMY_HASH)
        if not row or not row["active"] or not valid:
            raise HTTPException(401, "Incorrect username or password.", headers={"WWW-Authenticate": "Bearer"})
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (self.token_hash(token), row["user_id"], time.time() + SESSION_SECONDS))
        return token, Employee(row["user_id"], row["username"], row["role"])

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def resolve(self, token: str):
        with self.connect() as db:
            row = db.execute("""SELECT u.user_id,u.username,u.role FROM sessions s
                JOIN users u ON u.user_id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
                (self.token_hash(token), time.time())).fetchone()
        return Employee(**dict(row)) if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (self.token_hash(token),))


_DUMMY_HASH = password_hash("dummy-password-never-used-for-login")


def current_employee(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Employee:
    employee = request.app.state.identities.resolve(credentials.credentials) if credentials else None
    if employee is None:
        raise HTTPException(401, "Sign in to access company documents.", headers={"WWW-Authenticate": "Bearer"})
    return employee
