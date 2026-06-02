"""Tests for constitution/citation_parser.py — regex-based citation and claims extraction."""

from constitution.citation_parser import CitationParser


class TestCitationParser:
    def test_extract_citations_fr(self):
        parser = CitationParser()
        citations = parser.extract_citations("See [FR-01] and [NFR-03] for details.")
        assert len(citations) == 2

    def test_extract_citations_sad(self):
        parser = CitationParser()
        citations = parser.extract_citations("Refer to [SAD-003] for architecture.")
        assert len(citations) >= 1

    def test_extract_citations_adr(self):
        parser = CitationParser()
        citations = parser.extract_citations("Decision recorded in [ADR-007].")
        assert len(citations) == 1

    def test_extract_citations_section(self):
        parser = CitationParser()
        citations = parser.extract_citations("See [§4.1] for the implementation notes.")
        assert len(citations) >= 1

    def test_extract_citations_md_file(self):
        parser = CitationParser()
        citations = parser.extract_citations("Config defined in [SAD.md#L42].")
        assert len(citations) >= 1

    def test_extract_citations_task(self):
        parser = CitationParser()
        citations = parser.extract_citations("[TASK-123] assigned to this phase.")
        assert len(citations) == 1

    def test_extract_citations_empty_text(self):
        parser = CitationParser()
        assert parser.extract_citations("") == []

    def test_extract_citations_no_match(self):
        parser = CitationParser()
        assert parser.extract_citations("Plain text without any citation markers.") == []

    def test_extract_claims_must(self):
        parser = CitationParser()
        claims = parser.extract_claims("The system must validate all inputs before processing.")
        assert len(claims) >= 1

    def test_extract_claims_shall(self):
        parser = CitationParser()
        claims = parser.extract_claims("The API shall return 200 on success for all endpoints.")
        assert len(claims) >= 1

    def test_extract_claims_should(self):
        parser = CitationParser()
        claims = parser.extract_claims("Developers should follow the coding standard defined.")
        assert len(claims) >= 1

    def test_extract_claims_verify(self):
        parser = CitationParser()
        claims = parser.extract_claims("This test verifies the login flow correctness.")
        assert len(claims) >= 1

    def test_extract_claims_guarantee(self):
        parser = CitationParser()
        claims = parser.extract_claims("The protocol guarantees at-most-once delivery semantics.")
        assert len(claims) >= 1

    def test_extract_claims_empty(self):
        parser = CitationParser()
        assert parser.extract_claims("") == []

    def test_extract_claims_no_match(self):
        parser = CitationParser()
        assert parser.extract_claims("Regular descriptive text without modal verbs.") == []

    def test_verify_claim_no_citations(self):
        parser = CitationParser()
        assert parser.verify_claim("must do something", []) is False

    def test_verify_claim_traceable_keyword(self):
        parser = CitationParser()
        assert parser.verify_claim("must comply with requirement", ["[FR-01]"]) is True

    def test_verify_claim_sad_keyword(self):
        parser = CitationParser()
        assert parser.verify_claim("shall follow the SAD design", ["[SAD.md]"]) is True

    def test_verify_claim_no_traceable_but_has_citations(self):
        parser = CitationParser()
        assert parser.verify_claim("just a statement", ["[FR-01]"]) is True

    def test_verify_claim_fr_keyword(self):
        parser = CitationParser()
        assert parser.verify_claim("the FR-01 test coverage was checked", ["[FR-01]"]) is True
