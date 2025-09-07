# app/services/compliance_tasks.py
from typing import Iterable

def derive_compliance_status_from_tasks(tasks: Iterable[dict | object]) -> str:
    """
    Ulaz: kolekcija taskova (dict s .get ili ORM objekt s .status, .mandatory).
    Pravila (MVP):
      - ako postoji MANDATORY task u statusu open/blocked/postponed i due_date je prošao -> "non_compliant"
      - ako postoji bilo koji MANDATORY task u statusu open/blocked/postponed/in_progress -> "partially_compliant"
      - ako su svi MANDATORY 'done' -> "compliant"
    """
    any_mandatory_openish = False
    any_overdue = False

    from datetime import datetime, timezone
    now = datetime.utcnow()

    for t in tasks:
        status = (getattr(t, "status", None) or getattr(t, "get", lambda *_: None)("status") or "").lower()
        mandatory = getattr(t, "mandatory", None)
        if mandatory is None and hasattr(t, "get"):
            mandatory = t.get("mandatory")
        mandatory = bool(mandatory)

        if not mandatory:
            continue

        if status in {"open", "blocked", "postponed", "in_progress"}:
            any_mandatory_openish = True
            due = getattr(t, "due_date", None)
            if due is None and hasattr(t, "get"):
                due = t.get("due_date")
            if due and isinstance(due, datetime) and due < now:
                any_overdue = True

    if any_overdue:
        return "non_compliant"
    if any_mandatory_openish:
        return "partially_compliant"
    return "compliant"