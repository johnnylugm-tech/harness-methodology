"""Round 20 站1 — env-check readiness must be computed, not asserted.

The defect this pins, from Round 24 (37adc43)'s own commit message: the same
documented env var, against the same unchanged project state, was classified
optional_missing (ready=true) in one workflow run and required+present:false
(ready=false, a false FAIL) in another. Three layers read that verdict — the
agent's self-report, run-env-check's exit code, and the workflow JS
"anti-fabrication" cross-check — and all three read the SAME field, so none of
them could contradict it.

Splitting the question is what makes it checkable: classification (what does
this project need? — decided by its docs, stored in env_contract.json) and
verification (is it there right now? — measured every run).
"""
from __future__ import annotations

import inspect
import json

import pytest

from core.quality_gate import env_contract as ec


def _contract(mandatory=(), has_default=(), tools=(), fingerprint="fp"):
    return {
        "schema_version": ec.SCHEMA_VERSION,
        "source_sha256": fingerprint,
        "classified_at": "2026-07-27T00:00:00Z",
        "enforcer_sha": "deadbeef",
        "env_vars": {
            ec.MANDATORY: list(mandatory),
            ec.HAS_DEFAULT: list(has_default),
            ec.DEV_OPT_IN: [],
        },
        "cli_tools": list(tools),
        "infra_services": {},
    }


class TestFingerprint:
    def test_same_documents_same_fingerprint(self):
        a = ec.compute_source_fingerprint("SAD", "SRS", "COMPOSE")
        b = ec.compute_source_fingerprint("SAD", "SRS", "COMPOSE")
        assert a == b

    def test_any_document_change_changes_the_fingerprint(self):
        base = ec.compute_source_fingerprint("SAD", "SRS", "COMPOSE")
        assert ec.compute_source_fingerprint("SAD!", "SRS", "COMPOSE") != base
        assert ec.compute_source_fingerprint("SAD", "SRS!", "COMPOSE") != base
        assert ec.compute_source_fingerprint("SAD", "SRS", "COMPOSE!") != base

    def test_boundaries_between_documents_are_unambiguous(self):
        """Concatenation without a separator would make ("ab","c") and
        ("a","bc") the same project state. They are not."""
        assert ec.compute_source_fingerprint("ab", "c", "") != \
            ec.compute_source_fingerprint("a", "bc", "")

    def test_fingerprint_covers_every_prompt_document(self):
        """The fingerprint's inputs must be exactly the documents the
        classification sub-agent is shown. If evaluation_prompt() grows a
        fourth source, compute_source_fingerprint must grow with it or stored
        classifications go silently stale against that source."""
        from harness.harness_bridge import EnvCheckContext
        prompt_src = inspect.getsource(EnvCheckContext.evaluation_prompt)
        shown = {
            name for name in ("sad_excerpt", "srs_excerpt", "docker_compose_excerpt")
            if f"self.{name}" in prompt_src
        }
        fingerprint_params = set(
            inspect.signature(ec.compute_source_fingerprint).parameters
        )
        assert shown <= fingerprint_params, (
            f"evaluation_prompt() shows the classifier {sorted(shown)}, but "
            f"compute_source_fingerprint only hashes {sorted(fingerprint_params)} "
            f"— a document it is judged on is not part of the staleness check"
        )


