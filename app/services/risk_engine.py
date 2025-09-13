# app/services/risk_engine.py
from typing import Any, Dict, List, Tuple

# -------- Prohibited (Art. 5) --------
PROHIBITED_KEYS = {
    "subliminal_techniques",
    "exploits_vulnerabilities",
    "social_scoring_public_authorities",
    "real_time_remote_biometric_id_in_public_for_law_enforcement",
}

# -------- High-risk (Annex III) --------
# Dopunjeno: education_vocational_training, essential_private_services,
# biometric_categorisation (kada NIJE zabranjeno), insurance_eligibility, border_control_ai_assist
HIGH_RISK_FLAGS = {
    "critical_infrastructure",
    "employment_hr",
    "education",  # općenito obrazovanje
    "education_vocational_training",  # strukovno osposobljavanje
    "law_enforcement",
    "migration_asylum_border",
    "border_control_ai_assist",
    "justice_democratic_processes",
    "medical_device_or_care",
    "biometric_identification_post",
    "biometric_categorisation",
    "credit_scoring_or_access_to_essential_services",
    "essential_private_services",
    "insurance_eligibility",
}

# -------- Limited-risk (Art. 52) --------
LIMITED_RISK_FLAGS = {
    "content_generation_or_chatbot",
    "deepfake_or_synthetic_media",
    "emotion_recognition_non_le",
}

# -------- Reference map (lagano orijentacijski članci/prilozi) --------
FLAG_REFS: Dict[str, List[str]] = {
    # Prohibited (Art. 5(1))
    "subliminal_techniques": ["Art. 5(1)(a)"],
    "exploits_vulnerabilities": ["Art. 5(1)(b)"],
    "social_scoring_public_authorities": ["Art. 5(1)(c)"],
    "real_time_remote_biometric_id_in_public_for_law_enforcement": ["Art. 5(1)(d)"],
    # High-risk (Annex III – indikativno po točkama)
    "critical_infrastructure": ["Annex III"],
    "employment_hr": ["Annex III"],
    "education": ["Annex III"],
    "education_vocational_training": ["Annex III"],
    "law_enforcement": ["Annex III"],
    "migration_asylum_border": ["Annex III"],
    "border_control_ai_assist": ["Annex III"],
    "justice_democratic_processes": ["Annex III"],
    "medical_device_or_care": ["Annex III"],
    "biometric_identification_post": ["Annex III"],
    "biometric_categorisation": ["Annex III"],
    "credit_scoring_or_access_to_essential_services": ["Annex III"],
    "essential_private_services": ["Annex III"],
    "insurance_eligibility": ["Annex III"],
    # Limited (Art. 52)
    "content_generation_or_chatbot": ["Art. 52"],
    "deepfake_or_synthetic_media": ["Art. 52"],
    "emotion_recognition_non_le": ["Art. 52"],
}


def _collect_true_keys(answers: Dict[str, Any], keys: List[str]) -> List[str]:
    return [k for k in keys if bool(answers.get(k)) is True]


def _prohibited(answers: Dict[str, Any]) -> Tuple[bool, List[str]]:
    hits = _collect_true_keys(answers, list(PROHIBITED_KEYS))
    return (len(hits) > 0, hits)


def _high_risk(answers: Dict[str, Any]) -> Tuple[bool, List[str]]:
    hits = _collect_true_keys(answers, list(HIGH_RISK_FLAGS))
    return (len(hits) > 0, hits)


def _limited_risk(answers: Dict[str, Any]) -> Tuple[bool, List[str]]:
    hits = _collect_true_keys(answers, list(LIMITED_RISK_FLAGS))
    return (len(hits) > 0, hits)


