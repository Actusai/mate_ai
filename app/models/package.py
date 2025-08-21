from sqlalchemy import Column, Integer, String
from app.db.base import Base

class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    price = Column(Integer)
    ai_system_limit = Column(Integer)
    user_limit = Column(Integer)
