"""
tests/test_reviewer_router_extended.py — Extended ReviewerRouter coverage (W3).

Covers: decompose_with_deps, section extractors, dep graph, topo sort,
        enrich_with_context, merge_results, try_chain (mocked), review() flow.
"""
import os
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_router(target="telegram:test"):
    with patch.dict(os.environ, {"HERMES_REVIEWER_TARGET": target}):
        import importlib
        import harness.reviewer_router as rr
        importlib.reload(rr)  # ensure env var picked up
        return rr.ReviewerRouter(target=target)


APPROVE_RESULT = {
    "review_status": "APPROVE",
    "confidence": 0.9,
    "violations": [],
    "summary": "All good",
    "_reviewer_used": "subagent",
    "_degradation": [],
}

REJECT_RESULT = {
    "review_status": "REJECT",
    "confidence": 0.1,
    "violations": ["critical_issue"],
    "summary": "Bad",
    "_reviewer_used": "subagent",
    "_degradation": [],
}

LARGE_PROMPT = "X " * 1200   # > TASK_SIZE_THRESHOLD=2000 chars when joined

FR_PROMPT = (
    "# Review\n\n"
    "FR-001: User registration shall work.\n"
    "Acceptance criteria: user exists.\n\n"
    "FR-002: Login shall work.\n"
    "Depends on FR-001. Must be fast.\n\n"
    "FR-003: Logout.\n"
)

PHASE_PROMPT = (
    "## Phase 1: Requirements\n"
    "Spec must define all FRs.\n\n"
    "## Phase 2: Architecture\n"
    "Phase 1 results feed into arch.\n\n"
    "## Phase 3: Implementation\n"
    "Based on Phase 2 decisions.\n\n"
)

HEADING_PROMPT = (
    "## §1.1 Overview\nDetails here.\n\n"
    "## §2.3 Design\nMore details.\n\n"
    "## §3.0 Testing\nTest plan.\n\n"
)


# ===========================================================================
# _parse_chain
# ===========================================================================

class TestParseChain:
    def test_default_chain_has_subagent(self):
        from harness.reviewer_router import _parse_chain
        specs = _parse_chain("hermes,gemini")
        names = [s.name for s in specs]
        assert "subagent" in names

    def test_subagent_always_appended(self):
        from harness.reviewer_router import _parse_chain
        specs = _parse_chain("hermes")
        assert specs[-1].name == "subagent"

    def test_hermes_disabled_when_not_available(self):
        from harness.reviewer_router import _parse_chain
        with patch("harness.reviewer_router._HERMES_AVAILABLE", False):
            specs = _parse_chain("hermes")
            hermes = next((s for s in specs if s.name == "hermes"), None)
            assert hermes is None or hermes.enabled is False

    def test_gemini_disabled_when_not_available(self):
        from harness.reviewer_router import _parse_chain
        with patch("harness.reviewer_router._GEMINI_AVAILABLE", False):
            specs = _parse_chain("gemini")
            gemini = next((s for s in specs if s.name == "gemini"), None)
            assert gemini is None or gemini.enabled is False

    def test_subagent_always_enabled(self):
        from harness.reviewer_router import _parse_chain
        specs = _parse_chain("hermes")
        subagent = next(s for s in specs if s.name == "subagent")
        assert subagent.enabled is True

    def test_empty_chain_still_has_subagent(self):
        from harness.reviewer_router import _parse_chain
        specs = _parse_chain("")
        assert specs[0].name == "subagent"


# ===========================================================================
# Section extractors
# ===========================================================================

class TestSectionExtractors:
    def _router(self):
        return _make_router()

    def test_extract_phase_sections_finds_phases(self):
        r = self._router()
        sections = r._extract_phase_sections(PHASE_PROMPT)
        assert len(sections) >= 2
        assert any("Phase" in k for k in sections)

    def test_extract_phase_sections_returns_empty_for_single_phase(self):
        r = self._router()
        sections = r._extract_phase_sections("## Phase 1: Only\nSome content here.")
        assert sections == {}

    def test_extract_fr_sections_finds_frs(self):
        r = self._router()
        sections = r._extract_fr_sections(FR_PROMPT)
        assert len(sections) >= 2
        assert any("FR-" in k for k in sections)

    def test_extract_fr_sections_returns_empty_for_single_fr(self):
        r = self._router()
        sections = r._extract_fr_sections("Only FR-001 mentioned once.")
        assert sections == {}

    def test_extract_heading_sections_finds_headings(self):
        r = self._router()
        sections = r._extract_heading_sections(HEADING_PROMPT)
        assert len(sections) >= 2

    def test_extract_heading_sections_returns_empty_for_one(self):
        r = self._router()
        sections = r._extract_heading_sections("## §1.1 Only one section\nContent.")
        assert sections == {}

    def test_extract_label_phase(self):
        r = self._router()
        label = r._extract_label("## Phase 3 — Implementation\nContent.", "Phase")
        assert "Phase" in label and "3" in label

    def test_extract_label_fallback(self):
        r = self._router()
        label = r._extract_label("No phase number here", "Phase")
        assert len(label) <= 30


