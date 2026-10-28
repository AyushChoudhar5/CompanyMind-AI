"""Time-based policy activation shared by retrieval, previews and administration."""
import time

END_OF_TIME = 4102444800  # 2100-01-01 UTC; finite timestamps are portable in Chroma.


def now_epoch():
    return int(time.time())


def publication_filter():
    now = now_epoch()
    return {"$and": [
        {"lifecycle": {"$nin": ["draft", "archived"]}},
        {"$or": [
            {"publication_control": {"$nin": ["versioned"]}},
            {"$and": [{"effective_from": {"$lte": now}}, {"effective_until": {"$gt": now}}]},
        ]},
    ]}


def is_current(metadata):
    if metadata.get("lifecycle") in {"draft", "archived"}:
        return False
    if metadata.get("publication_control") != "versioned":
        return True  # Existing classified documents keep their original behavior.
    try:
        return metadata["effective_from"] <= now_epoch() < metadata["effective_until"]
    except (KeyError, TypeError):
        return False


def status(spec):
    if spec.lifecycle != "published":
        return spec.lifecycle
    if spec.effective_from > now_epoch():
        return "scheduled"
    if spec.effective_until <= now_epoch():
        return "archived"
    return "published"


def metadata_for(spec):
    return {"publication_control": "versioned", "lifecycle": spec.lifecycle,
            "effective_from": spec.effective_from, "effective_until": spec.effective_until}
