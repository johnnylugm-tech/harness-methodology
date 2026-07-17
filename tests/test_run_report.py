"""Round 14 站1: cli/report_cmds.py — run-report aggregation.

Each of the sources (spawn log / failure modes / degradations / crash
bundles / trajectory) is tested in isolation with synthetic fixtures, then
build_report is tested end-to-end for the "nothing exists yet" and
"everything present" cases. cmd_run_report itself is exercised only for
CLI-level concerns (--json shape, nonexistent --project) — the aggregation
logic lives in build_report and is covered above it.

Round 16 站3 added _failure_modes_report (MAST-aligned reclassification of
the same spawn-log entries _spawn_log_report reads — see
core/failure_modes.py).
"""

import argparse
import json

from cli.report_cmds import (
    _crash_report,
    _degradation_report,
    _failure_modes_report,
    _read_jsonl,
    _spawn_log_report,
    _trajectory_report,
    build_report,
    cmd_run_report,
)


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


class TestReadJsonl:
    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_jsonl(tmp_path / "nope.jsonl") == []

    def test_reads_valid_lines(self, tmp_path):
        p = tmp_path / "x.jsonl"
        _write_jsonl(p, [{"a": 1}, {"a": 2}])
        assert _read_jsonl(p) == [{"a": 1}, {"a": 2}]

    def test_skips_malformed_lines_without_crashing(self, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text('{"a": 1}\nnot json\n{"a": 2}\n\n', encoding="utf-8")
        assert _read_jsonl(p) == [{"a": 1}, {"a": 2}]

    def test_skips_non_dict_lines(self, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text('[1, 2, 3]\n{"a": 1}\n', encoding="utf-8")
        assert _read_jsonl(p) == [{"a": 1}]

    def test_line_cap_keeps_most_recent(self, tmp_path):
        p = tmp_path / "x.jsonl"
        _write_jsonl(p, [{"i": i} for i in range(10)])
        out = _read_jsonl(p, line_cap=3)
        assert out == [{"i": 7}, {"i": 8}, {"i": 9}]


class TestSpawnLogReport:
    def test_no_log_is_unavailable(self, tmp_path):
        assert _spawn_log_report(tmp_path) == {"available": False}

    def test_aggregates_status_and_error_class(self, tmp_path):
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [
            {"status": "complete", "fr_id": "FR-01"},
            {"status": "ERROR", "error_class": "INFRA_ERROR", "fr_id": "FR-01"},
            {"status": "ERROR", "error_class": "STRUCTURAL", "fr_id": "FR-02"},
            {"status": "TIMEOUT", "fr_id": "FR-02"},
        ])
        report = _spawn_log_report(tmp_path)
        assert report["available"] is True
        assert report["total_dispatches"] == 4
        assert report["status_counts"] == {"complete": 1, "ERROR": 2, "TIMEOUT": 1}
        assert report["error_class_counts"] == {"INFRA_ERROR": 1, "STRUCTURAL": 1}
        assert report["failure_rate"] == 0.75  # ERROR+ERROR+TIMEOUT out of 4

    def test_dispatches_per_fr_sorted_and_capped_at_10(self, tmp_path):
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        entries = [{"status": "complete", "fr_id": "FR-01"} for _ in range(5)]
        entries += [{"status": "complete", "fr_id": f"FR-{i:02d}"} for i in range(2, 13)]
        _write_jsonl(log, entries)
        report = _spawn_log_report(tmp_path)
        top = report["dispatches_per_fr_top10"]
        assert len(top) == 10
        assert next(iter(top)) == "FR-01"
        assert top["FR-01"]["dispatches"] == 5

    def test_entries_without_fr_id_excluded_from_per_fr_but_counted_in_total(self, tmp_path):
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [
            {"status": "PREFLIGHT_OK"},  # no fr_id — e.g. preflight-probe
            {"status": "complete", "fr_id": "FR-01"},
        ])
        report = _spawn_log_report(tmp_path)
        assert report["total_dispatches"] == 2
        assert list(report["dispatches_per_fr_top10"].keys()) == ["FR-01"]

    def test_cost_and_token_aggregation_with_partial_coverage(self, tmp_path):
        """Old log entries (pre-Round-14-站0) have no envelope fields at
        all — the denominator must reflect that, not silently drop them."""
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [
            {"status": "complete", "fr_id": "FR-01", "total_cost_usd": 0.01,
             "usage": {"input_tokens": 100, "output_tokens": 20}},
            {"status": "complete", "fr_id": "FR-01", "total_cost_usd": 0.02,
             "usage": {"input_tokens": 50, "output_tokens": 10}},
            {"status": "complete", "fr_id": "FR-02"},  # no envelope fields at all
        ])
        report = _spawn_log_report(tmp_path)
        assert report["cost_usd_total"] == 0.03
        assert report["cost_entries_with_data"] == 2
        assert report["cost_entries_total"] == 3
        assert report["tokens_input_total"] == 150
        assert report["tokens_output_total"] == 30
        assert report["dispatches_per_fr_top10"]["FR-01"]["cost_usd"] == 0.03

    def test_zero_envelope_entries_yields_none_not_zero(self, tmp_path):
        """No entry has cost/usage at all -> None (unknown), never 0
        (which would look like a real zero-cost run)."""
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [{"status": "complete", "fr_id": "FR-01"}])
        report = _spawn_log_report(tmp_path)
        assert report["cost_usd_total"] is None
        assert report["tokens_input_total"] is None
        assert report["tokens_output_total"] is None

    def test_duration_averages(self, tmp_path):
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [
            {"status": "complete", "duration_seconds": 1.0, "duration_api_ms": 100},
            {"status": "complete", "duration_seconds": 3.0, "duration_api_ms": 300},
        ])
        report = _spawn_log_report(tmp_path)
        assert report["duration_seconds_avg"] == 2.0
        assert report["duration_api_ms_avg"] == 200.0