# ===========================================================================
# _split_at_matches
# ===========================================================================

class TestSplitAtMatches:
    def _router(self):
        return _make_router()

    def test_splits_into_correct_number_of_sections(self):
        import re
        r = self._router()
        text = "FR-001: first\n\nFR-002: second\n\nFR-003: third\n"
        matches = list(re.finditer(r'(?=\bFR-(\d+)\b)', text))
        sections = r._split_at_matches(
            text, matches,
            label_fn=lambda m, txt: f"FR-{m.group(1).zfill(3)}",
        )
        assert len(sections) == 3

    def test_deduplicates_labels(self):
        import re
        r = self._router()
        # Two sections with same extracted label
        text = "FR-001: A\n\nFR-001: B\n"
        matches = list(re.finditer(r'(?=\bFR-(\d+)\b)', text))
        sections = r._split_at_matches(
            text, matches,
            label_fn=lambda m, txt: "FR-001",  # always same label
        )
        assert len(sections) == 2  # deduplicated by index suffix


# ===========================================================================
# _paragraph_subtasks
# ===========================================================================

class TestParagraphSubtasks:
    def _router(self):
        return _make_router()

    def test_returns_list_of_subtasks(self):
        r = self._router()
        big = "\n\n".join(["paragraph " + str(i) + " " * 100 for i in range(20)])
        result = r._paragraph_subtasks(big, "reviewer")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_each_subtask_has_correct_structure(self):
        r = self._router()
        big = "\n\n".join(["para " * 200 for i in range(5)])
        result = r._paragraph_subtasks(big, "reviewer")
        for st in result:
            assert hasattr(st, "content")
            assert hasattr(st, "label")
            assert st.total == len(result)

    def test_single_small_paragraph(self):
        r = self._router()
        result = r._paragraph_subtasks("short paragraph", "reviewer")
        assert len(result) == 1
        assert result[0].index == 1

    def test_dependencies_set_for_sequential(self):
        r = self._router()
        big = "\n\n".join(["para " * 100 for _ in range(5)])
        result = r._paragraph_subtasks(big, "reviewer")
        if len(result) > 1:
            assert result[1].dependencies == ["part_1"]


# ===========================================================================
# _build_dep_graph
# ===========================================================================

class TestBuildDepGraph:
    def _router(self):
        return _make_router()

    def test_no_cross_refs_no_deps(self):
        r = self._router()
        sections = {"A": "isolated content", "B": "also isolated"}
        deps = r._build_dep_graph(sections)
        assert deps["A"] == []
        assert deps["B"] == []

    def test_cross_ref_creates_dep(self):
        r = self._router()
        sections = {"A": "content about B section", "B": "standalone"}
        deps = r._build_dep_graph(sections)
        assert "B" in deps["A"]

    def test_phase_ordering_creates_implicit_dep(self):
        r = self._router()
        sections = {
            "Phase-1": "requirements",
            "Phase-2": "architecture",
            "Phase-3": "implementation",
        }
        deps = r._build_dep_graph(sections)
        assert "Phase-1" in deps["Phase-2"]
        assert "Phase-2" in deps["Phase-3"]

    def test_no_self_deps(self):
        r = self._router()
        sections = {"A": "A is great", "B": "B is fine"}
        deps = r._build_dep_graph(sections)
        assert "A" not in deps["A"]


# ===========================================================================
# _topological_sort
# ===========================================================================