def _obligations_for_tier(tier: str, answers: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Vraća mapu kategorija -> lista obveza s kratkim referencama na AI Act.
    Uvijek vraća i ključ 'situational' (može biti prazan).
    """
    tier = tier.lower()

    # situacijske obveze (dodaju se kontekstualno)
    situational: List[str] = []

    # Provider izvan EU -> ovlašteni predstavnik i EU kontakt
    if answers.get("providers_outside_eu") is True:
        situational.append(
            "Appoint EU Authorised Representative (provider outside the EU)."
        )
        situational.append("Provide EU contact in notices / documentation.")

    if tier == "prohibited":
        return {
            "core": [
                "Prohibited use — system must not be placed on the EU market or put into service. (Art. 5)"
            ],
            "situational": situational,
        }

    if tier == "high_risk":
        return {
            "core": [
                "Risk management system. (Art. 9)",
                "Data governance and data quality. (Art. 10)",
                "Technical documentation & logging. (Annex IV; Art. 11)",
                "Transparency & instructions for use. (Art. 13)",
                "Human oversight. (Art. 14)",
                "Accuracy, robustness, cybersecurity. (Art. 15)",
                "Conformity assessment & CE marking. (Art. 19–20)",
                "Registration where applicable. (Title VIII)",
                "Post-market monitoring & incident reporting. (Art. 61)",
            ],
            "situational": situational,
        }

    if tier == "limited_risk":
        return {
            "core": [
                "Transparency to users (clear information). (Art. 52)",
                "Label deepfakes/synthetic media where applicable. (Art. 52)",
            ],
            "situational": situational,
        }

    # minimal_risk
    return {"core": [], "situational": situational}


def _tier_references(tier: str) -> List[str]:
    tier = tier.lower()
    if tier == "prohibited":
        return ["Art. 5"]
    if tier == "high_risk":
        return ["Art. 9–15", "Annex III", "Annex IV", "Art. 19–20", "Art. 61"]
    if tier == "limited_risk":
        return ["Art. 52"]
    return []


def _flag_references(flags: List[str]) -> List[str]:
    refs: List[str] = []
    for f in flags:
        refs.extend(FLAG_REFS.get(f, []))
    # uniq + stabilan poredak
    seen = set()
    dedup: List[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            dedup.append(r)
    return dedup


def classify_ai_system(answers: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ulaz: dict s boolean poljima (flat).
    Izlaz:
      {
        "risk_tier": "prohibited" | "high_risk" | "limited_risk" | "minimal_risk" | "out_of_scope",
        "obligations": { "core": [...], "situational": [...] },
        "rationale": [ ... ],
        "references": [ "Art. ..." ]
      }
    Napomena: Prohibited ima prioritet; zatim high_risk; zatim limited_risk; inače minimal_risk.
    Ako 'is_ai_system' == False -> out_of_scope.
    """

    # 0) Scope check – je li uopće "AI system" po čl. 3?
    if answers.get("is_ai_system") is False:
        return {
            "risk_tier": "out_of_scope",
            "obligations": {"core": [], "situational": []},
            "rationale": [
                "Out of scope: does not meet definition of 'AI system' (Art. 3)."
            ],
            "references": ["Art. 3"],
        }

    # 1) Prohibited
    is_proh, proh_hits = _prohibited(answers)
    if is_proh:
        tier = "prohibited"
        return {
            "risk_tier": tier,
            "obligations": _obligations_for_tier(tier, answers),
            "rationale": [f"Prohibited criteria matched: {', '.join(proh_hits)}"],
            "references": _tier_references(tier) + _flag_references(proh_hits),
        }

    # 2) High risk (Annex III)
    is_high, high_hits = _high_risk(answers)
    if is_high:
        tier = "high_risk"
        return {
            "risk_tier": tier,
            "obligations": _obligations_for_tier(tier, answers),
            "rationale": [f"High-risk criteria matched: {', '.join(high_hits)}"],
            "references": _tier_references(tier) + _flag_references(high_hits),
        }

    # 3) Limited risk (Art. 52)
    is_limited, lim_hits = _limited_risk(answers)
    if is_limited:
        tier = "limited_risk"
        return {
            "risk_tier": tier,
            "obligations": _obligations_for_tier(tier, answers),
            "rationale": [f"Limited-risk criteria matched: {', '.join(lim_hits)}"],
            "references": _tier_references(tier) + _flag_references(lim_hits),
        }

    # 4) Minimal
    tier = "minimal_risk"
    return {
        "risk_tier": tier,
        "obligations": _obligations_for_tier(tier, answers),
        "rationale": ["No prohibited/high/limited flags matched."],
        "references": [],
    }


# ---------------------------------------------------------------------------
# (Opcionalno, ali korisno) helper za “Effective Risk”
# Ne mijenja DB; možeš ga koristiti u UI-ju ili odgovoru API-ja.
# ---------------------------------------------------------------------------
def calculate_effective_risk(risk_tier: str, compliance_status: str) -> str:
    r = (risk_tier or "").lower()
    c = (compliance_status or "").lower()

    if r in {"prohibited"}:
        return "prohibited"

    if r in {"high_risk", "high-risk"}:
        if c == "compliant":
            return "controlled_high_risk"
        if c == "partially_compliant":
            return "elevated_high_risk"
        return "critical_risk"  # non_compliant

    if r in {"limited_risk", "limited-risk"}:
        if c == "compliant":
            return "limited_risk"
        if c == "partially_compliant":
            return "elevated_limited_risk"
        return "formal_breach_low_impact"

    # minimal_risk i ostalo
    if c == "compliant":
        return "minimal_risk"
    if c == "partially_compliant":
        return "elevated_minimal_risk"
    return "formal_breach_low_impact"
