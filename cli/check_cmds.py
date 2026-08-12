"""The CLI wiring for the check commands — 24 subcommands, one register().

R49-B split the command bodies into cli/checks/ by the question each asks
(specs, gates, trace, approvals, constitution, hunt). What stayed here is the
argparse surface: this file is what harness_cli.py calls, and it is where a
reader looks to find out which flags a check command takes.

The imports below are re-exports as much as dependencies. harness_cli.py does
`from cli.check_cmds import (...)` and tests do the same; a split that moved
the names out from under those callers would break code that never asked
where the implementation lived.
"""

from __future__ import annotations

from cli.checks.approvals import (  # noqa: F401  (re-exported for harness_cli + tests)
    _generate_sab_json,
    _resolve_deliverable_ids,
    cmd_check_manifest_integrity,
    cmd_generate_verification_report,
    cmd_manifest,
    cmd_verify_agent_b_approvals,
    cmd_verify_file,
    cmd_write_approval,
)
from cli.checks.constitution import (  # noqa: F401
    _print_constitution_result,
    cmd_check_constitution,
    cmd_check_logic,
    cmd_print_legal_artifacts,
)
from cli.checks.gates import (  # noqa: F401
    cmd_crg_arch_check,
    cmd_spec_coverage_check,
    cmd_verify_ci,
    cmd_verify_gate,
)
from cli.checks.hunt import (  # noqa: F401
    _run_gap_analysis,
    cmd_bug_hunt_targets,
    cmd_run_gap_analysis,
)
from cli.checks.specs import (  # noqa: F401
    cmd_check_artifact_consistency,
    cmd_check_property_spec,
    cmd_check_spec_alignment,
    cmd_check_test_mirrors_spec,
    cmd_check_test_spec_consistency,
    cmd_verify_spec,
)
from cli.checks.trace import (  # noqa: F401
    cmd_build_trace_attestation,
    cmd_migrate_trace_overlay,
    cmd_verify_trace,
)


def register(sub) -> None:
    """Wire every check subcommand by asking each family to wire its own.

    R49-B 站3. This used to be 295 lines: 24 add_parser blocks in one place,
    so a command gaining a flag was edited in a file its 23 siblings also
    lived in. Each family now owns its parsers next to the functions they
    dispatch to, and this is the list of families.

    The families are called in a fixed order and `--help` lists subcommands in
    that order, which is the one visible change: the listing is now grouped by
    family instead of interleaved by the order the commands were written in
    over two years.
    """
    from cli.checks import (
        approvals,
        constitution,
        gates,
        hunt,
        specs,
        trace,
    )

    for family in (hunt, gates, specs, approvals, trace, constitution):
        family.register(sub)
