from datetime import date

from civenthra.rag import Civenthra
from civenthra.safety import assess, classify


def review(*domains, valid_until="2027-01-01"):
    return {
        "status": "approved_current",
        "domains": list(domains),
        "reviewed_by": "qualified reviewer",
        "reviewed_at": "2026-10-01",
        "valid_until": valid_until,
        "evidence_uri": "local-review://example",
    }


def test_risk_classifier_covers_operational_domains():
    assert classify("伤口如何止血") == ["medical"]
    assert set(classify("How do I sanitize drinking water for wilderness survival?")) == {
        "water_sanitation", "survival"
    }
    assert classify("一分钟社会学是什么？") == []


def test_high_risk_material_without_review_is_blocked():
    decision = assess(
        "伤口如何止血", [{"text": "historical procedure"}],
        today=date(2026, 11, 4),
    )
    assert decision["high_risk"]
    assert not decision["allowed"]
    assert decision["passages"] == []
    assert "不会给出可直接执行的步骤" in decision["notice"]


def test_expired_review_is_blocked():
    decision = assess(
        "food preservation and botulism",
        [{"text": "old", "safety_review": review("food", valid_until="2026-11-03")}],
        language="en", today=date(2026, 11, 4),
    )
    assert not decision["allowed"]


def test_every_detected_domain_requires_current_review():
    decision = assess(
        "How do I treat poisoning and purify drinking water?",
        [{"text": "medical", "safety_review": review("medical")}],
        language="en", today=date(2026, 11, 4),
    )
    assert set(decision["categories"]) == {"medical", "water_sanitation"}
    assert not decision["allowed"]


def test_only_reviewed_passages_reach_high_risk_answer():
    approved = {"text": "current", "safety_review": review("electrical")}
    unreviewed = {"text": "historical"}
    decision = assess(
        "electrical wiring", [approved, unreviewed],
        language="en", today=date(2026, 11, 4),
    )
    assert decision["allowed"]
    assert decision["passages"] == [approved]


def test_emergency_message_is_deterministic():
    decision = assess("正在流血，怎么止血", [], today=date(2026, 11, 4))
    assert not decision["allowed"]
    assert "立即联系当地紧急救援服务" in decision["notice"]


class _NeverCalledServer:
    def chat(self, *args, **kwargs):
        raise AssertionError("model must not be called for blocked high-risk requests")

    def stream(self, *args, **kwargs):
        raise AssertionError("model must not be called for blocked high-risk requests")


def blocked_civenthra():
    instance = object.__new__(Civenthra)
    instance.server = _NeverCalledServer()
    instance.retrieve = lambda _question: [{"text": "unreviewed historical advice"}]
    return instance


def test_blocked_answer_bypasses_model_and_citations():
    result = blocked_civenthra().ask("药物剂量是多少", language="zh-CN")
    assert not result["safety"]["allowed"]
    assert result["citations"] == []
    assert result["passages"] == []


def test_blocked_stream_bypasses_model():
    events = list(blocked_civenthra().stream_ask("How to treat poisoning?", language="en"))
    assert events[0] == {"type": "passages", "passages": []}
    assert "high-risk operational question" in events[1]["text"]
    assert events[-1] == {"type": "done"}
