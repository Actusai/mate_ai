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
# Dopunjeno prema prijedlogu: dodani education_vocational_training, essential_private_services,
# biometric_categorisation (kada NIJE zabranjeno), insurance_eligibility, border_control_ai_assist
HIGH_RISK_FLAGS = {
    "critical_infrastructure",
    "employment_hr",
    "education",  # općenito obrazovanje
    "education_vocational_training",  # posebno strukovno osposobljavanje
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
    """
    tier = tier.lower()

    # situacijske obveze (popunit ćemo dinamički)
    situational: List[str] = []

    # ako je provider izvan EU -> ovlašteni predstavnik i EU kontakt
    if answers.get("providers_outside_eu") is True:
        situational.append("Appoint EU Authorised Representative (provider outside EU) [Ch. 3 duties].")
        situational.append("Provide EU contact in user notices / documentation.")

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

def _references_for_tier(tier: str) -> List[str]:
    if tier == "prohibited":
        return ["Art. 5"]
    if tier == "high_risk":
        return ["Art. 9–15", "Annex III", "Annex IV", "Art. 19–20", "Art. 61"]
    if tier == "limited_risk":
        return ["Art. 52"]
    return []

def classify_ai_system(answers: Dict[str, Any]) -> Dict[str, Any]:
    """
    Vraća:
      {
        "risk_tier": "prohibited" | "high_risk" | "limited_risk" | "minimal_risk" | "out_of_scope",
        "obligations": { "core": [...], "situational": [...] },
        "rationale": [ ... ],
        "references": [ "Art. ..." ]
      }
    """

    # 0) Scope check – je li uopće "AI system" po čl. 3?
    # (default True da ne rušimo stare pozive; postavi False ako želite izbaciti iz opsega)
    if answers.get("is_ai_system") is False:
        return {
            "risk_tier": "out_of_scope",
            "obligations": {"core": [], "situational": []},
            "rationale": ["Out of scope: does not meet definition of 'AI system' (Art. 3)."],
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
            "references": _references_for_tier(tier),
        }

    # 2) High risk (Annex III)
    is_high, high_hits = _high_risk(answers)
    if is_high:
        tier = "high_risk"
        return {
            "risk_tier": tier,
            "obligations": _obligations_for_tier(tier, answers),
            "rationale": [f"High-risk criteria matched: {', '.join(high_hits)}"],
            "references": _references_for_tier(tier),
        }

    # 3) Limited risk (Art. 52)
    is_limited, lim_hits = _limited_risk(answers)
    if is_limited:
        tier = "limited_risk"
        return {
            "risk_tier": tier,
            "obligations": _obligations_for_tier(tier, answers),
            "rationale": [f"Limited-risk criteria matched: {', '.join(lim_hits)}"],
            "references": _references_for_tier(tier),
        }

    # 4) Minimal
    tier = "minimal_risk"
    return {
        "risk_tier": tier,
        "obligations": _obligations_for_tier(tier, answers),
        "rationale": ["No prohibited/high/limited flags matched."],
        "references": [],
    }