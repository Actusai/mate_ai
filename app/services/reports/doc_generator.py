# app/services/reports/doc_generator.py
from __future__ import annotations

import io
import json
import os
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_, func, text

# Core models (always present in ovoj aplikaciji)
from app.models.company import Company
from app.models.ai_system import AISystem
from app.models.user import User
from app.models.document import Document

# Best-effort optional models (tolerant to absence)
try:
    from app.models.compliance_task import ComplianceTask  # pragma: no cover
except Exception:  # pragma: no cover
    ComplianceTask = None  # type: ignore

try:
    from app.models.incident import Incident  # pragma: no cover
except Exception:  # pragma: no cover
    Incident = None  # type: ignore

try:
    from app.models.regulatory_deadline import RegulatoryDeadline  # pragma: no cover
except Exception:  # pragma: no cover
    RegulatoryDeadline = None  # type: ignore

try:
    from app.models.ai_assessment import AIAssessment  # pragma: no cover
except Exception:  # pragma: no cover
    AIAssessment = None  # type: ignore


# -----------------------------
# Helpers (ISO + human-readable)
# -----------------------------
def _iso(v: Any) -> Optional[str]:
    """Return ISO string (keeps whatever precision DB gives)."""
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    try:
        return str(v)
    except Exception:
        return None


