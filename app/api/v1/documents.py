# app/api/v1/documents.py
from __future__ import annotations

import io
import os
import json
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import and_, text, func

from app.core.auth import get_db, get_current_user
from app.core.scoping import can_read_company, can_write_company, is_super
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

router = APIRouter()


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
        like_pattern = f'%\"document_id\":{int(d.id)}%'
        row = db.execute(
            text(
                """
                SELECT 1
                  FROM notifications
                 WHERE type = 'doc_review_due'
                   AND company_id = :cid
                   AND ai_system_id IS :aid_is_null OR ai_system_id = :aid
                   AND payload LIKE :pattern
                   AND created_at >= datetime('now', :since)
                 LIMIT 1
                """
            ),
            {
                "cid": int(d.company_id),
                "aid_is_null": None if d.ai_system_id is None else None,  # keep SQL happy
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
                "ai_system_id": int(d.ai_system_id) if d.ai_system_id is not None else None,
                "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
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

    rows = db.execute(
        text(
            f"""
            SELECT id FROM notifications
            WHERE {where}
            """
        ),
        params,
    ).mappings().all()

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
@router.post("/documents/packs", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
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
        raise HTTPException(status_code=404, detail="No matching documents found to pack")

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
    folder = "/tmp/doc_packs"
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
            (Document.ai_system_id == system.id) & (Document.company_id == system.company_id)
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