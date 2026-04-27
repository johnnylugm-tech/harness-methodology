"""Constitution package — HR compliance checking modules.

Modules:
- bvs_runner: BVS phase-order invariant checker (HR-03)
- citation_parser: citation and claims extractor (HR-07, HR-09)
- verification_constitution_checker: wrapper bridging to enforcement.constitution_as_code
"""

from constitution.bvs_runner import BVSRunner
from constitution.citation_parser import CitationParser
from constitution.verification_constitution_checker import VerificationConstitutionChecker

__all__ = ["BVSRunner", "CitationParser", "VerificationConstitutionChecker"]
