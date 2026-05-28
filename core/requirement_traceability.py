#!/usr/bin/env python3
"""
Requirement Traceability - Requirement Traceability Module

FR -> SRS -> Code -> Test bidirectional traceability. ASPICE SWE.3 / SYS.4 compliant.

Note: In harness-methodology, run as:
    python core/requirement_traceability.py --project-id <id>
(NOT scripts/requirement_traceability.py - the SOP path was corrected in v1.7)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import uuid


class TraceStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_IMPLEMENTED = "not_implemented"


class LinkType(Enum):
    FR_TO_SRS = "fr→srs"
    SRS_TO_CODE = "srs→code"
    CODE_TO_TEST = "code→test"
    TEST_TO_QUALITY = "test→quality"
    QUALITY_TO_AUDIT = "quality→audit"
    BIDIRECTIONAL = "bidirectional"


@dataclass
class Requirement:
    req_id: str
    title: str
    description: str
    priority: str = "HIGH"
    status: TraceStatus = TraceStatus.PENDING
    srs_section: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "req_id": self.req_id, "title": self.title,
            "description": self.description, "priority": self.priority,
            "status": self.status.value, "srs_section": self.srs_section,
            "created_at": self.created_at.isoformat(), "metadata": self.metadata
        }


@dataclass
class CodeComponent:
    file_path: str
    functions: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    line_range: Optional[str] = None
    fr_id: Optional[str] = None
    test_files: List[str] = field(default_factory=list)
    coverage: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path, "functions": self.functions,
            "classes": self.classes, "line_range": self.line_range,
            "fr_id": self.fr_id, "test_files": self.test_files,
            "coverage": self.coverage, "metadata": self.metadata
        }


@dataclass
class TestCoverage:
    __test__ = False
    test_file: str
    test_functions: List[str] = field(default_factory=list)
    fr_id: Optional[str] = None
    coverage_percentage: float = 0.0
    status: TraceStatus = TraceStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "test_file": self.test_file, "test_functions": self.test_functions,
            "fr_id": self.fr_id, "coverage_percentage": self.coverage_percentage,
            "status": self.status.value, "metadata": self.metadata
        }


@dataclass
class TraceLink:
    link_id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    link_type: LinkType = LinkType.FR_TO_SRS
    bidirectional: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    verified_at: Optional[datetime] = None
    status: TraceStatus = TraceStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id, "source_type": self.source_type,
            "source_id": self.source_id, "target_type": self.target_type,
            "target_id": self.target_id, "link_type": self.link_type.value,
            "bidirectional": self.bidirectional,
            "created_at": self.created_at.isoformat(),
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "status": self.status.value, "metadata": self.metadata
        }


class RequirementTraceability:
    """FR → SRS → Code → Test complete bidirectional traceability manager."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.requirements: Dict[str, Requirement] = {}
        self.code_components: Dict[str, CodeComponent] = {}
        self.test_coverage: Dict[str, TestCoverage] = {}
        self.links: List[TraceLink] = []
        self._link_index: Dict[str, List[str]] = {}

    def add_requirement(self, req_id: str, title: str, srs_section: Optional[str] = None, description: str = "", priority: str = "HIGH", metadata: Optional[dict[str, Any]] = None):
        req = Requirement(req_id=req_id, title=title, description=description,
                          srs_section=srs_section, priority=priority, metadata=metadata or {})
        self.requirements[req_id] = req
        if srs_section:
            self.add_link("fr", req_id, "srs", srs_section, LinkType.FR_TO_SRS)
        return req

    def add_code_component(self, file_path, fr_id=None, functions=None, classes=None, line_range=None, metadata=None):
        component = CodeComponent(file_path=file_path, fr_id=fr_id,
                                  functions=functions or [], classes=classes or [],
                                  line_range=line_range, metadata=metadata or {})
        self.code_components[file_path] = component
        if fr_id:
            self.add_link("fr", fr_id, "code", file_path, LinkType.SRS_TO_CODE)
        return component

    def add_test_coverage(self, test_file, fr_id=None, test_functions=None, coverage_percentage=0.0, metadata=None):
        cov = TestCoverage(test_file=test_file, fr_id=fr_id,
                           test_functions=test_functions or [],
                           coverage_percentage=coverage_percentage, metadata=metadata or {})
        self.test_coverage[test_file] = cov
        if fr_id:
            self.add_link("fr", fr_id, "test", test_file, LinkType.CODE_TO_TEST)
        return cov

    def add_link(self, source_type, source_id, target_type, target_id,
                 link_type=LinkType.FR_TO_SRS, bidirectional=True, metadata=None):
        link_id = str(uuid.uuid4())[:8]
        link = TraceLink(link_id=link_id, source_type=source_type, source_id=source_id,
                         target_type=target_type, target_id=target_id,
                         link_type=link_type, bidirectional=bidirectional, metadata=metadata or {})
        self.links.append(link)
        for rid in [source_id, target_id]:
            if rid.startswith(("FR-", "NFR-")):
                self._link_index.setdefault(rid, []).append(link_id)
        return link

    def get_downstream(self, req_id):
        d = {"srs": [], "code": [], "test": [], "quality": []}
        for lnk in self.links:
            if lnk.source_id == req_id:
                d.get(lnk.target_type, []).append(lnk.target_id)
        return d

    def get_upstream(self, component_id):
        u = {"fr": [], "srs": [], "code": []}
        for lnk in self.links:
            if lnk.target_id == component_id:
                u.get(lnk.source_type, []).append(lnk.source_id)
        return u

    def verify_completeness(self):
        total = len(self.requirements)
        frs_srs, frs_code, frs_test, frs_ver = set(), set(), set(), set()
        for lnk in self.links:
            if lnk.source_type == "fr":
                {"srs": frs_srs, "code": frs_code, "test": frs_test}.get(
                    lnk.target_type, set()).add(lnk.source_id)
        for req in self.requirements.values():
            if req.status == TraceStatus.VERIFIED:
                frs_ver.add(req.req_id)
        all_ids = set(self.requirements.keys())
        return {
            "total_requirements": total,
            "srs_coverage": f"{len(frs_srs)/total*100:.1f}%" if total else "0%",
            "code_coverage": f"{len(frs_code)/total*100:.1f}%" if total else "0%",
            "test_coverage": f"{len(frs_test)/total*100:.1f}%" if total else "0%",
            "verification_rate": f"{len(frs_ver)/total*100:.1f}%" if total else "0%",
            "total_links": len(self.links),
            "missing_mappings": {
                "fr_without_srs": list(all_ids - frs_srs),
                "fr_without_code": list(all_ids - frs_code),
                "fr_without_test": list(all_ids - frs_test),
            }
        }

    def get_traceability_matrix(self):
        return [
            {"requirement_id": rid, "title": req.title, "priority": req.priority,
             "status": req.status.value, "srs_section": req.srs_section,
             **self.get_downstream(rid)}
            for rid, req in sorted(self.requirements.items())
        ]

    def export_report(self, format="standard"):
        report = {
            "project_id": self.project_id,
            "exported_at": datetime.now().isoformat(),
            "completeness": self.verify_completeness(),
            "traceability_matrix": self.get_traceability_matrix(),
            "all_links": [line.to_dict() for line in self.links],
        }
        if format == "aspice":
            c = report["completeness"]
            report["aspice_compliance"] = {
                "SWE_3_B_SP1": c["srs_coverage"] == "100.0%",
                "SWE_3_B_SP2": c["code_coverage"] == "100.0%",
                "SWE_3_B_SP3": c["test_coverage"] == "100.0%",
            }
        return report

    def save(self, filepath: str) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.export_report(), f, indent=2, ensure_ascii=False)


def main():  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="Requirement Traceability Manager")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--export")
    parser.add_argument("--format", default="standard", choices=["standard", "aspice"])
    args = parser.parse_args()
    rt = RequirementTraceability(args.project_id)
    if args.verify:
        print(json.dumps(rt.verify_completeness(), indent=2, ensure_ascii=False))
    if args.export:
        rt.save(args.export)
        print(f"Saved to {args.export}")


if __name__ == "__main__":  # pragma: no cover
    main()
