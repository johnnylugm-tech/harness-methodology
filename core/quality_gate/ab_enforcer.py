#!/usr/bin/env python3
"""
A/B Enforcement - mandatory A/B collaboration verification.

⚠️  DEPRECATED for active HR-01 enforcement (SG-13 from robustness audit).
    The canonical HR-01 self-review check lives in
    `harness_cli.cmd_finalize_gate` (around line 1035) and reads
    `.methodology/sessions_spawn.log` directly. That code path is the
    single source of truth for production A/B separation enforcement.

    This module reads `DEVELOPMENT_LOG.md`, which is NOT the canonical
    A/B audit log used by the pipeline. It is retained because:
      * `tests/test_ab_enforcer.py` and `tests/test_w7_gap_fill.py` rely
        on its API for unit-test coverage of the regex parsing logic.
      * Some downstream scripts may still call it programmatically.

    Do NOT add new call sites. For pipeline integration, use the
    sessions_spawn.log path in cmd_finalize_gate instead.

Features:
1. Verify Developer != Reviewer (different sessions)
2. Verify each Phase has A/B back-and-forth dialogue
3. Verify QA != Developer (Phase 4)

Usage:
    from quality_gate.ab_enforcer import ABEnforcer

    enforcer = ABEnforcer("/path/to/project")
    result = enforcer.verify_developer_reviewer_separation("phase_1")
"""

import re
from pathlib import Path
from typing import Dict, Optional

from core.quality_gate.parsers import DevelopmentLogParser


