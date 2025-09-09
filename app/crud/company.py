# app/crud/company.py
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text, delete

from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyUpdate

# Dodani importi za ručno kaskadno brisanje
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.ai_assessment import AIAssessment
from app.models.admin_assignment import AdminAssignment
from app.models.system_assignment import SystemAssignment
from app.models.company_package import CompanyPackage
from app.models.compliance_task import ComplianceTask
from app.models.invite import Invite
from app.models.password_reset import PasswordReset


# --- helpers -----------------------------------------------------------------

def _normalize_company_type(val: Optional[str]) -> str:
    """
    Normalize to one of: 'authorized_representative' | 'deployer' | 'developer'
    Default: 'authorized_representative' (kako ste zadali u schemi).
    """
    if not val:
        return "authorized_representative"
    v = val.strip().lower()
    allowed = {"authorized_representative", "deployer", "developer"}
    return v if v in allowed else "authorized_representative"


def _derive_is_ar_flag(company_type: str, explicit_flag: Optional[bool]) -> int:
    """
    Zadržavamo kompatibilnost sa starim boolean poljem is_authorized_representative (INT u bazi).
    True ako je company_type = authorized_representative ili je flag eksplicitno True.
    """
    return 1 if (company_type == "authorized_representative" or explicit_flag) else 0


def _enable_sqlite_fk_if_needed(db: Session) -> None:
    """
    SQLite traži PRAGMA foreign_keys = ON per-connection za kaskadno brisanje.
    Nije skoditi osigurati (no-op na drugim bazama).
    """
    try:
        if db.bind and db.bind.dialect.name == "sqlite":
            db.execute(text("PRAGMA foreign_keys = ON"))
    except Exception:
        # Ako ne uspije, ne rušimo — neki poolovi već imaju uključeno.
        pass


# --- CRUD --------------------------------------------------------------------

def get_company(db: Session, company_id: int) -> Optional[Company]:
    return db.get(Company, company_id)


def list_companies(db: Session, skip: int = 0, limit: int = 50) -> List[Company]:
    return (
        db.query(Company)
        .order_by(Company.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_company(db: Session, data: CompanyCreate) -> Company:
    company_type = _normalize_company_type(data.company_type)
    is_ar_int = _derive_is_ar_flag(company_type, data.is_authorized_representative)

    obj = Company(
        name=data.name,
        address=data.address,
        country=data.country,
        legal_form=data.legal_form,
        registration_number=data.registration_number,
        website=data.website,
        # Preferiramo contact_email; fields ostavljamo radi kompatibilnosti
        email=getattr(data, "contact_email", None),
        contact_email=data.contact_email,
        contact_phone=data.contact_phone,
        contact_person=data.contact_person,
        company_type=company_type,
        is_authorized_representative=is_ar_int,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_company(db: Session, obj: Company, data: CompanyUpdate) -> Company:
    # Osvježavamo samo ono što je došlo (None -> skip)
    if data.name is not None:
        obj.name = data.name
    if data.address is not None:
        obj.address = data.address
    if data.country is not None:
        obj.country = data.country
    if data.legal_form is not None:
        obj.legal_form = data.legal_form
    if data.registration_number is not None:
        obj.registration_number = data.registration_number
    if data.website is not None:
        obj.website = data.website
    if data.contact_email is not None:
            obj.contact_email = data.contact_email
            # legacy email polje poravnavamo ako ga koristite negdje u UI-u
            obj.email = data.contact_email
    if data.contact_phone is not None:
        obj.contact_phone = data.contact_phone
    if data.contact_person is not None:
        obj.contact_person = data.contact_person

    # Ako je došao company_type ili is_authorized_representative, uskladiti obje vrijednosti
    new_company_type = None
    if data.company_type is not None:
        new_company_type = _normalize_company_type(data.company_type)
        obj.company_type = new_company_type

    if data.is_authorized_representative is not None or new_company_type is not None:
        # derivirajmo ponovno AR flag
        effective_type = new_company_type if new_company_type is not None else (obj.company_type or "")
        obj.is_authorized_representative = _derive_is_ar_flag(
            effective_type,
            data.is_authorized_representative
        )

    db.commit()
    db.refresh(obj)
    return obj


def delete_company(db: Session, company: Company) -> None:
    cid = company.id

    # 1) Skupi sve ai_system_id za ovu kompaniju
    system_ids = [sid for (sid,) in db.query(AISystem.id).filter(AISystem.company_id == cid).all()]

    # 2) Briši “najdublje” tablice koje ovise o sustavima / kompaniji
    if system_ids:
        # assessments vezani na sustave ili direktno na company_id
        db.query(AIAssessment).filter(AIAssessment.ai_system_id.in_(system_ids)).delete(synchronize_session=False)
    db.query(AIAssessment).filter(AIAssessment.company_id == cid).delete(synchronize_session=False)

    # assignments (ako SystemAssignment nema company_id, briši preko system_ids)
    try:
        # Ako model IMA company_id kolonu:
        db.query(SystemAssignment).filter(SystemAssignment.company_id == cid).delete(synchronize_session=False)
    except Exception:
        # Ako NEMA company_id: briši prema ai_system_id
        if system_ids:
            db.query(SystemAssignment).filter(SystemAssignment.ai_system_id.in_(system_ids)).delete(synchronize_session=False)

    # compliance tasks (ako ih imaš)
    try:
        db.query(ComplianceTask).filter(ComplianceTask.company_id == cid).delete(synchronize_session=False)
    except Exception:
        pass

    # admin assignments
    db.query(AdminAssignment).filter(AdminAssignment.company_id == cid).delete(synchronize_session=False)

    # company_packages
    db.query(CompanyPackage).filter(CompanyPackage.company_id == cid).delete(synchronize_session=False)

    # invites (ako postoji)
    try:
        db.query(Invite).filter(Invite.company_id == cid).delete(synchronize_session=False)
    except Exception:
        pass

    # users (svi korisnici u toj kompaniji)
    # Moguće je da PasswordReset ovisi o User-u, pa se ovo mora obrisati prije Usera
    db.query(PasswordReset).filter(PasswordReset.user_id.in_([u.id for u in db.query(User).filter(User.company_id == cid).all()])).delete(synchronize_session=False)
    db.query(User).filter(User.company_id == cid).delete(synchronize_session=False)

    # ai_systems (na kraju djeca sustava)
    db.query(AISystem).filter(AISystem.company_id == cid).delete(synchronize_session=False)

    # 3) Konačno obriši company
    db.query(Company).filter(Company.id == cid).delete(synchronize_session=False)

    db.commit()