class TestFailureModesReport:
    def test_no_log_is_unavailable(self, tmp_path):
        assert _failure_modes_report(tmp_path) == {"available": False}

    def test_reclassifies_the_same_log_spawn_log_report_reads(self, tmp_path):
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [
            {"timestamp": "2026-01-01T00:00:00", "status": "ERROR", "error_class": "INFRA_ERROR"},
            {"timestamp": "2026-01-02T00:00:00", "status": "TIMEOUT"},
            {"timestamp": "2026-01-03T00:00:00", "status": "complete"},
        ])
        report = _failure_modes_report(tmp_path)
        assert report["available"] is True
        assert report["total"] == 3
        assert report["mode_counts"]["infra_error_transient"] == 1
        assert report["mode_counts"]["dispatch_timeout"] == 1
        assert report["mode_counts"]["UNCLASSIFIED"] == 1
        assert report["first_timestamp"] == "2026-01-01T00:00:00"
        assert report["last_timestamp"] == "2026-01-03T00:00:00"

    def test_missing_timestamps_yield_none_span(self, tmp_path):
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        _write_jsonl(log, [{"status": "complete"}])
        report = _failure_modes_report(tmp_path)
        assert report["first_timestamp"] is None
        assert report["last_timestamp"] is None


class TestDegradationReport:
    def test_no_ledger_is_unavailable(self, tmp_path):
        assert _degradation_report(tmp_path) == {"available": False}

    def test_groups_by_component_and_what(self, tmp_path):
        ledger = tmp_path / ".sessi-work" / "degradations.jsonl"
        _write_jsonl(ledger, [
            {"component": "state-io", "what": "corrupt state.json"},
            {"component": "state-io", "what": "corrupt state.json"},
            {"component": "crash-triage", "what": "skipped unreadable bundle"},
        ])
        report = _degradation_report(tmp_path)
        assert report["available"] is True
        assert report["total"] == 3
        assert report["by_component_what"][0] == {
            "component": "state-io", "what": "corrupt state.json", "count": 2,
        }


