"""Unit tests for core/subagent_isolator.py — Need-to-Know isolation enforcement."""
from pathlib import Path

import pytest

from core.subagent_isolator import (
    SubagentIsolator, SubagentContext, ArtifactSpec,
    ArtifactValidationError, IsolationViolationError,
    create_isolated_spawn,
    TurnBasedExecutor, TurnContext, TurnResult,
)


# ── SubagentContext ───────────────────────────────────────────────────────

class TestSubagentContext:
    def test_isolation_id_generated_on_creation(self):
        ctx = SubagentContext(task="test", role="developer")
        assert ctx.isolation_id
        assert len(ctx.isolation_id) == 16

    def test_isolation_id_deterministic(self):
        """isolation_id is a deterministic SHA-256 hash of (task, role) — not random."""
        a = SubagentContext(task="same", role="dev")
        b = SubagentContext(task="same", role="dev")
        assert a.isolation_id == b.isolation_id

    def test_isolation_id_differs_on_task_change(self):
        a = SubagentContext(task="A", role="dev")
        b = SubagentContext(task="B", role="dev")
        assert a.isolation_id != b.isolation_id

    def test_messages_always_start_empty(self):
        ctx = SubagentContext(task="t", role="r")
        assert ctx.messages == []

    def test_to_spawn_config_includes_all_fields(self):
        ctx = SubagentContext(
            task="build API", role="developer",
            artifacts=[ArtifactSpec("specs/x.md", role="input")],
            persona_prompt="You are a Go developer.",
        )
        cfg = ctx.to_spawn_config()
        assert cfg["task"] == "build API"
        assert cfg["role"] == "developer"
        assert cfg["isolation_id"]
        assert len(cfg["artifact_paths"]) == 1
        assert cfg["artifact_paths"][0]["path"] == "specs/x.md"
        assert isinstance(cfg["messages"], list)

    def test_to_spawn_config_deep_copies(self):
        ctx = SubagentContext(task="t", role="r", metadata={"key": "val"})
        cfg1 = ctx.to_spawn_config()
        cfg2 = ctx.to_spawn_config()
        cfg1["metadata"]["key"] = "modified"
        assert cfg2["metadata"]["key"] == "val"


# ── SubagentIsolator — create_context ─────────────────────────────────────

class TestCreateContext:
    def test_context_has_fresh_messages(self):
        isolator = SubagentIsolator()
        ctx = isolator.create_context(task="t1", role="developer")
        assert ctx.messages == []

    def test_context_tracked_in_active(self):
        isolator = SubagentIsolator()
        ctx = isolator.create_context(task="t1", role="dev")
        found = isolator.get_context(ctx.isolation_id)
        assert found is ctx

    def test_metadata_deep_copied(self):
        isolator = SubagentIsolator()
        md = {"tags": ["a"]}
        ctx = isolator.create_context(task="t1", role="dev", metadata=md)
        md["tags"].append("b")
        assert ctx.metadata["tags"] == ["a"]


# ── SubagentIsolator — validate ───────────────────────────────────────────

class TestValidate:
    def test_validate_passes_when_all_inputs_exist(self, tmp_path: Path):
        existing = tmp_path / "spec.md"
        existing.write_text("# spec")
        isolator = SubagentIsolator(project_root=str(tmp_path))
        ctx = isolator.create_context(
            task="t", role="dev",
            artifacts=[ArtifactSpec(str(existing), role="input", required=True)],
        )
        isolator.validate(ctx)  # should not raise

    def test_validate_raises_when_required_input_missing(self, tmp_path: Path):
        isolator = SubagentIsolator(project_root=str(tmp_path))
        ctx = isolator.create_context(
            task="t", role="dev",
            artifacts=[ArtifactSpec("NONEXISTENT", role="input", required=True)],
        )
        with pytest.raises(ArtifactValidationError, match="Missing required"):
            isolator.validate(ctx)

    def test_validate_ignores_output_artifacts(self, tmp_path: Path):
        isolator = SubagentIsolator(project_root=str(tmp_path))
        ctx = isolator.create_context(
            task="t", role="dev",
            artifacts=[ArtifactSpec("NONEXISTENT_OUTPUT", role="output", required=True)],
        )
        isolator.validate(ctx)  # output artifacts not checked for existence

    def test_validate_outputs_reports_missing(self, tmp_path: Path):
        isolator = SubagentIsolator(project_root=str(tmp_path))
        ctx = isolator.create_context(
            task="t", role="dev",
            artifacts=[ArtifactSpec("NONEXISTENT", role="output", required=True)],
        )
        result = isolator.validate_outputs(ctx)
        assert not result["complete"]
        assert "NONEXISTENT" in result["missing"]

    def test_validate_outputs_passes_when_produced(self, tmp_path: Path):
        existing = tmp_path / "result.py"
        existing.write_text("# code")
        isolator = SubagentIsolator(project_root=str(tmp_path))
        ctx = isolator.create_context(
            task="t", role="dev",
            artifacts=[ArtifactSpec(str(existing), role="output", required=True)],
        )
        result = isolator.validate_outputs(ctx)
        assert result["complete"]


