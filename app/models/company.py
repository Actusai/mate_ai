from sqlalchemy import Column, Integer, String
from app.db.base import Base

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    address = Column(String)
    country = Column(String)
    email = Column(String, unique=True)
