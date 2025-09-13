# app/api/v1/documents.py
from __future__ import annotations

import io
import os
import json
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
    Request,
    UploadFile,
    File,
    Response,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, text, func

from app.core.auth import get_db, get_current_user
from app.core.scoping import can_read_company, can_write_company, is_super
from app.core.rbac import ensure_system_access_read, ensure_system_write_limited
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.document import Document
from app.schemas.document import DocumentOut, DocumentPackCreate
from app.services.audit import audit_log, ip_from_request  # best-effort

# Optional notifications (tolerant import)
try:
    from app.services.notifications import send_pending_notifications  # type: ignore
except Exception:
    send_pending_notifications = None  # type: ignore

# Optional readiness view (not required; keep local logic self-contained)
try:
    from app.services.compliance import get_ar_readiness  # pragma: no cover
except Exception:
    get_ar_readiness = None  # type: ignore

from pydantic import BaseModel, Field

router = APIRouter()

# Where we store uploaded files locally (can be replaced with S3 later)
DOC_FOLDER = os.environ.get("DOC_STORAGE_DIR", "/var/app/docs")
DOC_PACKS_DIR = os.environ.get("DOC_PACKS_DIR", os.path.join(DOC_FOLDER, "packs"))

os.makedirs(DOC_FOLDER, exist_ok=True)
os.makedirs(DOC_PACKS_DIR, exist_ok=True)


# -----------------------------
# Helpers
# -----------------------------
def _load_system_or_404(db: Session, system_id: int) -> AISystem:
    obj = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")
    return obj


def _doc_to_out(doc: Document) -> DocumentOut:
    # Parse metadata_json -> metadata (dict)
    meta: Optional[Dict[str, Any]] = None
    try:
        if getattr(doc, "metadata_json", None):
            meta = json.loads(doc.metadata_json)
    except Exception:
        meta = None

    return DocumentOut.model_validate(
        {
            "id": doc.id,
            "company_id": doc.company_id,
            "ai_system_id": doc.ai_system_id,
            "uploaded_by": doc.uploaded_by,
            "name": doc.name,
            "storage_url": doc.storage_url,
            "content_type": doc.content_type,
            "size_bytes": doc.size_bytes,
            "type": doc.type,
            "status": doc.status,
            "review_due_at": doc.review_due_at,
            "metadata": meta,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }
    )


def _safe_fs_path_from_storage_url(url: Optional[str]) -> Optional[str]:
    """
    If storage_url is a local path or file:// URL, return a filesystem path; otherwise None.
    """
    if not url:
        return None
    if url.startswith("file://"):
        return url[len("file://") :]
    if url.startswith("/"):
        return url
    return None


def _unique_ints(values: Optional[List[int]]) -> List[int]:
    if not values:
        return []
    try:
        return list({int(v) for v in values})
    except Exception:
        return []


def _parse_iso_or_none(s: Optional[str]):
    if not s:
        return None
    try:
        # Accept both date and datetime (tolerate trailing Z)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _merge_doc_metadata(doc: Document, updates: Dict[str, Any]) -> None:
    """
    Safely merge a partial dict into Document.metadata_json.
    Preserves existing keys and appends to list fields if needed.
    """
    try:
        current = (
            json.loads(doc.metadata_json) if getattr(doc, "metadata_json", None) else {}
        )
    except Exception:
        current = {}

    def _deep_merge(a: Any, b: Any) -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            out = dict(a)
            for k, v in b.items():
                if k in out:
                    out[k] = _deep_merge(out[k], v)
                else:
                    out[k] = v
            return out
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        return b

    merged = _deep_merge(current, updates)
    doc.metadata_json = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