# ── SubagentIsolator — verify_isolation ───────────────────────────────────

class TestVerifyIsolation:
    def test_clean_context_passes(self):
        isolator = SubagentIsolator()
        ctx = isolator.create_context(task="t", role="dev")
        isolator.verify_isolation(ctx)  # should not raise

    def test_contaminated_context_raises(self):
        isolator = SubagentIsolator()
        ctx = isolator.create_context(task="t", role="dev")
        ctx.messages.append({"role": "user", "content": "prior history"})
        with pytest.raises(IsolationViolationError, match="isolation violated"):
            isolator.verify_isolation(ctx)


# ── SubagentIsolator — spawn ──────────────────────────────────────────────

class TestSpawn:
    def test_spawn_returns_config_dict(self, tmp_path: Path):
        existing = tmp_path / "spec.md"
        existing.write_text("spec")
        isolator = SubagentIsolator(project_root=str(tmp_path))
        cfg = isolator.spawn(
            task="do X", role="developer",
            artifacts=[ArtifactSpec(str(existing), role="input")],
        )
        assert cfg["task"] == "do X"
        assert cfg["role"] == "developer"
        assert cfg["isolation_id"]

    def test_spawn_validate_false_skips_check(self, tmp_path: Path):
        isolator = SubagentIsolator(project_root=str(tmp_path))
        cfg = isolator.spawn(
            task="do X", role="dev",
            artifacts=[ArtifactSpec("MISSING", role="input")],
            validate=False,
        )
        assert cfg["task"] == "do X"

    def test_spawn_validate_true_raises_on_missing(self, tmp_path: Path):
        isolator = SubagentIsolator(project_root=str(tmp_path))
        with pytest.raises(ArtifactValidationError):
            isolator.spawn(
                task="t", role="dev",
                artifacts=[ArtifactSpec("MISSING", role="input")],
                validate=True,
            )


# ── SubagentIsolator — lifecycle ──────────────────────────────────────────

class TestLifecycle:
    def test_release_removes_context(self):
        isolator = SubagentIsolator()
        ctx = isolator.create_context(task="t", role="dev")
        assert isolator.active_count() == 1
        isolator.release(ctx.isolation_id)
        assert isolator.get_context(ctx.isolation_id) is None
        assert isolator.active_count() == 0

    def test_release_nonexistent_no_error(self):
        isolator = SubagentIsolator()
        isolator.release("nonexistent-id")  # should not raise

    def test_active_count_zero_initially(self):
        assert SubagentIsolator().active_count() == 0

    def test_active_count_tracks_multiple(self):
        isolator = SubagentIsolator()
        isolator.create_context(task="a", role="dev")
        isolator.create_context(task="b", role="reviewer")
        assert isolator.active_count() == 2

    def test_set_workspace_without_manager_is_noop(self, tmp_path: Path):
        isolator = SubagentIsolator(project_root=str(tmp_path))
        ctx = isolator.create_context(task="t", role="dev")
        isolator.set_workspace(ctx, "FR-01")  # no workspace_manager set
        assert ctx.workspace_path == ""


# ── create_isolated_spawn ─────────────────────────────────────────────────

