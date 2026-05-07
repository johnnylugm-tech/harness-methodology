"""Tests for constitution/claim_extractor.py — Chinese-pattern claim extraction."""

import pytest
from constitution.claim_extractor import (
    extract_claims,
    _extract_keywords,
    claims_to_dict,
    Claim,
    CLAIM_PATTERNS,
    STOP_WORDS,
)


class TestClaim:
    def test_claim_dataclass(self):
        c = Claim(id="c1", text="test", keywords=["a"], pattern_matched="pat", claim_type="design_decision")
        assert c.id == "c1"
        assert c.text == "test"
        assert c.keywords == ["a"]
        assert c.claim_type == "design_decision"
        assert c.pattern_matched == "pat"

    def test_claim_defaults(self):
        c = Claim(id="c1", text="test")
        assert c.keywords == []
        assert c.pattern_matched == ""
        assert c.claim_type == "unknown"


class TestExtractClaims:
    def test_empty_text(self):
        assert extract_claims("") == []

    def test_short_sentence_skipped(self):
        claims = extract_claims("Hi.")
        assert claims == []

    def test_design_decision_use_strategy(self):
        claims = extract_claims("Implement LRU cache：使用LRU演算法來處理快取淘汰策略。")
        assert len(claims) >= 1
        # Should match design_decision patterns
        design = [c for c in claims if c.claim_type == "design_decision"]
        assert len(design) >= 1

    def test_implementation_through(self):
        claims = extract_claims("透過asyncio來處理非同步請求的並發問題。")
        assert len(claims) >= 1
        impl = [c for c in claims if c.claim_type == "implementation"]
        assert len(impl) >= 1

    def test_reasoning_because(self):
        # \w+ captures ASCII word chars after Chinese keyword
        claims = extract_claims("因為memory限制而選擇cache方案。")
        assert len(claims) >= 1
        reasoning = [c for c in claims if c.claim_type == "reasoning"]
        assert len(reasoning) >= 1

    def test_assumption(self):
        claims = extract_claims("假設network連線穩定成立，使用同步API。")
        assert len(claims) >= 1
        assumption = [c for c in claims if c.claim_type == "assumption"]
        assert len(assumption) >= 1

    def test_claim_has_keywords(self):
        claims = extract_claims("遵循security設計規格，使用AES-256加密所有敏感資料。")
        assert len(claims) >= 1
        assert len(claims[0].keywords) >= 1

    def test_claim_id_increments(self):
        text = "使用LRU演算法來處理快取資料。 採用async策略來提升整體效能。 基於microservices服務架構設計。"
        claims = extract_claims(text)
        ids = [c.id for c in claims]
        assert len(ids) >= 2
        assert all(id_.startswith("claim_") for id_ in ids)

    def test_claim_pattern_matched_recorded(self):
        claims = extract_claims("使用Redis策略，加速讀取效能。")
        assert len(claims) >= 1
        for c in claims:
            assert c.pattern_matched != ""


class TestExtractKeywords:
    def test_removes_stop_words(self):
        kw = _extract_keywords("的 了 是 在 和 系統 LRU")
        assert "的" not in kw
        assert "了" not in kw

    def test_returns_top5(self):
        kw = _extract_keywords("lru cache performance optimization testing benchmark")
        assert len(kw) <= 5

    def test_unique_keywords(self):
        kw = _extract_keywords("lru lru lru cache cache")
        assert kw.count("lru") == 1

    def test_min_length_2(self):
        kw = _extract_keywords("a b c x y z system")
        # Single chars filtered
        assert "a" not in kw
        assert "b" not in kw

    def test_custom_stop_words(self):
        kw = _extract_keywords("特殊一般詞彙", stop_words={"特殊"})
        assert "特殊" not in kw


class TestClaimsToDict:
    def test_empty_list(self):
        assert claims_to_dict([]) == []

    def test_converts_all_fields(self):
        claims = [Claim(id="c1", text="test", keywords=["k"], pattern_matched="p", claim_type="t")]
        result = claims_to_dict(claims)
        assert len(result) == 1
        assert result[0]["id"] == "c1"
        assert result[0]["text"] == "test"
        assert result[0]["keywords"] == ["k"]
        assert result[0]["pattern_matched"] == "p"
        assert result[0]["claim_type"] == "t"


class TestConstants:
    def test_claim_patterns_present(self):
        assert len(CLAIM_PATTERNS) >= 10

    def test_stop_words_not_empty(self):
        assert len(STOP_WORDS) >= 5
        assert "的" in STOP_WORDS