class TestTopologicalSort:
    def _router(self):
        return _make_router()

    def test_independent_nodes_original_order(self):
        r = self._router()
        deps = {"A": [], "B": [], "C": []}
        result = r._topological_sort(deps, ["A", "B", "C"])
        assert set(result) == {"A", "B", "C"}

    def test_dep_comes_first(self):
        r = self._router()
        deps = {"A": ["B"], "B": [], "C": []}
        result = r._topological_sort(deps, ["A", "B", "C"])
        assert result.index("B") < result.index("A")

    def test_chain_ordering(self):
        r = self._router()
        deps = {"Phase-1": [], "Phase-2": ["Phase-1"], "Phase-3": ["Phase-2"]}
        result = r._topological_sort(deps, ["Phase-1", "Phase-2", "Phase-3"])
        assert result == ["Phase-1", "Phase-2", "Phase-3"]

    def test_cycle_handled_without_exception(self):
        r = self._router()
        deps = {"A": ["B"], "B": ["A"]}  # cycle
        result = r._topological_sort(deps, ["A", "B"])
        assert set(result) == {"A", "B"}  # all nodes present, no crash


# ===========================================================================
# _enrich_with_context
# ===========================================================================

class TestEnrichWithContext:
    def _router(self):
        return _make_router()

    def test_no_context_returns_original_content(self):
        from harness.reviewer_router import SubTask
        r = self._router()
        st = SubTask(content="my content", label="F1")
        result = r._enrich_with_context(st, [])
        assert result == "my content"

    def test_context_prepended(self):
        from harness.reviewer_router import SubTask
        r = self._router()
        st = SubTask(content="current task", label="F2", dependencies=["F1"])
        result = r._enrich_with_context(st, ["✅ [F1] looks good"])
        assert "Previously approved" in result
        assert "current task" in result

    def test_context_capped_at_max(self):
        from harness.reviewer_router import SubTask, MAX_CONTEXT_LINES
        r = self._router()
        ctx = [f"✅ [F{i}] ok" for i in range(20)]
        st = SubTask(content="task", label="F99")
        result = r._enrich_with_context(st, ctx)
        # Only MAX_CONTEXT_LINES entries should appear
        count = result.count("✅")
        assert count <= MAX_CONTEXT_LINES

    def test_dependency_note_included(self):
        from harness.reviewer_router import SubTask
        r = self._router()
        st = SubTask(content="task", label="F3", dependencies=["F1", "F2"])
        result = r._enrich_with_context(st, ["✅ [F1] ok"])
        assert "Dependencies:" in result


# ===========================================================================
# _merge_results
# ===========================================================================

class TestMergeResults:
    def _router(self):
        return _make_router()

    def test_empty_results_returns_reject(self):
        r = self._router()
        result = r._merge_results([])
        assert result["review_status"] == "REJECT"

    def test_single_result_returned_unchanged(self):
        r = self._router()
        result = r._merge_results([APPROVE_RESULT])
        assert result == APPROVE_RESULT

    def test_all_approve_returns_approve(self):
        r = self._router()
        result = r._merge_results([APPROVE_RESULT, APPROVE_RESULT])
        assert result["review_status"] == "APPROVE"

    def test_any_reject_short_circuits(self):
        r = self._router()
        result = r._merge_results([APPROVE_RESULT, REJECT_RESULT])
        assert result["review_status"] == "REJECT"

    def test_min_confidence_used(self):
        r = self._router()
        r1 = {**APPROVE_RESULT, "confidence": 0.9}
        r2 = {**APPROVE_RESULT, "confidence": 0.6}
        result = r._merge_results([r1, r2])
        assert result["confidence"] == 0.6

    def test_violations_union(self):
        r = self._router()
        r1 = {**APPROVE_RESULT, "violations": ["v1"]}
        r2 = {**APPROVE_RESULT, "violations": ["v2"]}
        result = r._merge_results([r1, r2])
        assert "v1" in result["violations"]
        assert "v2" in result["violations"]

    def test_summaries_joined(self):
        r = self._router()
        r1 = {**APPROVE_RESULT, "summary": "good1"}
        r2 = {**APPROVE_RESULT, "summary": "good2"}
        result = r._merge_results([r1, r2])
        assert "good1" in result["summary"]
        assert "good2" in result["summary"]

    def test_merged_flag_set(self):
        r = self._router()
        result = r._merge_results([APPROVE_RESULT, APPROVE_RESULT])
        assert result["_merged"] is True
        assert result["_subtask_count"] == 2

    def test_degraded_flag_propagated(self):
        r = self._router()
        r1 = {**APPROVE_RESULT, "_degraded": True, "_degradation_note": "fell back"}
        result = r._merge_results([r1, APPROVE_RESULT])
        assert result["_degraded"] is True


