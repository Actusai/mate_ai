from sqlalchemy import Column, Integer, ForeignKey, DateTime, Boolean
from datetime import datetime
from app.db.base import Base

class CompanyPackage(Base):
    __tablename__ = "company_packages"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

