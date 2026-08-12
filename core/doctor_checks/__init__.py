"""The types every doctor check shares, and the reason they live here.

core/doctor.py is being split family by family (R49-B). Every check returns
`list[Finding]`, so every family module needs that type — and if it lived in
core/doctor.py, which imports the families, each family would import back into
the module importing it. A cycle that happens to work today because of import
order is a cycle that breaks on the day someone reorders the imports.

`Finding` therefore lives at the root of the package the families are in, and
core/doctor.py re-exports it: `from core.doctor import Finding` keeps working
for the tests and callers that already say it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Finding"]


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str  # "ERROR" | "WARN" | "INFO"
    message: str