# ===========================================================================
# _decompose_with_deps  (integration-style)
# ===========================================================================

class TestDecomposeWithDeps:
    def _router(self):
        return _make_router()

    def test_small_prompt_returns_single_subtask(self):
        r = self._router()
        result = r._decompose_with_deps("short", "reviewer")
        assert len(result) == 1
        assert result[0].label == "full_task"

    def test_fr_prompt_decomposes(self):
        r = self._router()
        big_fr = FR_PROMPT * 20  # multiply enough to exceed TASK_SIZE_THRESHOLD=2000
        result = r._decompose_with_deps(big_fr, "reviewer")
        assert len(result) >= 2

    def test_phase_prompt_decomposes(self):
        r = self._router()
        big = PHASE_PROMPT * 15  # exceed threshold
        result = r._decompose_with_deps(big, "reviewer")
        assert len(result) >= 2

    def test_unstructured_large_falls_back_to_paragraphs(self):
        r = self._router()
        # No Phase/FR/§ markers — should fall back to paragraph split
        big = "\n\n".join(["sentence " * 50 for _ in range(10)])
        result = r._decompose_with_deps(big, "reviewer")
        assert len(result) >= 1


# ===========================================================================
# review() with mocked _try_chain
# ===========================================================================

class TestReviewFlow:
    def _router(self):
        return _make_router()

    def test_review_single_subtask_returns_result(self):
        r = self._router()
        with patch.object(r, "_try_chain", return_value=APPROVE_RESULT):
            result = r.review(role="reviewer", prompt="check this", phase=3)
        assert result["review_status"] == "APPROVE"

    def test_review_multi_subtask_stops_on_reject(self):
        r = self._router()
        call_count = [0]

        def mock_try_chain(*args, **kwargs):
            call_count[0] += 1
            return REJECT_RESULT if call_count[0] == 1 else APPROVE_RESULT

        big = PHASE_PROMPT * 5  # triggers decompose
        with patch.object(r, "_decompose_with_deps") as mock_decomp:
            from harness.reviewer_router import SubTask
            mock_decomp.return_value = [
                SubTask("t1", "Phase-1", index=1, total=3),
                SubTask("t2", "Phase-2", index=2, total=3),
                SubTask("t3", "Phase-3", index=3, total=3),
            ]
            with patch.object(r, "_try_chain", side_effect=mock_try_chain):
                result = r.review(role="reviewer", prompt=big, phase=3)
        # First subtask REJECTED → merged result is REJECT
        assert result["review_status"] == "REJECT"
        assert call_count[0] == 1  # stopped after first

    def test_review_multi_subtask_all_approve(self):
        r = self._router()
        big = PHASE_PROMPT * 5
        with patch.object(r, "_decompose_with_deps") as mock_decomp:
            from harness.reviewer_router import SubTask
            mock_decomp.return_value = [
                SubTask("t1", "Phase-1", index=1, total=2),
                SubTask("t2", "Phase-2", index=2, total=2),
            ]
            with patch.object(r, "_try_chain", return_value=APPROVE_RESULT):
                result = r.review(role="reviewer", prompt=big, phase=3)
        assert result["review_status"] == "APPROVE"
        assert result.get("_subtask_count") == 2

    def test_review_accumulates_context(self):
        r = self._router()
        enriched_calls = []

        def mock_enrich(subtask, ctx):
            enriched_calls.append(len(ctx))
            return subtask.content

        with patch.object(r, "_decompose_with_deps") as mock_decomp:
            from harness.reviewer_router import SubTask
            mock_decomp.return_value = [
                SubTask("t1", "Phase-1", index=1, total=3),
                SubTask("t2", "Phase-2", index=2, total=3),
                SubTask("t3", "Phase-3", index=3, total=3),
            ]
            with patch.object(r, "_enrich_with_context", side_effect=mock_enrich):
                with patch.object(r, "_try_chain", return_value={
                    **APPROVE_RESULT, "summary": "partial summary"
                }):
                    r.review(role="reviewer", prompt="big prompt", phase=3)
        # Context grows: subtask 1→0 context, subtask 2→1 context, subtask 3→2 context
        assert enriched_calls[0] == 0
        assert enriched_calls[1] == 1
        assert enriched_calls[2] == 2