class TestCreateIsolatedSpawn:
    def test_factory_with_inputs(self, tmp_path: Path):
        existing = tmp_path / "in.md"
        existing.write_text("input")
        cfg = create_isolated_spawn(
            task="build", role="dev",
            input_paths=[str(existing)],
        )
        assert cfg["task"] == "build"
        assert len(cfg["artifact_paths"]) == 1
        assert cfg["artifact_paths"][0]["required"] is True

    def test_factory_with_outputs(self, tmp_path: Path):
        existing = tmp_path / "in.md"
        existing.write_text("input")
        cfg = create_isolated_spawn(
            task="test", role="qa",
            input_paths=[str(existing)],
            output_paths=["out.md"],
        )
        outputs = [a for a in cfg["artifact_paths"] if a["role"] == "output"]
        assert len(outputs) == 1
        assert outputs[0]["required"] is False

    def test_factory_persona_prompt_defaults_to_empty(self, tmp_path: Path):
        """create_isolated_spawn with persona_prompt omitted → empty str."""
        existing = tmp_path / "in.md"
        existing.write_text("input")
        cfg = create_isolated_spawn(
            task="build", role="dev", input_paths=[str(existing)],
        )
        assert cfg["persona_prompt"] == ""


# ── TurnBasedExecutor ─────────────────────────────────────────────────────

class TestTurnBasedExecutor:
    def test_first_turn_builds_full_prompt(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator, max_turns=5)
        turn = TurnContext(turn_number=1, fr_id="FR-01",
                           continuation_prompt="Execute full task.",
                           remaining_items=["task1", "task2"])
        result = executor.execute_turn(turn)
        assert result.turn_number == 1
        assert "spawn_config" in result.output
        assert result.should_continue

    def test_turn_continuation_builds_short_prompt(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator, max_turns=5)
        turn = TurnContext(turn_number=2, fr_id="FR-01",
                           previous_output={"x": 1},
                           remaining_items=["item3", "item4"])
        result = executor.execute_turn(turn)
        assert result.turn_number == 2
        # short continuation guidance, not full spec
        assert "Continuation" in result.output["spawn_config"]["task"]

    def test_should_terminate_at_max_turns(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator, max_turns=5)
        turn = TurnResult(turn_number=5, should_continue=True)
        assert executor.should_terminate(turn, turns_used=5)

    def test_should_terminate_on_stop_signal(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator, max_turns=5)
        turn = TurnResult(turn_number=2, should_continue=False)
        assert executor.should_terminate(turn, turns_used=2)

    def test_should_not_terminate_mid_sequence(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator, max_turns=5)
        turn = TurnResult(turn_number=3, should_continue=True)
        assert not executor.should_terminate(turn, turns_used=3)

    def test_generate_continuation_increments_turn(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator, max_turns=5)
        prev = TurnResult(turn_number=2, output={"x": 1},
                          state_changes={"foo": "bar"},
                          next_actions=["a", "b"])
        next_ctx = executor.generate_continuation(prev, ["a", "b"])
        assert next_ctx.turn_number == 3
        assert next_ctx.remaining_items == ["a", "b"]
        assert "Remaining items" in next_ctx.continuation_prompt

    def test_get_state_diff(self):
        diff = TurnBasedExecutor.get_state_diff(
            {"a": 1, "b": 2},
            {"a": 1, "b": 3},
        )
        assert "a" not in diff
        assert diff["b"] == {"from": 2, "to": 3}

    def test_history_tracks_all_turns(self):
        isolator = SubagentIsolator()
        executor = TurnBasedExecutor(isolator)
        executor.execute_turn(TurnContext(turn_number=1, fr_id="FR-01"))
        executor.execute_turn(TurnContext(turn_number=2, fr_id="FR-01",
                                          previous_output={}))
        assert len(executor.history()) == 2


# ── ArtifactSpec ──────────────────────────────────────────────────────────

class TestArtifactSpec:
    def test_exists_true(self, tmp_path: Path):
        path = tmp_path / "real.txt"
        path.write_text("content")
        spec = ArtifactSpec(str(path), role="input")
        assert spec.exists()

    def test_exists_false(self):
        spec = ArtifactSpec("/no/such/path", role="input")
        assert not spec.exists()

    def test_defaults(self):
        spec = ArtifactSpec("x", role="reference")
        assert spec.required is True
        assert spec.description == ""
