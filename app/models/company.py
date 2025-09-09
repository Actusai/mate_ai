from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.db.base import Base

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)

    # postojeće kolone u bazi (poravnato na PRAGMA)
    name = Column(String, nullable=False, index=True)
    address = Column(String, nullable=True)
    country = Column(String, nullable=True)

    # napomena: u bazi postoji "email" i "contact_email"
    # preferiramo contact_email, ali ostavljamo i email radi kompatibilnosti
    email = Column(String, nullable=True)              # (legacy / optional)
    contact_email = Column(String, nullable=True)

    contact_phone = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)

    legal_form = Column(String, nullable=True)
    registration_number = Column(String, nullable=True, index=True)
    website = Column(String, nullable=True)
    
    # --- ISPRAVLJENO: Dodana kolona 'company_type' ---
    company_type = Column(String, nullable=True)
    # ------------------------------------------------

    # flag koji već postoji u bazi
    is_authorized_representative = Column(Integer, default=0)

    # timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)