class ABEnforcer:
    """
    A/B collaboration mandatory verifier
    
    Used to verify that Developer and Reviewer truly collaborate,
    ensuring the work is not done by one person alone.
    """
    
    def __init__(self, project_path: str):
        """
        Initialize ABEnforcer
        
        Args:
            project_path: project root directory path
        """
        self.project_path = Path(project_path)
        self.development_log_path = self.project_path / "DEVELOPMENT_LOG.md"
    
    def verify_developer_reviewer_separation(self, phase: str) -> Dict:
        """
        Verify Developer and Reviewer are not the same person
        
        Read DEVELOPMENT_LOG Phase X session_id,
        confirm Developer session != Reviewer session.
        
        Args:
            phase: Phase identifier (e.g. "phase_1", "phase_2")
            
        Returns:
            Dict: {
                "separated": bool,           # whether separated
                "developer_session": str,    # Developer session
                "reviewer_session": str,     # Reviewer session
                "details": Dict             # detail info
            }
        """
        if not self.development_log_path.exists():
            return {
                "separated": False,
                "developer_session": None,
                "reviewer_session": None,
                "error": "DEVELOPMENT_LOG.md not found"
            }
        
        content = self.development_log_path.read_text(encoding="utf-8")
        
        # 1. Extract content for this Phase
        phase_content = self._extract_phase_content(content, phase)
        
        if not phase_content:
            return {
                "separated": False,
                "developer_session": None,
                "reviewer_session": None,
                "error": f"Phase {phase} not found in DEVELOPMENT_LOG"
            }
        
        # 2. Find Developer and Reviewer sessions
        developer_session = self._extract_session(phase_content, "developer")
        reviewer_session = self._extract_session(phase_content, "reviewer")
        
        # 3. Determine if separated
        if developer_session and reviewer_session:
            # Check if sessions differ
            dev_normalized = self._normalize_session(developer_session)
            rev_normalized = self._normalize_session(reviewer_session)
            
            separated = bool(dev_normalized != rev_normalized and dev_normalized and rev_normalized)
        else:
            separated = False
        
        return {
            "separated": separated,
            "developer_session": developer_session,
            "reviewer_session": reviewer_session,
            "details": {
                "phase": phase,
                "has_developer": bool(developer_session),
                "has_reviewer": bool(reviewer_session)
            }
        }
    
    def verify_ab_dialogue_exists(self, phase: str) -> Dict:
        """
        Verify A/B has actual dialogue (not one-sided review)
        
        Check DEVELOPMENT_LOG for back-and-forth dialogue:
        - Not just "Developer output" "Reviewer approved"
        - But includes "Developer responds to Reviewer feedback" records
        
        Args:
            phase: Phase identifier
            
        Returns:
            Dict: {
                "has_dialogue": bool,      # whether dialogue exists
                "dialogue_count": int,    # number of dialogue rounds
                "dialogue_examples": List[str],  # dialogue examples
                "details": Dict           # detail info
            }
        """
        if not self.development_log_path.exists():
            return {
                "has_dialogue": False,
                "dialogue_count": 0,
                "dialogue_examples": [],
                "error": "DEVELOPMENT_LOG.md not found"
            }
        
        content = self.development_log_path.read_text(encoding="utf-8")
        
        # 1. Extract content for this Phase
        phase_content = self._extract_phase_content(content, phase)
        
        if not phase_content:
            return {
                "has_dialogue": False,
                "dialogue_count": 0,
                "dialogue_examples": [],
                "error": f"Phase {phase} not found in DEVELOPMENT_LOG"
            }
        
        # 2. Search for signs of back-and-forth dialogue
        dialogue_indicators = [
            # Developer responds to Reviewer feedback
            r"responds.*?[Rr]eviewer",
            r"[Rr]eviewer.*?feedback.*?revised",
            r"[Rr]eviewer.*?suggestion.*?adopted",
            r"based.*?[Rr]eviewer.*?adjusted",
            # Round-trip markers
            r"→.*?←",  # round-trip arrows
            r"Developer.*?replied",
            r"[Rr]eviewer.*?replied",
            # Fix iterations
            r"fix.*?\d+.*?time",
            r"iteration.*?\d+",
            r"revision.*?time",
            r" Revision \d+",
            r"version.*?\d+",
            # Reviewer raises feedback
            r"[Rr]eviewer.*?raised",
            r"[Rr]eviewer.*?pointed out",
            r"[Rr]eviewer.*?found",
            r"[Rr]eviewer.*?suggested",
        ]
        
        dialogue_count = 0
        dialogue_examples: list[str] = []
        
        for pattern in dialogue_indicators:
            matches = re.findall(pattern, phase_content, re.IGNORECASE)
            if matches:
                dialogue_count += len(matches)
                # Keep first 3 examples
                for match in matches[:3]:
                    if len(dialogue_examples) < 3:
                        dialogue_examples.append(match.strip()[:100])
        
        # 3. Determine if there is genuine dialogue
        # Criterion: at least one round-trip (Reviewer feedback + Developer response)
        has_dialogue = dialogue_count >= 2
        
        # Extra check: only Developer output and Reviewer approved, no round-trip
        simple_patterns = [
            r"Developer.*?output",
            r"[Rr]eviewer.*?approved",
        ]
        has_simple_only = all(re.search(p, phase_content, re.IGNORECASE) for p in simple_patterns)
        
        if has_simple_only and dialogue_count < 2:
            has_dialogue = False
        
        return {
            "has_dialogue": has_dialogue,
            "dialogue_count": dialogue_count,
            "dialogue_examples": dialogue_examples,
            "details": {
                "phase": phase,
                "has_simple_production": has_simple_only
            }
        }
    
    def verify_qa_not_developer(self) -> Dict:
        """
        Verify Phase 4 Tester != Phase 3 Developer
        
        Ensure tester differs from developer to avoid self-testing.
        
        Returns:
            Dict: {
                "separated": bool,           # whether separated
                "developer_session": str,   # Phase 3 Developer session
                "tester_session": str,      # Phase 4 Tester session
                "details": Dict              # detail info
            }
        """
        if not self.development_log_path.exists():
            return {
                "separated": False,
                "developer_session": None,
                "tester_session": None,
                "error": "DEVELOPMENT_LOG.md not found"
            }
        
        content = self.development_log_path.read_text(encoding="utf-8")
        
        # 1. Find Phase 3 Developer session
        phase3_content = self._extract_phase_content(content, "phase_3")
        developer_session = self._extract_session(phase3_content, "developer") if phase3_content else None
        
        # 2. Find Phase 4 Tester session
        phase4_content = self._extract_phase_content(content, "phase_4")
        tester_session = self._extract_session(phase4_content, "tester") if phase4_content else None
        
        # If Tester not found, try QA
        if not tester_session:
            tester_session = self._extract_session(phase4_content, "qa") if phase4_content else None
        
        # 3. Determine if separated
        if developer_session and tester_session:
            dev_normalized = self._normalize_session(developer_session)
            test_normalized = self._normalize_session(tester_session)
            
            separated = bool(dev_normalized != test_normalized and dev_normalized and test_normalized)
        elif developer_session and not tester_session:
            separated = False
        elif not developer_session and tester_session:
            separated = False
        else:
            separated = False
        
        return {
            "separated": separated,
            "developer_session": developer_session,
            "tester_session": tester_session,
            "details": {
                "phase3_has_developer": bool(developer_session),
                "phase4_has_tester": bool(tester_session)
            }
        }
    
    def verify_all_ab_checks(self, phase: int) -> Dict:
        """
        Run all A/B verification checks
        
        This is the main entry point, runs all A/B collaboration verification.
        
        Args:
            phase: Phase number
            
        Returns:
            Dict: comprehensive report containing all verification results
        """
        phase_str = f"phase_{phase}"
        
        return {
            "developer_reviewer_separation": self.verify_developer_reviewer_separation(phase_str),
            "ab_dialogue_exists": self.verify_ab_dialogue_exists(phase_str),
            "qa_not_developer": self.verify_qa_not_developer() if phase == 4 else None
        }
    
    # ------------------------------------------------------------------
    # Parsing — delegated to DevelopmentLogParser (crg-003)
    # ------------------------------------------------------------------

    def _extract_phase_content(self, content: str, phase: str) -> Optional[str]:
        return DevelopmentLogParser.extract_phase_content(content, phase)

    def _extract_session(self, content: str, role: str) -> Optional[str]:
        return DevelopmentLogParser.extract_session(content, role)

    def _normalize_session(self, session: str) -> str:
        return DevelopmentLogParser.normalize_session(session)


