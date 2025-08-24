from sqlalchemy import Column, Integer, String, Float
from app.db.base import Base

class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, default=0.0)

    ai_system_limit = Column(Integer, default=0)  # AR = 0
    user_limit = Column(Integer, default=1)      # broj korisnika u AR timu
    client_limit = Column(Integer, default=0)    # broj klijenata koje AR smije zastupati
    is_ar_only = Column(Integer, default=0)      # 0/1 kao boolean
