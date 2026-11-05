"""Fail-closed policy for operational medical, food, engineering and survival advice."""
from __future__ import annotations

from datetime import date
from typing import Iterable

from .i18n import normalize_locale

RISK_KEYWORDS = {
    "medical": (
        "急救", "止血", "伤口", "骨折", "中毒", "药物", "剂量", "诊断", "治疗",
        "first aid", "bleeding", "fracture", "poison", "medicine", "dosage", "diagnose", "treat",
    ),
    "food": (
        "罐藏", "腌制", "食物保存", "肉毒", "食品安全",
        "canning", "food preservation", "botulism", "food safety",
    ),
    "water_sanitation": (
        "饮用水", "净水", "污水", "消毒饮水", "卫生工程",
        "drinking water", "water purification", "sewage", "sanitize water",
    ),
    "electrical": (
        "触电", "高压电", "接线", "配电", "电路维修",
        "electric shock", "high voltage", "electrical wiring", "breaker panel",
    ),
    "structural": (
        "承重", "结构安全", "桥梁施工", "房屋加固", "材料强度计算",
        "load bearing", "structural safety", "bridge construction", "building reinforcement",
    ),
    "survival": (
        "野外求生", "火星生存", "密闭舱生存", "制氧", "应急避难",
        "wilderness survival", "mars survival", "sealed habitat", "oxygen generation", "emergency shelter",
    ),
}

EMERGENCY_KEYWORDS = (
    "正在流血", "呼吸困难", "失去意识", "严重中毒", "着火了", "触电了", "坍塌",
    "bleeding now", "can't breathe", "cannot breathe", "unconscious", "poisoned now",
    "on fire", "electrocuted", "collapse",
)


def classify(question: str) -> list[str]:
    text = (question or "").casefold()
    return [category for category, keywords in RISK_KEYWORDS.items()
            if any(keyword.casefold() in text for keyword in keywords)]


def _valid_review(review: object, category: str, today: date) -> bool:
    if not isinstance(review, dict) or review.get("status") != "approved_current":
        return False
    domains = review.get("domains")
    if not isinstance(domains, list) or category not in domains:
        return False
    if any(not review.get(key) for key in ("reviewed_by", "reviewed_at", "valid_until", "evidence_uri")):
        return False
    try:
        reviewed_at = date.fromisoformat(review["reviewed_at"])
        valid_until = date.fromisoformat(review["valid_until"])
    except (TypeError, ValueError):
        return False
    return reviewed_at <= today <= valid_until


def assess(question: str, passages: Iterable[dict], language: str = "auto",
           today: date | None = None) -> dict:
    locale = normalize_locale(language, question)
    categories = classify(question)
    rows = list(passages)
    if not categories:
        return {
            "high_risk": False, "allowed": True, "categories": [],
            "passages": rows, "reason": "not_high_risk", "notice": "",
        }

    current = today or date.today()
    approved = [row for row in rows if any(
        _valid_review(row.get("safety_review"), category, current)
        for category in categories
    )]
    covered = all(any(
        _valid_review(row.get("safety_review"), category, current)
        for row in approved
    ) for category in categories)
    emergency = any(keyword.casefold() in (question or "").casefold()
                    for keyword in EMERGENCY_KEYWORDS)
    if not covered:
        if locale == "en":
            notice = (
                "This is a high-risk operational question. The retrieved material has no "
                "current, documented professional review for every relevant domain, so "
                "Civenthra will not provide executable steps. Use current local standards "
                "and a qualified professional. Historical sources are research material only."
            )
            if emergency:
                notice += " Contact local emergency services now; do not wait for this system."
        else:
            notice = (
                "这是高风险操作问题。当前检索材料没有覆盖全部相关领域且仍在有效期内的专业审核记录，"
                "因此文明穹顶不会给出可直接执行的步骤。请采用当地现行规范并联系合格专业人员；"
                "历史材料只能用于研究。"
            )
            if emergency:
                notice += "如正处于紧急危险，请立即联系当地紧急救援服务，不要等待本系统。"
        return {
            "high_risk": True, "allowed": False, "categories": categories,
            "passages": [], "reason": "missing_current_expert_review", "notice": notice,
        }

    notice = (
        "High-risk answer: only currently reviewed passages were supplied; verify local rules before acting."
        if locale == "en" else
        "高风险回答：仅使用仍在审核有效期内的材料；执行前仍须核对当地现行规范。"
    )
    return {
        "high_risk": True, "allowed": True, "categories": categories,
        "passages": approved, "reason": "current_expert_review_present", "notice": notice,
    }
