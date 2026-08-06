"""Round 41 站0 — a transport failure is diagnosed from transport text.

`_classify_dispatch_error` decides whether a dispatch failed because the model
could not be reached (INFRA_ERROR) or because the agent's own work failed
(EXECUTION_ERROR), by matching `_INFRA_ERROR_RE` against a string. Round 19
built that registry from a real corpus, and its members are the vocabulary of
network and auth faults: `401|403|404|429|5xx`, `api key`, `authentication`,
`rate limit`, `quota`, `permission denied`, `connection refused`.

That vocabulary is also the SUBJECT MATTER of any project that builds an HTTP
API. taskq-api is one: its FR-03 is API-key authentication and its FR-04 is
scope authorisation returning 403.

The two meet because `core.failure_modes._effective_error_class` re-derives the
class from the log's `error_output`, and Round 26 站2 made `error_output` on a
semantic failure the diagnostic PLUS the sub-agent's verbatim reply — for good
reason (a diagnostic may not replace the evidence it describes). So the agent's
own prose about 403s is fed to a scanner looking for the string "403".

Measured before writing this: across 984 dispatch records in five consuming
projects, twelve entries re-derive from EXECUTION_ERROR to INFRA_ERROR and
**all twelve are correct** (genuine `Stream idle timeout`). There is no live
misclassification today. This is a hazard, not a wound — and it stays merely a
hazard only while INFRA_ERROR has no consumer that changes control flow.
Round 41 站3 gives it one. That is why this test exists before that station,
not after it: the moment a failure class decides whether to re-dispatch, a
sentence about HTTP status codes must not be able to cast that vote.

The fix this pins is a separation, not a smarter regex: transport text and
agent text are different fields, and only the first is evidence about
transport. A registry of signatures can never distinguish "the API returned
401" from "the test asserts 401" — the two strings are identical, and only
their provenance differs.
"""

from __future__ import annotations

import pytest

from core.agent_spawner import _classify_dispatch_error
from core.failure_modes import _effective_error_class

pytestmark = [pytest.mark.core]


# Real shapes: an agent working on an HTTP API reports its work, and fails.
_DOMAIN_REPLIES = [
    (
        "gate blocked on failing auth tests",
        "Commit-required step 'TDD-IMPROVE' returned empty commit (status='DONE')\n\n"
        '{"status": "DONE", "commit": null, '
        '"summary": "Gate 1 BLOCKED: 12 FR-01/FR-02 tests fail (401)"}',
    ),
    (
        "no-op while implementing the api-key dependency",
        "Sub-agent exited 0 with semantic no-op status 'NOTHING_TO_DO': "
        "'require_api_key already implemented; no api key work left'",
    ),
    (
        "a wrong status code in the FR's own assertion",
        "Commit-required step 'TDD-GREEN' returned empty commit (status='DONE')\n\n"
        '{"status": "DONE", "commit": null, '
        '"summary": "test_fr04 expects 403 but the route returns 404"}',
    ),
    (
        "the rate-limit feature, not a rate-limited API",
        "Sub-agent exited 0 with semantic no-op status 'NOTHING_TO_DO': "
        "'rate limit bucket for NFR-01 was already committed'",
    ),
]


@pytest.mark.parametrize("label,error_output", _DOMAIN_REPLIES,
                         ids=[r[0] for r in _DOMAIN_REPLIES])
def test_the_agents_own_words_do_not_diagnose_the_transport(label, error_output):
    """A semantic failure must not be re-derived into a transport failure.

    Each of these is a dispatch that reached the model, ran its tools, and
    failed at its own task. Calling any of them INFRA_ERROR states that the
    model could not be reached — which the record itself disproves.
    """
    entry = {
        "status": "ERROR",
        "error_class": "EXECUTION_ERROR",
        "error_output": error_output,
        "fr_id": "FR-04",
    }
    assert _effective_error_class(entry) != "INFRA_ERROR", (
        f"{label}: an agent-logic failure classified as an environment fault "
        f"because the project's domain vocabulary is the fault registry's "
        f"vocabulary — the string decided, and the string came from the agent"
    )


def test_a_real_transport_failure_is_still_a_transport_failure():
    """Positive control. The separation must not cost the real signal — this
    is the exact text taskq-api's eight identical FR-04 failures carried."""
    entry = {
        "status": "ERROR",
        "error_class": "EXECUTION_ERROR",
        "error_output": "subtype=success API Error: Stream idle timeout - no chunks received",
        "transport_error": "subtype=success API Error: Stream idle timeout - no chunks received",
    }
    assert _effective_error_class(entry) == "INFRA_ERROR"


def test_the_live_classifier_still_reads_the_text_it_is_given():
    """Negative-space control for the layer below: `_classify_dispatch_error`
    is a pure text classifier and stays one. The separation belongs to its
    CALLERS — which text they hand it — not to the registry."""
    assert _classify_dispatch_error(
        "subtype=success API Error: Stream idle timeout"
    ) == "INFRA_ERROR"
    assert _classify_dispatch_error("the tests did not pass") == "EXECUTION_ERROR"