class TestCrashReport:
    def test_no_crash_dir_is_unavailable(self, tmp_path):
        assert _crash_report(tmp_path) == {"available": False}

    def test_empty_crash_dir_is_unavailable(self, tmp_path):
        (tmp_path / ".sessi-work" / "crash").mkdir(parents=True)
        assert _crash_report(tmp_path) == {"available": False}

    def test_counts_total_and_untriaged(self, tmp_path):
        crash_dir = tmp_path / ".sessi-work" / "crash"
        crash_dir.mkdir(parents=True)
        (crash_dir / "crash_1_1.json").write_text("{}", encoding="utf-8")
        (crash_dir / "crash_2_2.json").write_text("{}", encoding="utf-8")
        (crash_dir / "crash_2_2.json.triaged").write_text("CR-01", encoding="utf-8")
        report = _crash_report(tmp_path)
        assert report == {"available": True, "total": 2, "untriaged": 1}


class TestTrajectoryReport:
    def test_no_trace_file_is_unavailable(self, tmp_path):
        assert _trajectory_report(tmp_path) == {"available": False}

    def test_aggregates_wall_seconds_by_span_name(self, tmp_path):
        trace = tmp_path / ".harness" / "traces" / "agent_trajectory.jsonl"
        _write_jsonl(trace, [
            {"name": "run_gate", "start_time": 0, "end_time": 2_000_000_000},
            {"name": "run_gate", "start_time": 0, "end_time": 1_000_000_000},
            {"name": "finalize_gate", "start_time": 0, "end_time": 500_000_000},
        ])
        report = _trajectory_report(tmp_path)
        assert report["available"] is True
        assert report["total_spans"] == 3
        assert report["span_counts"] == {"run_gate": 2, "finalize_gate": 1}
        assert report["wall_seconds_by_span"]["run_gate"] == 3.0
        assert report["wall_seconds_by_span"]["finalize_gate"] == 0.5

    def test_line_cap_applied(self, tmp_path):
        trace = tmp_path / ".harness" / "traces" / "agent_trajectory.jsonl"
        _write_jsonl(trace, [
            {"name": "old", "start_time": 0, "end_time": 1_000_000_000},
            {"name": "kept1", "start_time": 0, "end_time": 1_000_000_000},
            {"name": "kept2", "start_time": 0, "end_time": 1_000_000_000},
        ])
        report = _trajectory_report(tmp_path, line_cap=2)
        assert report["total_spans"] == 2
        assert "old" not in report["span_counts"]

    def test_malformed_start_end_skips_wall_time_but_keeps_count(self, tmp_path):
        trace = tmp_path / ".harness" / "traces" / "agent_trajectory.jsonl"
        _write_jsonl(trace, [{"name": "weird", "start_time": "not-a-number", "end_time": 5}])
        report = _trajectory_report(tmp_path)
        assert report["span_counts"] == {"weird": 1}
        assert report["wall_seconds_by_span"] == {}


class TestBuildReport:
    def test_nothing_present_all_sections_unavailable(self, tmp_path):
        report = build_report(tmp_path)
        assert report["project"] == str(tmp_path)
        assert report["spawn_log"] == {"available": False}
        assert report["failure_modes"] == {"available": False}
        assert report["degradations"] == {"available": False}
        assert report["crash_bundles"] == {"available": False}
        assert report["trajectory"] == {"available": False}

    def test_json_serializable(self, tmp_path):
        _write_jsonl(tmp_path / ".methodology" / "sessions_spawn.log",
                     [{"status": "complete", "fr_id": "FR-01", "total_cost_usd": 0.01}])
        report = build_report(tmp_path)
        json.dumps(report)  # must not raise


class TestCmdRunReport:
    def _args(self, project, *, as_json=False):
        return argparse.Namespace(project=str(project), json=as_json)

    def test_json_flag_prints_valid_json(self, tmp_path, capsys):
        exit_code = cmd_run_report(self._args(tmp_path, as_json=True))
        assert exit_code == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["project"] == str(tmp_path)

    def test_human_readable_mentions_all_five_sections(self, tmp_path, capsys):
        exit_code = cmd_run_report(self._args(tmp_path))
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Spawn dispatches" in out
        assert "Failure modes" in out
        assert "Degradations" in out
        assert "Crash bundles" in out
        assert "Agent trajectory spans" in out

    def test_nonexistent_project_warns_but_still_succeeds(self, tmp_path, capsys):
        missing = tmp_path / "does-not-exist"
        exit_code = cmd_run_report(self._args(missing))
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "does not exist" in captured.err
        assert "n/a" in captured.out
