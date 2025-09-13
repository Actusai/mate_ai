# app/schemas/dashboard.py
from typing import Dict, List, Optional
from pydantic import BaseModel


class RiskBucket(BaseModel):
    prohibited: int = 0
    high_risk: int = 0
    limited_risk: int = 0
    minimal_risk: int = 0
    unknown: int = 0  # ako je risk_tier None/prazno


class CompanyMini(BaseModel):
    id: int
    name: str


class SystemMini(BaseModel):
    id: int
    name: str
    company_id: int
    company_name: Optional[str] = None
    risk_tier: Optional[str] = None
    status: Optional[str] = None
    lifecycle_stage: Optional[str] = None


class DashboardSummary(BaseModel):
    # globalno ili skupljeno po scopu
    scope: str  # "global" | "assigned_companies" | "company" | "me"
    companies_count: int
    ai_systems_count: int
    contributors_count: int
    risk_distribution: RiskBucket

    # opcionalno: brzi uvidi
    companies: Optional[List[CompanyMini]] = None
    systems_sample: Optional[List[SystemMini]] = None