class TestContractCurrency:
    def test_absent_contract_is_not_current(self, tmp_path):
        assert ec.load_contract(tmp_path) is None
        assert not ec.contract_is_current(None, "fp")

    def test_matching_fingerprint_is_current(self):
        assert ec.contract_is_current(_contract(fingerprint="fp"), "fp")

    def test_changed_source_invalidates(self):
        assert not ec.contract_is_current(_contract(fingerprint="old"), "new")

    def test_older_schema_version_invalidates(self):
        stale = _contract(fingerprint="fp")
        stale["schema_version"] = ec.SCHEMA_VERSION - 1
        assert not ec.contract_is_current(stale, "fp")

    def test_corrupt_contract_reads_as_absent_not_raises(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        ec.contract_path(tmp_path).write_text("{not json", encoding="utf-8")
        assert ec.load_contract(tmp_path) is None

    def test_roundtrip_through_disk(self, tmp_path):
        written = _contract(mandatory=["A"], fingerprint="fp")
        ec.write_contract(tmp_path, written)
        assert ec.load_contract(tmp_path) == written


class TestDerivation:
    def test_measurements_are_not_stored(self):
        """`present`, `ready` and `checked_at` describe one moment's environment.
        Storing them would make the contract stale the instant it is written."""
        derived = ec.derive_contract_from_result(
            {
                "ready": True,
                "checked_at": "2026-07-27T00:00:00Z",
                "env_vars": {"required": [{"name": "A", "present": True}],
                             "optional_missing": ["B"]},
                "cli_tools": {"required": [{"name": "python3", "present": True}]},
            },
            "fp",
        )
        blob = json.dumps(derived)
        assert '"present"' not in blob
        assert '"ready"' not in blob
        assert derived["env_vars"][ec.MANDATORY] == ["A"]
        assert derived["env_vars"][ec.HAS_DEFAULT] == ["B"]
        assert derived["cli_tools"] == ["python3"]

    def test_derivation_is_stable_for_the_same_result(self):
        result = {"env_vars": {"required": [{"name": "B"}, {"name": "A"}]}}
        one = ec.derive_contract_from_result(result, "fp")
        two = ec.derive_contract_from_result(result, "fp")
        assert one["env_vars"][ec.MANDATORY] == two["env_vars"][ec.MANDATORY] == ["A", "B"]


class TestEvaluation:
    def test_empty_contract_is_ready(self, tmp_path):
        assert ec.evaluate_contract(_contract(), tmp_path)["ready"] is True

    def test_unset_mandatory_var_blocks(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HARNESS_R20_UNSET", raising=False)
        out = ec.evaluate_contract(_contract(mandatory=["HARNESS_R20_UNSET"]), tmp_path)
        assert out["ready"] is False
        assert "HARNESS_R20_UNSET" in out["summary"]

    def test_set_mandatory_var_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_R20_SET", "1")
        assert ec.evaluate_contract(_contract(mandatory=["HARNESS_R20_SET"]), tmp_path)["ready"]

    def test_absent_optional_var_does_not_block(self, tmp_path, monkeypatch):
        """A documented default or a dev-only opt-in flag being unset IS the
        intended state — this is exactly the shape 37adc43 misclassified into a
        false FAIL, and it must never gate readiness again."""
        monkeypatch.delenv("HARNESS_R20_OPTIONAL", raising=False)
        out = ec.evaluate_contract(
            _contract(has_default=["HARNESS_R20_OPTIONAL"]), tmp_path
        )
        assert out["ready"] is True
        assert "HARNESS_R20_OPTIONAL" in out["env_vars"]["optional_missing"]

    def test_missing_cli_tool_blocks(self, tmp_path):
        out = ec.evaluate_contract(
            _contract(tools=["definitely_not_a_real_tool_xyz"]), tmp_path
        )
        assert out["ready"] is False

    def test_same_contract_same_environment_is_deterministic(self, tmp_path, monkeypatch):
        """The property whose absence produced Round 24's contradiction: two
        evaluations of one contract against one environment must agree. Only
        `checked_at` may differ."""
        monkeypatch.setenv("HARNESS_R20_DET", "1")
        monkeypatch.delenv("HARNESS_R20_DET_MISSING", raising=False)
        c = _contract(
            mandatory=["HARNESS_R20_DET"],
            has_default=["HARNESS_R20_DET_MISSING"],
            tools=["python3"],
        )
        first = ec.evaluate_contract(c, tmp_path)
        second = ec.evaluate_contract(c, tmp_path)
        first.pop("checked_at")
        second.pop("checked_at")
        assert first == second

    def test_result_keeps_the_published_schema_shape(self, tmp_path):
        """finalize-env-check and the workflow JS cross-check read this file;
        computing `ready` must not change its shape."""
        out = ec.evaluate_contract(_contract(mandatory=[], tools=[]), tmp_path)
        for key in ("ready", "checked_at", "env_vars", "cli_tools",
                    "infra_services", "summary"):
            assert key in out, f"evaluate_contract dropped `{key}` from the result schema"


@pytest.mark.parametrize("bucket", [ec.MANDATORY, ec.HAS_DEFAULT, ec.DEV_OPT_IN])
def test_every_declared_bucket_is_handled_by_evaluation(bucket, tmp_path, monkeypatch):
    """A bucket present in ENV_BUCKETS but unread by evaluate_contract would be
    a silently ignored classification — the dead-rule shape Round 19 站1 found
    in the failure classifier."""
    monkeypatch.delenv("HARNESS_R20_BUCKET", raising=False)
    c = _contract()
    c["env_vars"][bucket] = ["HARNESS_R20_BUCKET"]
    out = ec.evaluate_contract(c, tmp_path)
    seen = (
        any(e["name"] == "HARNESS_R20_BUCKET" for e in out["env_vars"]["required"])
        or "HARNESS_R20_BUCKET" in out["env_vars"]["optional_missing"]
    )
    assert seen, f"bucket {bucket!r} is declared in ENV_BUCKETS but never evaluated"