# ===== Quick Function Entry Points =====

def verify_ab_separation(project_path: str, phase: int) -> Dict:
    """
    Quick verify Developer and Reviewer separation
    
    Args:
        project_path: project root directory path
        phase: Phase number
        
    Returns:
        Dict: verification result
    """
    enforcer = ABEnforcer(project_path)
    return enforcer.verify_developer_reviewer_separation(f"phase_{phase}")


def verify_ab_dialogue(project_path: str, phase: int) -> Dict:
    """
    Quick verify A/B dialogue exists
    
    Args:
        project_path: project root directory path
        phase: Phase number
        
    Returns:
        Dict: verification result
    """
    enforcer = ABEnforcer(project_path)
    return enforcer.verify_ab_dialogue_exists(f"phase_{phase}")


if __name__ == "__main__":  # pragma: no cover
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python ab_enforcer.py <project_path> <phase>")
        sys.exit(1)
    
    project_path = sys.argv[1]
    phase = int(sys.argv[2])
    
    enforcer = ABEnforcer(project_path)
    phase_str = f"phase_{phase}"
    
    print(f"A/B Enforcement Results for Phase {phase}:")
    print("=" * 50)
    
    # Developer/Reviewer separation
    sep = enforcer.verify_developer_reviewer_separation(phase_str)
    print(f"Developer/Reviewer Separation: {sep['separated']}")
    print(f"  Developer session: {sep.get('developer_session', 'N/A')}")
    print(f"  Reviewer session: {sep.get('reviewer_session', 'N/A')}")
    
    # A/B dialogue
    dial = enforcer.verify_ab_dialogue_exists(phase_str)
    print(f"  Has Dialogue: {dial['has_dialogue']}")
    print(f"  Dialogue Count: {dial['dialogue_count']}")
    
    # Phase 4 special check
    if phase == 4:
        qa = enforcer.verify_qa_not_developer()
        print(f"QA/Developer Separation: {qa['separated']}")
        print(f"  Developer session: {qa.get('developer_session', 'N/A')}")
        print(f"  Tester session: {qa.get('tester_session', 'N/A')}")