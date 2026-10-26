"""Local operator commands. Never expose this module as a public web endpoint."""
import argparse
import getpass
import sqlite3
from filelock import FileLock
from backend.app.rag.ingest import CATALOG_PATH

from backend.app.core.auth import IdentityStore
from fastapi import HTTPException


def index_documents():
    from backend.app.rag.chunking import create_chunks
    from backend.app.rag.ingest import load_catalog
    from backend.app.rag.vector_store import get_vector_store, index_catalog
    identities = IdentityStore()
    store = get_vector_store()
    load_catalog()  # Initialize the private registry before taking the operation lock.
    with FileLock(str(CATALOG_PATH) + ".lock", timeout=10):
        specs = load_catalog()
        for spec in specs:
            if spec.visibility == "employee" and not identities.user_exists(spec.owner_id):
                raise ValueError(f"Create the account for owner_id '{spec.owner_id}' before indexing.")
        # Serialize CLI indexing with HR uploads and removals.
        chunks = create_chunks(specs)
        count = index_catalog(store, chunks)
    print(f"Indexed {len(specs)} documents as {count} chunks in company_documents_v2.")


def main():
    parser = argparse.ArgumentParser(description="CompanyMind local administration")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("--id", dest="user_id", required=True)
    create.add_argument("--role", choices=["employee", "hr_admin"], default="employee")
    commands.add_parser("index")
    commands.add_parser("setup-demo")
    commands.add_parser("add-company-demo", help="Add the fictional shared company library without replacing existing documents.")
    delete = commands.add_parser("delete-document")
    delete.add_argument("document_id")
    args = parser.parse_args()
    if args.command == "setup-demo":
        identities = IdentityStore()
        for username, user_id, role in [
            ("alice", "EMP001", "employee"), ("bob", "EMP002", "employee"), ("hr", "HR001", "hr_admin")
        ]:
            if identities.user_exists(user_id):
                print(f"Account ID {user_id} already exists; keeping its password.")
                continue
            print(f"Create fictional demo account: {username} ({role})")
            password = getpass.getpass("Choose password (12–128 characters): ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords did not match. Run setup-demo again to continue.")
            identities.create_user(username, password, role, user_id)
        index_documents()
    elif args.command == "create-user":
        password = getpass.getpass("Choose password (12–128 characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("Passwords did not match.")
        try:
            employee = IdentityStore().create_user(args.username, password, args.role, args.user_id)
        except sqlite3.IntegrityError:
            raise ValueError("That username or employee ID already exists.") from None
        print(f"Created {employee.username}: ID={employee.user_id}, role={employee.role}")
    elif args.command == "index":
        index_documents()
    elif args.command == "add-company-demo":
        from backend.cli.library import install_company_library
        result = install_company_library()
        print(f"Company demo library: {result['added']} added, {result['skipped']} already available, {result['repaired']} repaired; {result['chunks']} new chunks indexed.")
    else:
        from backend.app.rag.vector_store import get_vector_store
        from backend.app.documents.admin import DocumentManager
        result = DocumentManager(get_vector_store(), IdentityStore()).delete(args.document_id)
        print(f"Removed document from catalog and search. Cleanup pending: {result['cleanup_pending']}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, HTTPException) as error:
        raise SystemExit(error.detail if isinstance(error, HTTPException) else str(error)) from None

