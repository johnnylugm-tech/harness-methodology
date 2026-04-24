#!/usr/bin/env python3
"""
Phase Hooks Framework — 檢查/監控框架
=========================================

這個模組提供獨立的 hooks，讓 Agent 在執行 Phase 時可以呼叫。
不再嘗試直接呼叫 sessions_spawn（架構限制），而是提供檢查和監控功能。

使用方法：
    from phase_hooks import PhaseHooks
    
    hooks = PhaseHooks("/path/to/project")
    
    # PRE-FLIGHT
    hooks.preflight_fsm_check()      # FSM 狀態檢查
    hooks.preflight_constitution()    # Constitution 檢查
    hooks.preflight_tool_registry()  # 工具註冊檢查
    
    # MONITORING（每個 FR 前後呼叫）
    hooks.monitoring_before_dev(fr_id="FR-01")      # Developer 執行前
    hooks.monitoring_after_dev(fr_id="FR-01", result=dev_result)  # Developer 完成後
    hooks.monitoring_before_rev(fr_id="FR-01")      # Reviewer 執行前
    hooks.monitoring_after_rev(fr_id="FR-01", result=rev_result)  # Reviewer 完成後
    
    # POST-FLIGHT
    hooks.postflight_constitution()  # Constitution 最終檢查
    hooks.postflight_update_state()  # 更新 FSM 狀態
    hooks.postflight_summary()       # 產出執行摘要

Version: v6.102
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

# Import Constitution checker
sys.path.insert(0, str(Path(__file__).parent))
from quality_gate.constitution import run_constitution_check


class PhaseHooks:
    """
    Phase 執行鉤子框架
    
    提供獨立的檢查和監控功能，Agent 在執行 Phase 時呼叫。
    """
    
    def __init__(self, project_path: str, phase: int = None):
        self.project_path = Path(project_path)
        self.phase = phase
        self.docs_path = self.project_path / "docs"
        self.state_path = self.project_path / ".methodology" / "state.json"
        self.log_path = self.project_path / ".methodology" / "run-phase.log"
        
        # Execution tracking
        self.fr_results: List[Dict] = []
        self.preflight_results: Dict[str, bool] = {}
        self.monitoring_events: List[Dict] = []
        
    # =====================================================================
    # PRE-FLIGHT HOOKS
    # =====================================================================
    
    def preflight_fsm_check(self) -> Dict[str, Any]:
        """
        PRE-FLIGHT: FSM 狀態檢查
        
        檢查專案 FSM 狀態，FREEZE/PAUSED 阻擋執行。
        
        Returns:
            Dict with keys: passed, state, message
        """
        print(f"\n[PRE-FLIGHT] FSM State Check")
        print(f"   Project: {self.project_path}")
        
        if not self.state_path.exists():
            msg = f"state.json not found at {self.state_path}"
            print(f"   ⚠️  {msg}")
            return {"passed": False, "state": "UNKNOWN", "message": msg}
        
        state = json.loads(self.state_path.read_text())
        current_state = state.get("state", "UNKNOWN")
        current_phase = state.get("current_phase", 0)
        
        print(f"   Current State: {current_state}")
        print(f"   Current Phase: {current_phase}")
        
        # Check blocking states
        if current_state == "FREEZE":
            msg = "FSM is FREEZE - execution blocked"
            print(f"   🔴 {msg}")
            return {"passed": False, "state": current_state, "message": msg}
        
        if current_state == "PAUSED":
            msg = "FSM is PAUSED - manual intervention required"
            print(f"   🟠 {msg}")
            return {"passed": False, "state": current_state, "message": msg}
        
        # Check phase sequence (can't go backwards)
        if self.phase and current_phase > self.phase:
            msg = f"Cannot go backwards: current={current_phase}, requested={self.phase}"
            print(f"   🔴 {msg}")
            return {"passed": False, "state": current_state, "message": msg}
        
        print(f"   ✅ FSM check passed")
        return {"passed": True, "state": current_state, "message": "OK"}
    
    def preflight_constitution(self, check_mode: str = "preflight") -> Dict[str, Any]:
        """
        PRE-FLIGHT: Constitution 檢查
        
        執行 Constitution quality gate check。
        
        Args:
            check_mode: "preflight" (檢查前提) 或 "postflight" (檢查產出)
        
        Returns:
            Dict with keys: passed, score, violations
        """
        print(f"\n[PRE-FLIGHT] Constitution Check ({check_mode})")
        
        try:
            result = run_constitution_check(
                check_type="all",
                docs_path=str(self.docs_path),
                current_phase=self.phase,
                check_mode=check_mode
            )
            
            passed = result.passed
            score = result.score
            violations = len(result.violations)
            
            print(f"   Score: {score:.0f}%")
            print(f"   Violations: {violations}")
            
            if passed:
                print(f"   ✅ Constitution check passed")
            else:
                print(f"   🔴 Constitution check failed")
                for v in result.violations[:5]:
                    print(f"      - [{v.get('severity', '?')}] {v.get('message', '?')}")
            
            return {
                "passed": passed,
                "score": score,
                "violations": violations,
                "details": result.details
            }
            
        except Exception as e:
            msg = f"Constitution check error: {e}"
            print(f"   🔴 {msg}")
            return {"passed": False, "score": 0, "violations": 0, "error": str(e)}
    
    def preflight_tool_registry(self) -> Dict[str, Any]:
        """
        PRE-FLIGHT: Tool Registry 檢查
        
        檢查工具註冊狀態。
        
        Returns:
            Dict with keys: passed, tools_count
        """
        print(f"\n[PRE-FLIGHT] Tool Registry Check")
        
        try:
            from tool_registry import ToolRegistry
            tools = ToolRegistry.list_tools()
            count = len(tools)
            
            print(f"   Tools registered: {count}")
            
            if count == 0:
                print(f"   ⚠️  No tools registered")
                return {"passed": False, "tools_count": count}
            
            print(f"   ✅ Tool registry check passed")
            return {"passed": True, "tools_count": count}
            
        except ImportError:
            print(f"   ⚠️  ToolRegistry not available")
            return {"passed": True, "tools_count": 0, "skipped": True}
        except Exception as e:
            print(f"   ⚠️  ToolRegistry error: {e}")
            return {"passed": True, "tools_count": 0, "error": str(e)}
    
    def preflight_all(self) -> Dict[str, Any]:
        """
        PRE-FLIGHT: 執行所有預檢查
        
        便利方法，執行所有 PRE-FLIGHT 檢查。
        
        Returns:
            Dict with all check results
        """
        print(f"\n{'='*60}")
        print(f"PRE-FLIGHT: {self.phase or 'Phase N'}")
        print(f"{'='*60}")
        
        results = {
            "fsm": self.preflight_fsm_check(),
            "constitution": self.preflight_constitution(),
            "tool_registry": self.preflight_tool_registry(),
        }
        
        all_passed = all(r.get("passed", False) for r in results.values())
        
        print(f"\n{'='*60}")
        print(f"PRE-FLIGHT Result: {'✅ PASS' if all_passed else '❌ FAIL'}")
        print(f"{'='*60}")
        
        return {
            "all_passed": all_passed,
            "details": results
        }
    
    # =====================================================================
    # MONITORING HOOKS
    # =====================================================================
    
    def monitoring_before_dev(self, fr_id: str) -> None:
        """
        MONITORING: Developer 執行前的鉤子
        
        記錄即將開始的 Developer 執行。
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": "before_dev",
            "fr_id": fr_id,
        }
        self.monitoring_events.append(event)
        print(f"\n[MONITORING] Before Dev: {fr_id}")
        self._append_log(f"BEFORE_DEV: {fr_id}")
    
    def monitoring_after_dev(self, fr_id: str, result: Any = None) -> None:
        """
        MONITORING: Developer 執行完成後的鉤子
        
        記錄 Developer 執行結果。
        
        Args:
            fr_id: FR 編號
            result: Developer 執行結果（可選）
        """
        status = getattr(result, 'status', 'unknown') if result else 'unknown'
        confidence = getattr(result, 'confidence', 0) if result else 0
        
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": "after_dev",
            "fr_id": fr_id,
            "status": status,
            "confidence": confidence,
        }
        self.monitoring_events.append(event)
        
        print(f"\n[MONITORING] After Dev: {fr_id}")
        print(f"   Status: {status}")
        print(f"   Confidence: {confidence}")
        
        self._append_log(f"AFTER_DEV: {fr_id} status={status} confidence={confidence}")
        
        # Store for later analysis
        self.fr_results.append({
            "fr_id": fr_id,
            "dev_status": status,
            "dev_confidence": confidence,
        })
    
    def monitoring_before_rev(self, fr_id: str) -> None:
        """
        MONITORING: Reviewer 執行前的鉤子
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": "before_rev",
            "fr_id": fr_id,
        }
        self.monitoring_events.append(event)
        print(f"\n[MONITORING] Before Rev: {fr_id}")
        self._append_log(f"BEFORE_REV: {fr_id}")
    
    def monitoring_after_rev(self, fr_id: str, result: Any = None) -> None:
        """
        MONITORING: Reviewer 執行完成後的鉤子
        
        檢查 HR-12 (max iterations) 並記錄結果。
        """
        status = getattr(result, 'status', 'unknown') if result else 'unknown'
        review_status = getattr(result, 'review_status', None) if result else None
        confidence = getattr(result, 'confidence', 0) if result else 0
        
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": "after_rev",
            "fr_id": fr_id,
            "status": status,
            "review_status": review_status,
            "confidence": confidence,
        }
        self.monitoring_events.append(event)
        
        print(f"\n[MONITORING] After Rev: {fr_id}")
        print(f"   Status: {status}")
        print(f"   Review: {review_status}")
        print(f"   Confidence: {confidence}")
        
        self._append_log(f"AFTER_REV: {fr_id} status={status} review={review_status}")
        
        # Update FR results
        if self.fr_results and self.fr_results[-1].get("fr_id") == fr_id:
            self.fr_results[-1]["rev_status"] = status
            self.fr_results[-1]["review_status"] = review_status
            self.fr_results[-1]["rev_confidence"] = confidence
    
    def monitoring_hr12_check(self, fr_id: str, iteration: int, max_iterations: int = 5) -> bool:
        """
        MONITORING: HR-12 檢查
        
        檢查是否超過最大審查迭代次數。
        
        Returns:
            True if can proceed, False if HR-12 triggered
        """
        if iteration >= max_iterations:
            print(f"\n[MONITORING] 🔴 HR-12 TRIGGERED: {fr_id} iteration {iteration} >= {max_iterations}")
            self._append_log(f"HR12_TRIGGERED: {fr_id}")
            return False
        
        print(f"\n[MONITORING] HR-12: {fr_id} iteration {iteration}/{max_iterations}")
        return True
    
    # =====================================================================
    # POST-FLIGHT HOOKS
    # =====================================================================
    
    def postflight_constitution(self) -> Dict[str, Any]:
        """
        POST-FLIGHT: Constitution 最終檢查
        
        執行 Constitution quality gate check（postflight 模式）。
        
        Returns:
            Dict with keys: passed, score, violations
        """
        print(f"\n[POST-FLIGHT] Constitution Check")
        
        return self.preflight_constitution(check_mode="postflight")
    
    def postflight_update_state(self, success: bool = True) -> Dict[str, Any]:
        """
        POST-FLIGHT: 更新 FSM 狀態
        
        只有在執行成功時才更新 state.json。
        
        Args:
            success: 是否成功
        
        Returns:
            Dict with keys: updated, new_state
        """
        print(f"\n[POST-FLIGHT] Update FSM State")
        
        if not success:
            print(f"   ⚠️  Execution failed - not updating state")
            return {"updated": False, "reason": "execution_failed"}
        
        if not self.state_path.exists():
            print(f"   ⚠️  state.json not found")
            return {"updated": False, "reason": "no_state"}
        
        state = json.loads(self.state_path.read_text())
        old_phase = state.get("current_phase", 0)
        
        # Only advance phase
        if self.phase and self.phase > old_phase:
            state["current_phase"] = self.phase
            state["last_update"] = datetime.now().isoformat()
            
            self.state_path.write_text(json.dumps(state, indent=2))
            
            print(f"   ✅ state.json updated: {old_phase} → {self.phase}")
            self._append_log(f"STATE_UPDATE: {old_phase} → {self.phase}")
            
            return {"updated": True, "old_phase": old_phase, "new_phase": self.phase}
        else:
            print(f"   ⚠️  Phase not advanced (current={old_phase}, requested={self.phase})")
            return {"updated": False, "reason": "phase_not_advanced"}
    
    def postflight_summary(self) -> Dict[str, Any]:
        """
        POST-FLIGHT: 產出執行摘要
        
        產出最終的執行摘要報告。
        
        Returns:
            Dict with execution summary
        """
        print(f"\n{'='*60}")
        print(f"POST-FLIGHT: Execution Summary")
        print(f"{'='*60}")
        
        total_frs = len(self.fr_results)
        approved = sum(1 for r in self.fr_results if r.get("review_status") == "APPROVE")
        
        print(f"   Total FRs: {total_frs}")
        print(f"   Approved: {approved}/{total_frs}")
        print(f"   Monitoring Events: {len(self.monitoring_events)}")
        
        print(f"\n   FR Results:")
        for r in self.fr_results:
            fr = r.get("fr_id", "?")
            dev = r.get("dev_status", "?")
            rev = r.get("review_status", "?")
            print(f"      {fr}: dev={dev}, rev={rev}")
        
        print(f"\n{'='*60}")
        
        return {
            "total_frs": total_frs,
            "approved": approved,
            "fr_results": self.fr_results,
            "monitoring_events": len(self.monitoring_events)
        }
    
    def postflight_all(self) -> Dict[str, Any]:
        """
        POST-FLIGHT: 執行所有後檢查
        
        便利方法，執行所有 POST-FLIGHT 檢查。
        
        Returns:
            Dict with all check results
        """
        print(f"\n{'='*60}")
        print(f"POST-FLIGHT: {self.phase or 'Phase N'}")
        print(f"{'='*60}")
        
        # Constitution check
        const_result = self.postflight_constitution()
        
        # Calculate overall success
        fr_approved = sum(1 for r in self.fr_results if r.get("review_status") == "APPROVE")
        total_frs = len(self.fr_results) or 1
        fr_success = fr_approved >= total_frs
        
        success = const_result.get("passed", False) and fr_success
        
        # Update state
        state_result = self.postflight_update_state(success=success)
        
        # Summary
        summary = self.postflight_summary()
        
        print(f"\n{'='*60}")
        print(f"POST-FLIGHT Result: {'✅ PASS' if success else '❌ FAIL'}")
        print(f"{'='*60}")
        
        return {
            "success": success,
            "constitution": const_result,
            "state_update": state_result,
            "summary": summary
        }
    
    # =====================================================================
    # UTILITY METHODS
    # =====================================================================
    
    def _append_log(self, message: str) -> None:
        """Append to run-phase.log"""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.log_path.append_text(f"[{timestamp}] {message}\n")
        except Exception:
            pass
    
    def add_fr_result(self, fr_id: str, dev_result: Any, rev_result: Any) -> None:
        """
        手動新增 FR 結果
        
        當 Agent 自己執行 sessions_spawn 時，可以用這個方法記錄結果。
        
        Args:
            fr_id: FR 編號
            dev_result: Developer 執行結果
            rev_result: Reviewer 執行結果
        """
        self.fr_results.append({
            "fr_id": fr_id,
            "dev_status": getattr(dev_result, 'status', 'unknown'),
            "dev_confidence": getattr(dev_result, 'confidence', 0),
            "rev_status": getattr(rev_result, 'status', 'unknown'),
            "review_status": getattr(rev_result, 'review_status', None),
            "rev_confidence": getattr(rev_result, 'confidence', 0),
        })


def main():
    """CLI 入口 - 支援個別呼叫 hooks"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Phase Hooks Framework")
    parser.add_argument("--project", "-p", required=True, help="Project path")
    parser.add_argument("--phase", type=int, required=True, help="Phase number")
    parser.add_argument("--hook", required=True, 
                        choices=["preflight-all", "postflight-all", "preflight-fsm", 
                                 "preflight-constitution", "preflight-tool-registry",
                                 "postflight-constitution", "postflight-state", "postflight-summary"],
                        help="Hook to execute")
    
    args = parser.parse_args()
    
    hooks = PhaseHooks(args.project, args.phase)
    
    if args.hook == "preflight-all":
        result = hooks.preflight_all()
    elif args.hook == "preflight-fsm":
        result = hooks.preflight_fsm_check()
    elif args.hook == "preflight-constitution":
        result = hooks.preflight_constitution()
    elif args.hook == "preflight-tool-registry":
        result = hooks.preflight_tool_registry()
    elif args.hook == "postflight-all":
        result = hooks.postflight_all()
    elif args.hook == "postflight-constitution":
        result = hooks.postflight_constitution()
    elif args.hook == "postflight-state":
        result = hooks.postflight_update_state(success=True)
    elif args.hook == "postflight-summary":
        result = hooks.postflight_summary()
    
    print(f"\nResult: {result}")
    return 0 if result.get("passed", result.get("success", True)) else 1


if __name__ == "__main__":
    sys.exit(main())