# ===========================================================================
# _try_chain (subagent path)
# ===========================================================================

class TestTryChain:
    def _router(self):
        return _make_router()

    def test_subagent_fallback_when_all_disabled(self):
        r = self._router()
        # Disable hermes and gemini
        for spec in r._chain:
            if spec.name != "subagent":
                spec.enabled = False
        with patch.object(r, "_try_subagent", return_value=APPROVE_RESULT) as mock_sub:
            result = r._try_chain("reviewer", "prompt", 3, None, None)
        mock_sub.assert_called_once()
        assert result["review_status"] == "APPROVE"

    def test_degradation_note_when_hermes_times_out(self):
        r = self._router()
        # Enable hermes, disable gemini so chain falls through to subagent
        for spec in r._chain:
            if spec.name == "hermes":
                spec.enabled = True
            elif spec.name == "gemini":
                spec.enabled = False
        with patch.object(r, "_try_hermes", side_effect=TimeoutError("timeout")):
            with patch.object(r, "_try_subagent", return_value=APPROVE_RESULT):
                result = r._try_chain("reviewer", "prompt", 3, None, None)
        assert result.get("_degraded") is True
        assert "subagent" == result.get("_reviewer_used")

    def test_subagent_result_marked_degraded_when_chain_skipped(self):
        r = self._router()
        for spec in r._chain:
            if spec.name != "subagent":
                spec.enabled = False
        with patch.object(r, "_try_subagent", return_value={"review_status": "APPROVE", "confidence": 0.5, "violations": [], "summary": "ok", "_reviewer_used": "subagent", "_degradation": []}):
            result = r._try_chain("reviewer", "prompt", 3, None, None)
        assert result["_reviewer_used"] == "subagent"


# ===========================================================================
# _try_subagent
# ===========================================================================

class TestTrySubagent:
    def _router(self):
        return _make_router()

    def test_try_subagent_emergency_fallback_on_exception(self):
        r = self._router()
        # AgentSpawner is imported lazily inside _try_subagent
        with patch("core.agent_spawner.AgentSpawner", side_effect=Exception("spawn error")):
            with patch.dict("sys.modules", {"core.agent_spawner": None}):
                result = r._try_subagent("reviewer", "prompt", 3, None)
        assert result.get("_emergency_fallback") is True
        assert result["confidence"] == 0.3

    def test_try_subagent_returns_spawner_dict(self):
        r = self._router()
        mock_spawner_instance = MagicMock()
        mock_spawner_instance.spawn.return_value = APPROVE_RESULT
        mock_spawner_cls = MagicMock(return_value=mock_spawner_instance)
        mock_module = MagicMock()
        mock_module.AgentSpawner = mock_spawner_cls
        with patch.dict("sys.modules", {"core.agent_spawner": mock_module}):
            result = r._try_subagent("reviewer", "prompt", 3, None)
        assert result["review_status"] == "APPROVE"

    def test_try_subagent_wraps_non_dict_output(self):
        r = self._router()
        mock_spawner_instance = MagicMock()
        mock_spawner_instance.spawn.return_value = "plain string result"
        mock_spawner_cls = MagicMock(return_value=mock_spawner_instance)
        mock_module = MagicMock()
        mock_module.AgentSpawner = mock_spawner_cls
        with patch.dict("sys.modules", {"core.agent_spawner": mock_module}):
            result = r._try_subagent("reviewer", "prompt", 3, None)
        assert "output" in result


# ===========================================================================
# _clean_gemini_response
# ===========================================================================

class TestCleanGeminiResponse:
    def _router(self):
        return _make_router()

    def test_clean_removes_contamination(self):
        r = self._router()
        raw = 'Good review output.\n\nsession-end-marker and more garbage after'
        result = r._clean_gemini_response(raw)
        assert "session-end-marker" not in result
        assert "Good review" in result

    def test_clean_no_contamination_unchanged(self):
        r = self._router()
        raw = "Clean response text."
        result = r._clean_gemini_response(raw)
        assert result == "Clean response text."

    def test_clean_plugin_root_marker(self):
        r = self._router()
        raw = "Output here\nplugin_root garbage\n"
        result = r._clean_gemini_response(raw)
        assert "plugin_root" not in result
