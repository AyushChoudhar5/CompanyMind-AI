"""Server-owned role and document audience rules for one company."""
from dataclasses import dataclass
from backend.app.core.publication import is_current, publication_filter


@dataclass(frozen=True)
class Employee:
    user_id: str
    username: str
    role: str


def document_filter(employee: Employee) -> dict:
    # The identity comes from the signed-in session, never from the request body.
    if employee.role == "hr_admin":
        allowed = {"visibility": {"$in": ["company", "employee", "hr"]}}
    elif employee.role == "employee":
        allowed = {"$or": [
            {"visibility": "company"},
            {"$and": [{"visibility": "employee"}, {"owner_id": employee.user_id}]},
        ]}
    else:
        # No document can have both values. Unknown roles receive no records.
        allowed = {"$and": [{"visibility": "company"}, {"visibility": "employee"}]}
    return {"$and": [allowed, {"upload_status": {"$nin": ["staging"]}}, publication_filter()]}


def can_read(metadata: dict, employee: Employee) -> bool:
    if employee.role not in {"employee", "hr_admin"} or metadata.get("upload_status") == "staging" or not is_current(metadata):
        return False
    visibility = metadata.get("visibility")
    if visibility == "hr":
        return employee.role == "hr_admin"
    return visibility == "company" or (
        visibility == "employee"
        and (employee.role == "hr_admin" or metadata.get("owner_id") == employee.user_id)
    )