def _parse_to_dt(v: Any) -> Optional[datetime]:
    """Best-effort parse of date/datetime/ISO string to UTC datetime (seconds resolution)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    elif isinstance(v, str):
        try:
            s = v.strip()
            # tolerate trailing Z
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
    else:
        return None

    # ensure UTC tz and drop microseconds
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0)


def _fmt_display(v: Any) -> Optional[str]:
    """Human-readable: DD-MM-YYYY HH:MM:SS UTC"""
    dt = _parse_to_dt(v)
    if not dt:
        return None
    return dt.strftime("%d-%m-%Y %H:%M:%S") + " UTC"


def _now_iso_z_seconds() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# -----------------------------
# Data aggregation
# -----------------------------
def build_doc_payload(db: Session, ai_system_id: int) -> Dict[str, Any]:
    """
    Aggregate all relevant data for the EU Conformity Report into a JSON payload.
    Everything visible in the report is in ENGLISH.
    """
    system: AISystem | None = (
        db.query(AISystem).filter(AISystem.id == ai_system_id).first()
    )
    if not system:
        raise ValueError("AI system not found")

    company: Company | None = (
        db.query(Company).filter(Company.id == system.company_id).first()
    )

    # --- Basic system & company ---
    gen_at_iso = _now_iso_z_seconds()
    payload: Dict[str, Any] = {
        "generated_at": gen_at_iso,
        "generated_at_display": _fmt_display(gen_at_iso),
        "kind": "eu_conformity_report",
        "company": {
            "id": getattr(company, "id", None),
            "name": getattr(company, "name", None),
            "status": getattr(company, "status", None),
            "compliance_due_date": _iso(getattr(company, "compliance_due_date", None)),
            "compliance_due_date_display": _fmt_display(
                getattr(company, "compliance_due_date", None)
            ),
            "created_at": _iso(getattr(company, "created_at", None)),
            "created_at_display": _fmt_display(getattr(company, "created_at", None)),
        },
        "ai_system": {
            "id": system.id,
            "company_id": system.company_id,
            "name": system.name,
            "risk_tier": getattr(system, "risk_tier", None),
            "status": getattr(system, "status", None),
            "lifecycle_stage": getattr(system, "lifecycle_stage", None),
            "owner_user_id": getattr(system, "owner_user_id", None),
            "compliance_due_date": _iso(getattr(system, "compliance_due_date", None)),
            "compliance_due_date_display": _fmt_display(
                getattr(system, "compliance_due_date", None)
            ),
            "created_at": _iso(getattr(system, "created_at", None)),
            "created_at_display": _fmt_display(getattr(system, "created_at", None)),
            "updated_at": _iso(getattr(system, "updated_at", None)),
            "updated_at_display": _fmt_display(getattr(system, "updated_at", None)),
        },
        "sections": {},
    }

    # --- Assessments (latest first) ---
    assessments_out: List[Dict[str, Any]] = []
    if AIAssessment is not None:
        asses = (
            db.query(AIAssessment)
            .filter(AIAssessment.ai_system_id == ai_system_id)
            .order_by(
                getattr(AIAssessment, "version", 0).desc(), AIAssessment.id.desc()
            )
            .limit(10)
            .all()
        )
        for a in asses:
            assessments_out.append(
                {
                    "id": a.id,
                    "version": getattr(a, "version", None),
                    "status": getattr(a, "status", None),
                    "approved_at": _iso(getattr(a, "approved_at", None)),
                    "approved_at_display": _fmt_display(
                        getattr(a, "approved_at", None)
                    ),
                    "created_at": _iso(getattr(a, "created_at", None)),
                    "created_at_display": _fmt_display(getattr(a, "created_at", None)),
                    "updated_at": _iso(getattr(a, "updated_at", None)),
                    "updated_at_display": _fmt_display(getattr(a, "updated_at", None)),
                    "summary": getattr(a, "summary", None),
                }
            )
    payload["sections"]["assessments"] = assessments_out

    # --- Technical documentation (documents) ---
    docs_query = (
        db.query(Document)
        .filter(
            and_(
                Document.company_id == system.company_id,
                Document.ai_system_id == system.id,
            )
        )
        .order_by(Document.id.asc())
        .all()
    )
    docs_out: List[Dict[str, Any]] = []
    total_bytes = 0
    for d in docs_query:
        total_bytes += int(getattr(d, "size_bytes", 0) or 0)
        docs_out.append(
            {
                "id": d.id,
                "name": d.name,
                "type": getattr(d, "type", None),
                "content_type": getattr(d, "content_type", None),
                "size_bytes": getattr(d, "size_bytes", None),
                "status": getattr(d, "status", None),
                "review_due_at": _iso(getattr(d, "review_due_at", None)),
                "review_due_at_display": _fmt_display(
                    getattr(d, "review_due_at", None)
                ),
                "storage_url": getattr(d, "storage_url", None),
                "created_at": _iso(getattr(d, "created_at", None)),
                "created_at_display": _fmt_display(getattr(d, "created_at", None)),
                "updated_at": _iso(getattr(d, "updated_at", None)),
                "updated_at_display": _fmt_display(getattr(d, "updated_at", None)),
            }
        )
    payload["sections"]["technical_documentation"] = {
        "items": docs_out,
        "total_size_bytes": total_bytes,
        "count": len(docs_out),
    }

    # --- Compliance tasks snapshot ---
    tasks_summary = {"total": 0, "open": 0, "overdue": 0}
    tasks_preview: List[Dict[str, Any]] = []
    if ComplianceTask is not None:
        now = datetime.utcnow()
        q = db.query(ComplianceTask).filter(ComplianceTask.ai_system_id == ai_system_id)
        rows = q.order_by(ComplianceTask.due_date.asc().nulls_last()).limit(50).all()
        for t in rows:
            tasks_summary["total"] += 1
            status = (getattr(t, "status", "") or "").lower()
            is_open = status not in {"done", "cancelled"}
            if is_open:
                tasks_summary["open"] += 1
                if getattr(t, "due_date", None) and t.due_date < now:
                    tasks_summary["overdue"] += 1
            tasks_preview.append(
                {
                    "id": t.id,
                    "title": getattr(t, "title", None),
                    "status": getattr(t, "status", None),
                    "severity": getattr(t, "severity", None),
                    "mandatory": getattr(t, "mandatory", None),
                    "owner_user_id": getattr(t, "owner_user_id", None),
                    "reference": getattr(t, "reference", None),
                    "due_date": _iso(getattr(t, "due_date", None)),
                    "due_date_display": _fmt_display(getattr(t, "due_date", None)),
                    "completed_at": _iso(getattr(t, "completed_at", None)),
                    "completed_at_display": _fmt_display(
                        getattr(t, "completed_at", None)
                    ),
                }
            )
    payload["sections"]["compliance_tasks"] = {
        "summary": tasks_summary,
        "preview": tasks_preview,
    }

    # --- Incidents (last 20) ---
    inc_out: List[Dict[str, Any]] = []
    if Incident is not None:
        incs = (
            db.query(Incident)
            .filter(Incident.ai_system_id == ai_system_id)
            .order_by(Incident.occurred_at.desc().nulls_last(), Incident.id.desc())
            .limit(20)
            .all()
        )
        for i in incs:
            inc_out.append(
                {
                    "id": i.id,
                    "severity": getattr(i, "severity", None),
                    "type": getattr(i, "type", None),
                    "status": getattr(i, "status", None),
                    "summary": getattr(i, "summary", None),
                    "occurred_at": _iso(getattr(i, "occurred_at", None)),
                    "occurred_at_display": _fmt_display(
                        getattr(i, "occurred_at", None)
                    ),
                    "created_at": _iso(getattr(i, "created_at", None)),
                    "created_at_display": _fmt_display(getattr(i, "created_at", None)),
                }
            )
    payload["sections"]["incidents"] = inc_out

    # --- Regulatory deadlines (next/past 365d) ---
    reg_out: List[Dict[str, Any]] = []
    if RegulatoryDeadline is not None:
        now = datetime.utcnow()
        year_back = now.replace(year=now.year - 1)
        year_fwd = now.replace(year=now.year + 1)
        regs = (
            db.query(RegulatoryDeadline)
            .filter(
                and_(
                    RegulatoryDeadline.company_id == system.company_id,
                    RegulatoryDeadline.due_date >= year_back,
                    RegulatoryDeadline.due_date <= year_fwd,
                )
            )
            .order_by(RegulatoryDeadline.due_date.asc())
            .limit(200)
            .all()
        )
        for r in regs:
            reg_out.append(
                {
                    "id": r.id,
                    "title": r.title,
                    "status": getattr(r, "status", None),
                    "severity": getattr(r, "severity", None),
                    "due_date": _iso(getattr(r, "due_date", None)),
                    "due_date_display": _fmt_display(getattr(r, "due_date", None)),
                    "ai_system_id": getattr(r, "ai_system_id", None),
                }
            )
    payload["sections"]["regulatory_deadlines"] = reg_out

    return payload


# -----------------------------
# PDF generation (no external deps required)
# -----------------------------
def _make_simple_pdf(text_lines: List[str]) -> bytes:
    """
    Minimal single-page PDF generator (Helvetica 10pt) – dependency free.
    Good enough for a readable, downloadable document.
    """
    objs: List[bytes] = []

    def b(s: str) -> bytes:
        return s.encode("latin-1", "replace")

    # 1) Catalog
    objs.append(b("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"))
    # 2) Pages
    objs.append(b("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"))
    # 3) Page
    objs.append(
        b(
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            "/Resources << /Font << /F1 5 0 R >> >> "
            "/Contents 4 0 R >>\nendobj\n"
        )
    )

    # 4) Content stream
    y = 800  # start near top
    lines: List[str] = []
    lines.append("BT /F1 10 Tf 50 %d Td" % y)
    first = True
    for raw in text_lines:
        txt = raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if first:
            lines.append(f"({txt}) Tj")
            first = False
        else:
            lines.append("T*")
            lines.append(f"({txt}) Tj")
    lines.append("ET")
    content_str = "\n".join(lines)
    content_bytes = b(content_str)
    objs.append(b(f"4 0 obj\n<< /Length {len(content_bytes)} >>\nstream\n"))
    objs.append(content_bytes)
    objs.append(b"\nendstream\nendobj\n")

    # 5) Font
    objs.append(
        b("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    )

    # Assemble xref
    xref_positions: List[int] = []
    out = io.BytesIO()
    out.write(b("%PDF-1.4\n"))
    for obj in objs:
        xref_positions.append(out.tell())
        out.write(obj)
    xref_start = out.tell()
    out.write(b("xref\n0 %d\n" % (len(objs) + 1)))
    out.write(b("0000000000 65535 f \n"))
    for pos in xref_positions:
        out.write(b("%010d 00000 n \n" % pos))
    out.write(
        b(
            "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (len(objs) + 1, xref_start)
        )
    )
    return out.getvalue()


def _payload_to_text(payload: Dict[str, Any]) -> List[str]:
    """
    Flatten the JSON payload into human-readable lines for the PDF body.
    Uses *_display fields so users see DD-MM-YYYY HH:MM:SS UTC.
    """
    L: List[str] = []
    L.append("EU Conformity Report")
    L.append("----------------------------------------")
    L.append(
        f"Generated at: {payload.get('generated_at_display') or payload.get('generated_at')}"
    )
    L.append("")
    c = payload.get("company") or {}
    s = payload.get("ai_system") or {}
    L.append("Company")
    L.append(f"  ID: {c.get('id')}  Name: {c.get('name')}  Status: {c.get('status')}")
    if c.get("compliance_due_date") or c.get("compliance_due_date_display"):
        L.append(
            f"  Compliance due date: {c.get('compliance_due_date_display') or c.get('compliance_due_date')}"
        )
    L.append("")
    L.append("AI System")
    L.append(f"  ID: {s.get('id')}  Name: {s.get('name')}")
    L.append(f"  Risk tier: {s.get('risk_tier')}  Status: {s.get('status')}")
    if s.get("lifecycle_stage"):
        L.append(f"  Lifecycle stage: {s.get('lifecycle_stage')}")
    if s.get("compliance_due_date") or s.get("compliance_due_date_display"):
        L.append(
            f"  Compliance due date: {s.get('compliance_due_date_display') or s.get('compliance_due_date')}"
        )
    L.append("")

    # Assessments
    asses = (payload.get("sections", {}).get("assessments") or [])[:5]
    L.append(f"Assessments (latest {len(asses)})")
    for a in asses:
        L.append(
            f"  #{a.get('id')} v{a.get('version')} status={a.get('status')} approved_at={a.get('approved_at_display') or a.get('approved_at')}"
        )
    L.append("")

    # Technical docs
    tdocs = payload.get("sections", {}).get("technical_documentation", {})
    L.append(
        f"Technical documentation: {tdocs.get('count', 0)} item(s), total {tdocs.get('total_size_bytes', 0)} bytes"
    )
    for d in (tdocs.get("items") or [])[:8]:
        L.append(
            f"  [{d.get('type')}] {d.get('name')} (status={d.get('status')}, review_due={d.get('review_due_at_display') or d.get('review_due_at')})"
        )
    L.append("")

    # Compliance tasks
    comp = payload.get("sections", {}).get("compliance_tasks", {})
    summ = comp.get("summary", {})
    L.append(
        f"Compliance tasks: total={summ.get('total', 0)} open={summ.get('open', 0)} overdue={summ.get('overdue', 0)}"
    )
    for t in (comp.get("preview") or [])[:8]:
        L.append(
            f"  - {t.get('title')} (status={t.get('status')}, due={t.get('due_date_display') or t.get('due_date')})"
        )
    L.append("")

    # Incidents
    incs = payload.get("sections", {}).get("incidents", [])
    L.append(f"Incidents (last {len(incs)}):")
    for i in incs[:8]:
        L.append(
            f"  - [{i.get('severity')}] {i.get('summary')} (status={i.get('status')}, at={i.get('occurred_at_display') or i.get('occurred_at')})"
        )
    L.append("")

    # Deadlines
    regs = payload.get("sections", {}).get("regulatory_deadlines", [])
    L.append(f"Regulatory deadlines ({len(regs)} in ±1y):")
    for r in regs[:8]:
        L.append(
            f"  - {r.get('title')} due {r.get('due_date_display') or r.get('due_date')} (status={r.get('status')})"
        )
    return L


def create_doc_pdf(payload: Dict[str, Any]) -> bytes:
    """
    Return a PDF bytes document for the given payload.
    If reportlab is available, use it; otherwise use the minimal generator.
    """
    # Try reportlab if installed
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 40
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, "EU Conformity Report")
        y -= 24
        c.setFont("Helvetica", 9)

        for line in _payload_to_text(payload):
            if y < 40:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
            c.drawString(40, y, (line or "")[:120])
            y -= 12

        c.showPage()
        c.save()
        buffer.seek(0)
        return buffer.getvalue()
    except Exception:
        # Fallback to a dependency-free minimal PDF
        return _make_simple_pdf(_payload_to_text(payload))


# -----------------------------
# Persistence (as Document row)
# -----------------------------
def persist_pdf_as_document(
    db: Session,
    *,
    ai_system_id: int,
    company_id: int,
    user_id: Optional[int],
    pdf_bytes: bytes,
    display_name: Optional[str] = None,
) -> Document:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    name = display_name or f"EU Conformity Report ({ts})"
    folder = "/tmp/docs"
    os.makedirs(folder, exist_ok=True)
    fname = f"eu_conformity_aid{ai_system_id}_{ts}.pdf"
    path = os.path.join(folder, fname)
    with open(path, "wb") as f:
        f.write(pdf_bytes)

    doc = Document(
        company_id=company_id,
        ai_system_id=ai_system_id,
        uploaded_by=user_id,
        name=name,
        storage_url=path,  # local path; can be served by a download endpoint later
        content_type="application/pdf",
        size_bytes=len(pdf_bytes),
        type="doc_eu_conformity",
        status="complete",
        metadata_json=json.dumps(
            {
                "generated_at": _now_iso_z_seconds(),
                "format": "pdf",
                "note": "Auto-generated EU Conformity Report",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# -----------------------------
# High-level orchestration
# -----------------------------
def generate_doc_report(
    db: Session,
    *,
    ai_system_id: int,
    requested_by_user: Optional[User] = None,
    format: str = "pdf",  # "pdf" | "json"
    persist: bool = True,
    display_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point used by the API.
    Returns a dict with keys:
      - payload (dict)
      - pdf_bytes (optional)
      - document_id (optional)
      - storage_path (optional)
    """
    system = db.query(AISystem).filter(AISystem.id == ai_system_id).first()
    if not system:
        raise ValueError("AI system not found")

    payload = build_doc_payload(db, ai_system_id)

    out: Dict[str, Any] = {
        "payload": payload,
        "document_id": None,
        "storage_path": None,
    }
    if format.lower() == "json":
        # Optionally store JSON as a Document too (not required)
        if persist:
            json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode(
                "utf-8"
            )
            ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            folder = "/tmp/docs"
            os.makedirs(folder, exist_ok=True)
            fname = f"eu_conformity_aid{ai_system_id}_{ts}.json"
            path = os.path.join(folder, fname)
            with open(path, "wb") as f:
                f.write(json_bytes)
            doc = Document(
                company_id=system.company_id,
                ai_system_id=system.id,
                uploaded_by=getattr(requested_by_user, "id", None),
                name=display_name or f"EU Conformity Report (JSON) ({ts})",
                storage_url=path,
                content_type="application/json",
                size_bytes=len(json_bytes),
                type="doc_eu_conformity",
                status="complete",
                metadata_json=json.dumps(
                    {"format": "json", "generated_at": payload["generated_at"]}
                ),
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            out["document_id"] = doc.id
            out["storage_path"] = path
        return out

    # Default PDF flow
    pdf_bytes = create_doc_pdf(payload)
    out["pdf_bytes"] = pdf_bytes

    if persist:
        doc = persist_pdf_as_document(
            db,
            ai_system_id=system.id,
            company_id=system.company_id,
            user_id=getattr(requested_by_user, "id", None),
            pdf_bytes=pdf_bytes,
            display_name=display_name,
        )
        out["document_id"] = doc.id
        out["storage_path"] = doc.storage_url

    return out