# -----------------------------
# NEW: Upload a single document
# -----------------------------
@router.post(
    "/documents/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    request: Request,  # <-- move first: non-default must come before defaulted params
    company_id: int = Query(..., ge=1),
    ai_system_id: Optional[int] = Query(None, ge=1),
    type: Optional[str] = Query(
        "evidence", description="Document type, e.g. evidence, policy, report, fria"
    ),
    status_v: Optional[str] = Query("active", alias="status"),
    review_due_at: Optional[str] = Query(
        None, description="ISO date/time when review is due"
    ),
    display_name: Optional[str] = Query(
        None, description="Human-friendly display name"
    ),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a document (multipart/form-data).
    RBAC:
      - system-scoped → requires at least limited write on that AI system,
      - company-scoped → requires company-level write.
    """
    # RBAC
    if ai_system_id:
        # requires at least limited write on the system
        sys = ensure_system_write_limited(db, current_user, ai_system_id)
        if sys.company_id != company_id:
            raise HTTPException(
                status_code=400, detail="AI system does not belong to the company"
            )
    else:
        if not can_write_company(db, current_user, company_id):
            raise HTTPException(status_code=403, detail="Insufficient privileges")

    # Persist file to disk
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base_name = (
        (display_name or file.filename or "document")
        .strip()
        .replace("/", "_")
        .replace("\\", "_")
    )
    fname = f"cid{company_id}_aid{ai_system_id or 0}_{ts}_{base_name}"
    path = os.path.join(DOC_FOLDER, fname)

    size_bytes = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                out.write(chunk)
    finally:
        await file.close()

    # Parse review_due_at if provided
    parsed_review_due_at = _parse_iso_or_none(review_due_at)

    # Create DB row
    doc = Document(
        company_id=company_id,
        ai_system_id=ai_system_id,
        uploaded_by=getattr(current_user, "id", None),
        name=base_name,
        storage_url=path,  # local path; consider file:// if you prefer
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size_bytes,
        type=type,
        status=status_v or "active",
        review_due_at=parsed_review_due_at,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=company_id,
            user_id=current_user.id,
            action="DOCUMENT_UPLOADED",
            entity_type="document",
            entity_id=doc.id,
            meta={
                "ai_system_id": ai_system_id,
                "name": doc.name,
                "type": type,
                "status": status_v,
                "size_bytes": size_bytes,
                "content_type": doc.content_type,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _doc_to_out(doc)


# -----------------------------
# NEW: List documents (generic)
# -----------------------------
@router.get("/documents", response_model=List[DocumentOut])
def list_documents(
    company_id: Optional[int] = Query(None, ge=1),
    ai_system_id: Optional[int] = Query(None, ge=1),
    type: Optional[str] = Query(
        None, description="Filter by document type (e.g. fria)"
    ),
    status_v: Optional[str] = Query(None, alias="status"),
    q: Optional[str] = Query(None, description="Substring match on name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List documents. Non-super users are automatically scoped to their company.
    """
    qry = db.query(Document)

    # RBAC scoping
    if not is_super(current_user):
        if company_id is None:
            company_id = current_user.company_id
        if not can_read_company(db, current_user, company_id):
            raise HTTPException(status_code=403, detail="Forbidden")
    if company_id is not None:
        qry = qry.filter(Document.company_id == company_id)

    if ai_system_id is not None:
        # ensure they can read the system
        ensure_system_access_read(db, current_user, ai_system_id)
        qry = qry.filter(Document.ai_system_id == ai_system_id)

    if type:
        qry = qry.filter(Document.type == type)
    if status_v:
        qry = qry.filter(Document.status == status_v)
    if q:
        # SQLite-friendly ILIKE fallback
        qry = qry.filter(func.lower(Document.name).like(f"%{q.lower()}%"))

    rows = qry.order_by(Document.id.desc()).offset(skip).limit(limit).all()
    return [_doc_to_out(r) for r in rows]


# -----------------------------
# NEW: Convenience list by system (alias)
# -----------------------------
@router.get("/ai-systems/{system_id}/documents", response_model=List[DocumentOut])
def list_documents_for_system(
    system_id: int,
    type: Optional[str] = Query(
        None, description="Filter by document type (e.g. fria)"
    ),
    status_v: Optional[str] = Query(None, alias="status"),
    q: Optional[str] = Query(None, description="Substring match on name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # RBAC read on system
    ensure_system_access_read(db, current_user, system_id)
    return list_documents(
        company_id=None,
        ai_system_id=system_id,
        type=type,
        status_v=status_v,
        q=q,
        skip=skip,
        limit=limit,
        db=db,
        current_user=current_user,
    )


# -----------------------------
# NEW: Download a document
# -----------------------------
@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Download the stored file. Uses storage_url path on disk.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # RBAC
    if not is_super(current_user) and not can_read_company(
        db, current_user, doc.company_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not doc.storage_url or not os.path.exists(doc.storage_url):
        raise HTTPException(status_code=404, detail="Stored file not found")

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=doc.company_id,
            user_id=current_user.id,
            action="DOCUMENT_DOWNLOADED",
            entity_type="document",
            entity_id=doc.id,
            meta={"ai_system_id": doc.ai_system_id, "name": doc.name},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return FileResponse(
        doc.storage_url,
        media_type=doc.content_type or "application/octet-stream",
        filename=doc.name or f"document_{doc.id}",
    )


# -----------------------------
# NEW: Delete a document
# -----------------------------
@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Delete a document (DB row + stored file).
    Requires company write (or system write if linked).
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # RBAC: if system-scoped require limited write on that system, else company-level write
    if doc.ai_system_id:
        ensure_system_write_limited(db, current_user, doc.ai_system_id)
    else:
        if not can_write_company(db, current_user, doc.company_id):
            raise HTTPException(status_code=403, detail="Insufficient privileges")

    # Remove file from disk if present
    try:
        if doc.storage_url and os.path.exists(doc.storage_url):
            os.remove(doc.storage_url)
    except Exception:
        pass

    db.delete(doc)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -----------------------------
# Local fallback: generate "stale evidence" reminders
# -----------------------------
def _generate_stale_evidence_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    duplicate_guard_hours: int = 24,
) -> int:
    """
    Enqueue 'doc_review_due' notifications for documents where:
      - review_due_at < now()
      - status != 'complete'
    Duplicate guard: skip if same (document_id) was queued within last N hours.
    """
    filters = [
        Document.review_due_at.isnot(None),
        Document.review_due_at < func.now(),
        (Document.status.is_(None)) | (Document.status != "complete"),
    ]
    if for_company_id is not None:
        filters.append(Document.company_id == for_company_id)

    docs: List[Document] = (
        db.query(Document)
        .filter(and_(*filters))
        .order_by(Document.review_due_at.asc())
        .all()
    )

    created = 0
    for d in docs:
        # duplicate guard via LIKE on payload JSON
        like_pattern = f'%"document_id":{int(d.id)}%'

        row = db.execute(
            text(
                """
                SELECT 1
                  FROM notifications
                 WHERE type = 'doc_review_due'
                   AND company_id = :cid
                   AND (
                        (:aid IS NULL AND ai_system_id IS NULL)
                     OR (ai_system_id = :aid)
                   )
                   AND payload LIKE :pattern
                   AND created_at >= datetime('now', :since)
                 LIMIT 1
                """
            ),
            {
                "cid": int(d.company_id),
                "aid": int(d.ai_system_id) if d.ai_system_id is not None else None,
                "pattern": like_pattern,
                "since": f"-{int(duplicate_guard_hours)} hour",
            },
        ).fetchone()

        if row:
            continue

        payload = {
            "document_id": int(d.id),
            "ai_system_id": int(d.ai_system_id) if d.ai_system_id is not None else None,
            "company_id": int(d.company_id),
            "document_type": d.type,
            "document_name": d.name,
            "review_due_at": d.review_due_at.isoformat() if d.review_due_at else None,
            "reason": "review_due",
        }

        db.execute(
            text(
                """
                INSERT INTO notifications(
                    company_id, user_id, ai_system_id, task_id,
                    type, channel, payload,
                    status, error, scheduled_at, sent_at, created_at
                ) VALUES (
                    :company_id, NULL, :ai_system_id, NULL,
                    'doc_review_due', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": int(d.company_id),
                "ai_system_id": (
                    int(d.ai_system_id) if d.ai_system_id is not None else None
                ),
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


def _fallback_mark_sent(db: Session, *, for_company_id: Optional[int] = None) -> int:
    """
    Fallback if send_pending_notifications service is unavailable:
    mark queued 'doc_review_due' as sent.
    """
    params: Dict[str, Any] = {}
    where = "status = 'queued' AND type = 'doc_review_due'"
    if for_company_id is not None:
        where += " AND company_id = :cid"
        params["cid"] = int(for_company_id)

    rows = (
        db.execute(
            text(
                f"""
            SELECT id FROM notifications
            WHERE {where}
            """
            ),
            params,
        )
        .mappings()
        .all()
    )

    if not rows:
        return 0

    ids = [int(r["id"]) for r in rows]
    db.execute(
        text(
            f"""
            UPDATE notifications
               SET status = 'sent',
                   sent_at = datetime('now')
             WHERE id IN ({",".join(str(i) for i in ids)})
            """
        )
    )
    db.commit()
    return len(ids)


# -----------------------------
# POST /documents/packs – generate ZIP
# -----------------------------
@router.post(
    "/documents/packs", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
def create_document_pack(
    request: Request,
    payload: DocumentPackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _load_system_or_404(db, payload.ai_system_id)

    # RBAC
    if not can_write_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    # Collect source documents
    q = db.query(Document).filter(
        and_(
            Document.company_id == system.company_id,
            Document.ai_system_id == system.id,
        )
    )
    # Exclude previous packs from inclusion
    q = q.filter((Document.type.is_(None)) | (Document.type != "doc_pack_zip"))

    ids = _unique_ints(payload.document_ids)
    if ids:
        q = q.filter(Document.id.in_(ids))

    if payload.types:
        q = q.filter(Document.type.in_(payload.types))

    docs: List[Document] = q.order_by(Document.id.asc()).all()
    if not docs:
        raise HTTPException(
            status_code=404, detail="No matching documents found to pack"
        )

    # Build ZIP in-memory
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED)

    manifest_items: List[Dict[str, Any]] = []
    included = 0
    skipped = 0

    for d in docs:
        fs_path = _safe_fs_path_from_storage_url(d.storage_url)
        safe_name = (d.name or "file").replace("/", "_").replace("\\", "_")
        entry_name = f"{d.type or 'doc'}_{d.id}_{safe_name}"

        if fs_path and os.path.isfile(fs_path):
            try:
                zf.write(fs_path, arcname=entry_name)
                included += 1
                manifest_items.append(
                    {
                        "id": d.id,
                        "name": d.name,
                        "type": d.type,
                        "content_type": d.content_type,
                        "size_bytes": d.size_bytes,
                        "storage": "embedded",
                        "source_path": fs_path,
                    }
                )
            except Exception as ex:
                skipped += 1
                manifest_items.append(
                    {
                        "id": d.id,
                        "name": d.name,
                        "type": d.type,
                        "content_type": d.content_type,
                        "size_bytes": d.size_bytes,
                        "storage": "skipped",
                        "reason": f"read_error: {ex}",
                    }
                )
        else:
            skipped += 1
            manifest_items.append(
                {
                    "id": d.id,
                    "name": d.name,
                    "type": d.type,
                    "content_type": d.content_type,
                    "size_bytes": d.size_bytes,
                    "storage": "referenced_only",
                    "storage_url": d.storage_url,
                }
            )

    # Add manifest.json
    manifest = {
        "ai_system_id": system.id,
        "company_id": system.company_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "generated_by": current_user.id,
        "source_document_ids": [d.id for d in docs],
        "types_requested": payload.types,
        "included_count": included,
        "skipped_count": skipped,
        "items": manifest_items,
    }
    zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    zf.close()

    buf.seek(0)
    zip_bytes = buf.getvalue()
    size = len(zip_bytes)

    # Persist ZIP to disk (simple local storage)

    folder = DOC_PACKS_DIR
    os.makedirs(folder, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fname = f"doc_pack_aid{system.id}_{ts}.zip"
    fpath = os.path.join(folder, fname)
    with open(fpath, "wb") as f:
        f.write(zip_bytes)

    display_name = payload.name or f"Technical Documentation Pack ({ts})"

    # Create a Document row for the pack
    pack = Document(
        company_id=system.company_id,
        ai_system_id=system.id,
        uploaded_by=current_user.id,
        name=display_name,
        storage_url=fpath,  # local path; later you can add a download endpoint
        content_type="application/zip",
        size_bytes=size,
        type="doc_pack_zip",
        status="complete",
        metadata_json=json.dumps(
            {
                "source_document_ids": manifest["source_document_ids"],
                "types_requested": payload.types,
                "included_count": included,
                "skipped_count": skipped,
                "generated_at": manifest["generated_at"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    db.add(pack)
    db.commit()
    db.refresh(pack)

    # Audit (best-effort)
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="DOC_PACK_CREATED",
            entity_type="document",
            entity_id=pack.id,
            meta={
                "ai_system_id": system.id,
                "source_document_ids": manifest["source_document_ids"],
                "included_count": included,
                "skipped_count": skipped,
                "file": fpath,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _doc_to_out(pack)


# -----------------------------
# GET /documents/packs – list generated packs
# -----------------------------
@router.get("/documents/packs", response_model=List[DocumentOut])
def list_document_packs(
    ai_system_id: Optional[int] = Query(None, ge=1),
    company_id: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List generated documentation packs (type='doc_pack_zip').

    Scoping:
      - If ai_system_id is provided, RBAC is checked via that system's company.
      - Else: super admin sees all; others restricted to their company (or empty if none).
    """
    q = db.query(Document).filter(Document.type == "doc_pack_zip")

    if ai_system_id is not None:
        system = _load_system_or_404(db, ai_system_id)
        if not can_read_company(db, current_user, system.company_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        q = q.filter(
            (Document.ai_system_id == system.id)
            & (Document.company_id == system.company_id)
        )
    else:
        if is_super(current_user):
            if company_id is not None:
                q = q.filter(Document.company_id == company_id)
        else:
            if not current_user.company_id:
                return []
            q = q.filter(Document.company_id == current_user.company_id)

    rows = q.order_by(Document.created_at.desc()).all()
    return [_doc_to_out(r) for r in rows]


# -----------------------------
# POST /documents/reminders/review-due-run – trigger stale evidence reminders (admin)
# -----------------------------
@router.post("/documents/reminders/review-due-run")
def run_review_due_reminders(
    company_id: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Triggers reminders for documents where review_due_at < now().
    RBAC: Super Admin only.
    """
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    created = _generate_stale_evidence_reminders(db, for_company_id=company_id)

    if callable(send_pending_notifications):
        try:
            sent = send_pending_notifications(db, for_company_id=company_id)  # type: ignore
        except Exception:
            sent = 0
    else:
        # Fallback: mark queued 'doc_review_due' as sent
        sent = _fallback_mark_sent(db, for_company_id=company_id)

    return {"ok": True, "created": created, "sent": sent}


# -----------------------------
# NEW: Send to authority (audit + metadata record)
# -----------------------------
class SendToAuthorityBody(BaseModel):
    authority: Optional[str] = Field(
        None, description="Recipient authority name (e.g., AI Office, DPA)"
    )
    channel: Optional[str] = Field(
        "email", description="Transport channel (email|portal|api)"
    )
    reference: Optional[str] = Field(
        None, description="External reference / tracking id"
    )
    note: Optional[str] = Field(None, description="Optional note")
    sent_at: Optional[str] = Field(
        None, description="ISO timestamp; defaults to now() if missing"
    )
    extra: Optional[Dict[str, Any]] = Field(
        None, description="Any additional structured fields"
    )


@router.post("/documents/{document_id}/send-to-authority", response_model=DocumentOut)
def send_to_authority(
    document_id: int,
    payload: SendToAuthorityBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentOut:
    """
    Records a transmission of this document to a competent authority.
    - Appends a record to metadata_json.transmissions[]
    - AUDIT action: DOC_SENT
    - Returns the updated document
    RBAC: requires system-limited write if system-scoped, otherwise company write.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # RBAC
    if doc.ai_system_id:
        ensure_system_write_limited(db, current_user, int(doc.ai_system_id))
    else:
        if not can_write_company(db, current_user, int(doc.company_id)):
            raise HTTPException(status_code=403, detail="Insufficient privileges")

    # Prepare transmission record
    when = payload.sent_at or (datetime.utcnow().isoformat() + "Z")
    transmission = {
        "authority": payload.authority or "unknown",
        "channel": payload.channel or "email",
        "reference": payload.reference,
        "note": payload.note,
        "sent_at": when,
        "user_id": getattr(current_user, "id", None),
        "extra": payload.extra or {},
    }

    # Merge into metadata_json
    _merge_doc_metadata(doc, {"transmissions": [transmission], "last_sent_at": when})
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=int(doc.company_id),
            user_id=getattr(current_user, "id", None),
            action="DOC_SENT",
            entity_type="document",
            entity_id=int(doc.id),
            meta={
                "ai_system_id": doc.ai_system_id,
                "authority": transmission["authority"],
                "channel": transmission["channel"],
                "reference": transmission["reference"],
                "note": transmission["note"],
                "sent_at": transmission["sent_at"],
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _doc_to_out(doc)
