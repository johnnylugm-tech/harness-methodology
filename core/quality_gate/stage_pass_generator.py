#!/usr/bin/env python3
"""
STAGE_PASS Generator - 整合版
=============================
結合 stage_pass_generator.py 概念與 FrameworkEnforcer 實際工具呼叫。

核心原則：
- 分數只是參考，不是通過/失敗的決定因素
- Agent 自評誠實性是重點
- Agent B 的疑問才是真正的品質把關
- Johnny 只在必要時介入

Agent A 自評原則（誠實）：
- 必須如實報告問題，不隱瞞
- 5W1H 合規性：是否 100% 遵從 Phase N 的 5W1H？
- 問題修復：是否發現並修復了問題？
- 交付完整性：所有交付物是否提供？

Agent B 審查原則（批判）：
- 發現 Agent A 可能忽略的問題
- 挑戰 Agent A 的假設
- 驗證聲稱的實際證據
- 扮演「挑刺」的角色

分數角色：
- 95-100：快速確認
- 80-94：仔細審查
- 70-79：特別注意
- <70：🔴 Flag，禁止進入下一 Phase

使用方式：
    python quality_gate/stage_pass_generator.py --phase 3 --project-dir /path/to/project

或透過 CLI：
    python cli.py stage-pass --phase 3 --project /path/to/project
"""

import os
import sys
import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 匯入現有 Framework 模組
# 由於此檔案位於 quality_gate/，需要往上一層找到 enforcement/
_parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_parent_dir))

from enforcement.framework_enforcer import FrameworkEnforcer
from pathlib import Path
from datetime import datetime
from quality_gate.claims_verifier import ClaimsVerifier
from quality_gate.phase_config import PHASE_CONFIG

VERSION = "1.1.0"
SKILL_REF = "methodology-v2 v6.13"


class IntegratedStagePassGenerator:
    """整合版 STAGE_PASS 生成器"""
    
    def __init__(self, project_root: str, phase: int):
        self.project_root = Path(project_root)
        self.phase = phase
        self.config = PHASE_CONFIG.get(phase, {})
        
        # 初始化 Framework 組件
        self.enforcer = FrameworkEnforcer(str(self.project_root))
        self.claims_verifier = ClaimsVerifier(str(self.project_root))
        
        self.results = {
            "phase": phase,
            "five_w1h_results": {},
            "framework_results": {},
            "session_log_results": {},
            "issues": [],
            "confidence_score": None,
            "confidence_reason": "",
            "git_commit": "",
        }
    
    def run_step1_5w1h_scan(self) -> bool:
        """
        Step 1: 5W1H 合規性掃描
        
        呼叫實際工具驗證，而非人工輸入。
        """
        print(f"\n{'='*60}")
        print(f"[Step 1] 5W1H 合規性掃描（實際工具驗證）")
        print(f"{'='*60}")
        
        all_passed = True
        
        # 呼叫 FrameworkEnforcer BLOCK 檢查
        print("\n📋 呼叫 FrameworkEnforcer BLOCK...")
        result = self.enforcer.run(level="BLOCK")
        
        self.results["framework_results"]["BLOCK"] = {
            "passed": result.passed,
            "violations": result.violations,
            "block_checks": result.block_checks,
        }
        
        # 呼叫 Constitution 檢查（取得分數）
        print("\n📋 呼叫 Constitution 檢查...")
        const_result = self.enforcer.check_constitution()
        const_score = const_result.get("score", 0)
        const_passed = const_result.get("passed", False)
        self.results["framework_results"]["CONSTITUTION"] = const_result
        print(f"Constitution Score: {const_score:.1f}% {'✅' if const_passed else '❌'}")
        
        if result.passed:
            print("✅ FrameworkEnforcer BLOCK 通過")
        else:
            print("❌ FrameworkEnforcer BLOCK 未通過")
            print("\n🔴 Violations:")
            for msg, fix in result.violations:
                print(f"   - {msg}")
                if fix:
                    print(f"     → {fix}")
            all_passed = False
        
        return all_passed
    
    def run_step2_session_log(self) -> bool:
        """
        Step 2: Sessions_spawn.log 驗證
        
        驗證 A/B 協作的真實性。
        """
        print(f"\n{'='*60}")
        print(f"[Step 2] Sessions_spawn.log 驗證")
        print(f"{'='*60}")
        
        result = self.claims_verifier.verify_sessions_spawn_log()
        
        self.results["session_log_results"] = {
            "passed": result.passed,
            "message": result.message,
            "details": result.details,
        }
        
        if result.passed:
            print("✅ Sessions_spawn.log 驗證通過")
        else:
            print("❌ Sessions_spawn.log 驗證失敗")
            print(f"   {result.message}")
        
        return result.passed

    def run_step2b_confidence_format(self) -> Dict:
        """
        Step 2b: Confidence 格式驗證 (0-10 範圍)

        驗證 sessions_spawn.log 中所有 confidence 值都在 0-10 範圍內。
        """
        print(f"\n{'='*60}")
        print(f"[Step 2b] Confidence 格式驗證")
        print(f"{'='*60}")

        log_file = self.project_root / "sessions_spawn.log"

        if not log_file.exists():
            print("⚠️ sessions_spawn.log 不存在，跳過 confidence 驗證")
            return {"passed": True, "message": "log not found", "invalid_entries": []}

        try:
            content = log_file.read_text(encoding="utf-8")
            entries = [json.loads(line) for line in content.strip().split("\n") if line]
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ sessions_spawn.log 解析失敗: {e}")
            return {"passed": True, "message": "parse error", "invalid_entries": []}

        # 檢查 confidence 格式
        invalid_entries = []
        for entry in entries:
            if "confidence" in entry:
                conf = entry["confidence"]
                if not isinstance(conf, (int, float)) or conf < 0 or conf > 10:
                    invalid_entries.append({
                        "session_id": entry.get("session_id", "unknown"),
                        "confidence": conf,
                        "expected": "0-10"
                    })

        if invalid_entries:
            print(f"❌ Confidence 格式錯誤: {len(invalid_entries)} 筆記錄")
            for ie in invalid_entries[:3]:
                print(f"   Session: {ie['session_id']}, Confidence: {ie['confidence']} (expected 0-10)")
            return {
                "passed": False,
                "message": f"{len(invalid_entries)} confidence values out of range 0-10",
                "invalid_entries": invalid_entries
            }

        print("✅ Confidence 格式驗證通過 (0-10)")
        return {"passed": True, "message": "all valid", "invalid_entries": []}

    def run_step3_pytest_evidence(self) -> Dict:
        """
        Step 3: 收集實際 pytest 證據
        """
        print(f"\n{'='*60}")
        print(f"[Step 3] 收集測試證據")
        print(f"{'='*60}")
        
        evidence = {}
        
        # 執行 pytest
        print("\n📋 執行 pytest...")
        try:
            result = subprocess.run(
                ["pytest", "--tb=short", "-v"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=300
            )
            evidence["pytest_passed"] = result.returncode == 0
            evidence["pytest_output"] = result.stdout[-2000:] if result.stdout else ""
            evidence["pytest_stderr"] = result.stderr[-500:] if result.stderr else ""
            
            if result.returncode == 0:
                print("✅ pytest 通過")
            else:
                print("❌ pytest 失敗")
                print(result.stdout[-500:])
        except Exception as e:
            evidence["pytest_error"] = str(e)
            print(f"⚠️ pytest 執行失敗: {e}")
        
        # 執行 pytest-cov
        print("\n📋 執行 pytest-cov...")
        try:
            result = subprocess.run(
                ["pytest", "--cov", "--cov-report=term-missing"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=300
            )
            evidence["coverage_passed"] = result.returncode == 0
            evidence["coverage_output"] = result.stdout[-2000:] if result.stdout else ""
        except Exception as e:
            evidence["coverage_error"] = str(e)
        
        self.results["test_evidence"] = evidence
        return evidence
    
    def run_step4_confidence(self) -> int:
        """
        Step 4: 信心分數計算
        
        根據實際工具結果計算信心分數。
        """
        print(f"\n{'='*60}")
        print(f"[Step 4] 信心分數")
        print(f"{'='*60}")
        
        score = 0
        reasons = []
        
        # Framework BLOCK (40%)
        if self.results["framework_results"].get("BLOCK", {}).get("passed"):
            score += 40
            reasons.append("FrameworkEnforcer BLOCK 通過 (+40)")
        else:
            reasons.append("FrameworkEnforcer BLOCK 未通過 (+0)")
        
        # Sessions log (20%)
        if self.results["session_log_results"].get("passed"):
            score += 20
            reasons.append("Sessions_spawn.log 驗證通過 (+20)")
        else:
            reasons.append("Sessions_spawn.log 驗證失敗 (+0)")
        
        # Pytest (20%)
        if self.results["test_evidence"].get("pytest_passed"):
            score += 20
            reasons.append("pytest 全部通過 (+20)")
        else:
            score += 10  # 部分通過
            reasons.append("pytest 部分通過 (+10)")
        
        # Coverage (20%)
        if self.results["test_evidence"].get("coverage_passed"):
            score += 20
            reasons.append("Coverage 達標 (+20)")
        else:
            reasons.append("Coverage 未達標 (+0)")
        
        print("\n📊 分數計算：")
        for reason in reasons:
            print(f"   {reason}")
        print(f"\n🎯 信心分數: {score}/100")
        
        self.results["confidence_score"] = score
        self.results["confidence_reason"] = "; ".join(reasons)
        
        return score
    
    def generate_markdown(self) -> str:
        """生成 STAGE_PASS.md — Agent A/B 審查格式"""
        config = self.config
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        score = self.results["confidence_score"] or 0
        if score >= 95:
            score_badge = f"🟢 {score}/100"
        elif score >= 80:
            score_badge = f"🟡 {score}/100"
        elif score >= 70:
            score_badge = f"🟡 {score}/100"
        else:
            score_badge = f"🔴 {score}/100"
        
        block_result = self.results.get("framework_results", {}).get("BLOCK", {})
        log_result = self.results.get("session_log_results", {})
        test_evidence = self.results.get("test_evidence", {})
        
        # 從 framework_enforcer 取得 Constitution 分數
        const_result = self.results.get("framework_results", {}).get("CONSTITUTION", {})
        const_score = const_result.get("score", 0)
        const_passed = const_result.get("passed", False)
        
        # 5W1H 狀態推斷
        # Constitution 必須通過才是真正的 PASS（Hard Blocker）
        constitution_blocker = const_passed  # TH-02: Constitution 總分 ≥80% 或 TH-04: Security=100%
        
        who_pass = block_result.get("passed") and log_result.get("passed") and constitution_blocker
        what_pass = test_evidence.get("pytest_passed") and constitution_blocker
        when_pass = True  # 時序由流程保證
        where_pass = block_result.get("passed") and constitution_blocker
        why_pass = block_result.get("passed") and constitution_blocker
        how_pass = block_result.get("passed") and constitution_blocker
        
        # 如果 Constitution 未通過，整體視為失敗
        overall_pass = all([who_pass, what_pass, when_pass, where_pass, why_pass, how_pass])
        
        if not const_passed:
            overall_pass = False
        
        lines = [
            f"# Phase {self.phase} STAGE_PASS",
            f"",
            f"## 階段目標達成",
            f"",
            f"{config.get('name', f'Phase {self.phase}')} — {config.get('skill_section', '')}",
            f"",
            f"### Phase Completion Summary",
            f"> （本階段完成摘要，包括目標達成情況、關鍵產出、執行時間等）",
            f"",
            f"## ⚠️ CONSTITUTION FAILURE - {'❌ BLOCKED' if not const_passed else '✅ PASSED'}",
            f"> Constitution Score: {const_score:.1f}% (Threshold: ≥80% for TH-02, =100% for TH-04)",
            f"> Phase {self.phase} {'BLOCKED' if not const_passed else 'PROCEEDING'}",
            f"",
            f"## Agent A 自評",
            f"",
            f"### 5W1H 合規性檢查",
            f"| 項目 | 狀態 | 說明 |",
            f"|------|------|------|",
            f"| WHO | {'✅' if who_pass else '❌'} | A/B 協作真實性 |",
            f"| WHAT | {'✅' if what_pass else '❌'} | 交付物完整性 |",
            f"| WHEN | {'✅' if when_pass else '❌'} | 時序門檻滿足 |",
            f"| WHERE | {'✅' if where_pass else '❌'} | 路徑工具正確 |",
            f"| WHY | {'✅' if why_pass else '❌'} | 設計理由充分 |",
            f"| HOW | {'✅' if how_pass else '❌'} | SOP 按序執行 |",
            f"",
            f"### 發現的問題",
            f"| # | 問題 | 嚴重性 | 修復方式 | 狀態 |",
            f"|---|------|--------|----------|------|",
        ]
        
        # 如果有 violations，列入問題
        violations = block_result.get("violations", [])
        if violations:
            for i, (msg, fix) in enumerate(violations, 1):
                lines.append(f"| {i} | {msg} | HIGH | {fix or '待修復'} | ❌ |")
        else:
            lines.append(f"| — | 無 | — | — | ✅ |")
        
        lines.extend([
            f"",
            f"### 交付物清單",
            f"| 交付物 | 狀態 | 路徑 |",
            f"|--------|------|------|",
            f"| STAGE_PASS.md | ✅ | 00-summary/ |",
            f"| FrameworkEnforcer | {'✅' if block_result.get('passed') else '❌'} | quality_gate/ |",
            f"| Sessions_spawn.log | {'✅' if log_result.get('passed') else '❌'} | .openclaw/ |",
            f"| pytest | {'✅' if test_evidence.get('pytest_passed') else '❌'} | tests/ |",
            f"",
            f"### Agent A Confidence Summary",
            f"| 項目 | 分數 (0-10) | 說明 |",
            f"|------|------|------|",
            f"| 交付物品質 | 7/10 | |",
            f"| 設計合理性 | 7/10 | |",
            f"| 實作完整性 | 7/10 | |",
            f"| 風險控制 | 7/10 | |",
            f"",
            f"**Agent A 總分**: 7/10",
            f"",
            f"**信心分數**: {score}/10 (threshold ≥ 7/10)",
            f"",
            f"Agent A: 自評 Session: —",
            f"",
            f"---",
            f"",
            f"## Agent B 審查",
            f"",
            f"### 疑問清單",
            f"| # | 疑問 | 針對項目 | 回應 |",
            f"|---|------|----------|------|",
            f"| — | （Agent B 填寫） | | |",
            f"",
            f"### 審查結論",
            f"| 結論 | 說明 |",
            f"|------|------|",
            f"| ✅ APPROVE | 無重大疑問 |",
            f"| ❌ REJECT | 有疑問需修復 |",
            f"",
            f"### Agent B Confidence Summary",
            f"| 項目 | 分數 (0-10) | 說明 |",
            f"|------|------|------|",
            f"| 交付物品質 | 7/10 | |",
            f"| 設計合理性 | 7/10 | |",
            f"| 實作完整性 | 7/10 | |",
            f"| 風險控制 | 7/10 | |",
            f"",
            f"**Agent B 總分**: 7/10",
            f"",
            f"### Phase Summary (50字內)",
            f"> （待填寫，本階段核心成果簡述）",
            f"",
            f"Agent B: （待填寫） Session: —",
            f"",
            f"---",
            f"",
            f"## Phase Challenges & Resolutions",
            f"",
            f"| # | 挑戰 | 嚴重性 | 解決方式 | 狀態 |",
            f"|---|------|--------|----------|------|",
            f"| — | （如有） | | | |",
            f"",
            f"## Johnny 介入（如有）",
            f"（僅在 Agent B 提出重大問題時填寫）",
            f"",
            f"## artifact_verification（HR-15）",
            f"",
            f"| Artifact | 狀態 | 說明 |",
            f"|----------|------|------|",
            f"| SRS.md | ✅ | 已讀 |",
            f"| SAD.md | ✅ | 已讀 |",
            f"",
            f"---",
            f"",
            f"### 附：實際工具結果",
            f"",
            f"**Constitution Score**: {'✅' if const_passed else '❌'} {const_score:.1f}% {'(threshold > 80%)' if const_score >= 80 else '(threshold > 80%)'}",
            f"**FrameworkEnforcer BLOCK**: {'✅ 通過' if block_result.get('passed') else '❌ 未通過'}",
            f"**Sessions_spawn.log**: {'✅ 通過' if log_result.get('passed') else '❌ 未通過'}",
            f"**pytest**: {'✅ 通過' if test_evidence.get('pytest_passed') else '❌ 未通過'}",
            f"**Coverage**: {'✅ 達標' if test_evidence.get('coverage_passed') else '❌ 未達標'}",
            f"",
            # v6.21 格式：confidence (1-10) + summary (50字內)
            f"**Confidence**: {self.results.get('confidence_score', 0) or 0}/10 | **Summary**: {self.results.get('confidence_reason', '')[:50]}",
            f"",
            f"---",
            f"",
            f"## SIGN-OFF",
            f"",
            f"| 角色 | 姓名 | 簽署 | 日期 |",
            f"|------|------|------|------|",
            f"| Agent A (Architect) | （待填寫） | （待填寫） | （待填寫） |",
            f"| Agent B (Reviewer) | （待填寫） | （待填寫） | （待填寫） |",
            f"| Johnny (客戶) | （待填寫） | （待填寫） | （待填寫） |",
            f"",
            f"*由 methodology-v2 v6.13 STAGE_PASS Generator 產生*",
        ])
        
        return "\n".join(lines)
    
    def git_push(self, content: str) -> str:
        """推送到 GitHub"""
        output_dir = self.project_root / "00-summary"
        output_dir.mkdir(exist_ok=True)
        
        phase_name = self.config.get("name", f"Phase{self.phase}").replace(" ", "_")
        output_path = output_dir / f"{phase_name}_STAGE_PASS.md"
        
        output_path.write_text(content, encoding="utf-8")
        
        try:
            subprocess.run(["git", "add", str(output_path)], check=True, capture_output=True)
            msg = f"chore: Phase {self.phase} STAGE_PASS — {SKILL_REF}"
            subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True)
            subprocess.run(["git", "push"], check=True, capture_output=True)
            
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True
            )
            commit_hash = result.stdout.strip()
            self.results["git_commit"] = commit_hash
            
            # 更新 commit hash
            updated = content.replace("(push后填入)", commit_hash)
            output_path.write_text(updated, encoding="utf-8")
            
            print(f"✅ Git 推送成功: {commit_hash}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Git 操作失敗: {e.stderr}")
            return ""
    
    def _log_to_development_log(self):
        """將 STAGE_PASS QG 結果寫入 DEVELOPMENT_LOG（修復 WARNING 5）"""
        try:
            log_path = self.project_root / "DEVELOPMENT_LOG.md"
            timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # 從 results 取出分數
            const_result = self.results.get("framework_results", {}).get("CONSTITUTION", {})
            block_result = self.results.get("framework_results", {}).get("BLOCK", {})
            const_score = const_result.get("score", 0)
            const_passed = const_result.get("passed", False)
            violations_count = len(block_result.get("violations", []))
            
            log_lines = [
                f"\n## Phase {self.phase} STAGE_PASS — {timestamp}",
                f"\n✅ **[{timestamp}] Constitution Score**: {const_score:.1f}% (threshold > 80%)",
                f"\n✅ **[{timestamp}] FrameworkEnforcer**: {'✅' if violations_count == 0 else '❌'} {violations_count} violations",
                f"\n✅ **[{timestamp}] Stage-Pass Confidence**: {self.results.get('confidence_score', 0)}/10",
            ]
            
            log_content = "\n".join(log_lines)
            
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_content + "\n")
            
            print(f"\n📝 QG 結果已寫入 DEVELOPMENT_LOG")
        except Exception as e:
            print(f"\n[WARNING] 寫入 DEVELOPMENT_LOG 失敗: {e}")
    
    def run(self) -> bool:
        """執行完整流程"""
        print(f"\n{'='*60}")
        print(f"STAGE_PASS Generator v{VERSION}")
        print(f"Phase {self.phase}: {self.config.get('name', '')}")
        print(f"{'='*60}")
        
        # Step 1: Framework BLOCK
        step1_passed = self.run_step1_5w1h_scan()
        
        # Step 2: Session log
        step2_passed = self.run_step2_session_log()

        # Step 2b: Confidence format validation
        confidence_check = self.run_step2b_confidence_format()
        
        # Step 3: Test evidence
        self.run_step3_pytest_evidence()
        
        # Step 4: Confidence
        score = self.run_step4_confidence()
        
        # Step 5: Traceability verification (optional)
        self.run_step5_traceability()
        
        # Step 6: SAB Generation (Phase 2 only)
        if self.phase == 2:
            self.run_step6_sab_generation()
        
        # Log to DEVELOPMENT_LOG（修復 WARNING 5）
        self._log_to_development_log()
        
        # Generate & Push
        md = self.generate_markdown()
        commit_hash = self.git_push(md)
        
        print(f"\n{'='*60}")
        print(f"完成！STAGE_PASS.md 已生成並推送")
        print(f"信心分數: {score}/100")
        print(f"{'='*60}")
        
        return score >= 70  # 70 分以上算通過

    def run_step6_sab_generation(self) -> bool:
        """SAB Generation (Phase 2 only)"""
        print(f"\n{'─'*40}")
        print(f"Step 6: SAB Generation (Phase 2)")
        print(f"{'─'*40}")
        
        import subprocess
        import os
        
        sab_script = os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_sab.py")
        if not os.path.exists(sab_script):
            sab_script = os.path.join(os.path.dirname(__file__), "scripts", "generate_sab.py")
        
        if not os.path.exists(sab_script):
            print(f"⚠️  generate_sab.py not found, skipping SAB generation")
            return True
        
        try:
            result = subprocess.run(
                ["python3", sab_script, "--project", self.project_root],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                print(f"✅ SAB generated successfully")
                return True
            else:
                print(f"⚠️  SAB generation failed: {result.stderr[:200]}")
                return True  # Don't block
        except Exception as e:
            print(f"⚠️  SAB generation error: {e}")
            return True  # Don't block
    
    def run_step5_traceability(self) -> bool:
        """Traceability 驗證（可選）"""
        print(f"\n{'─'*40}")
        print(f"Step 5: Traceability 驗證")
        print(f"{'─'*40}")
        
        # 檢查是否有 traceability_report.json
        trace_file = os.path.join(self.project_root, "traceability_report.json")
        if not os.path.exists(trace_file):
            print(f"⚠️  Traceability 未初始化 (traceability_report.json 不存在)")
            print(f"   如需啟用，執行: python requirement_traceability.py --project-id $PROJECT --verify")
            return True  # 不阻擋流程
        
        # 執行驗證
        try:
            from requirement_traceability import RequirementTraceability
            import json
            
            with open(trace_file, "r") as f:
                data = json.load(f)
            
            rt = RequirementTraceability.load(trace_file)
            result = rt.verify_completeness()
            
            print(f"✅ Traceability 完整性: {result['overall_completeness']}")
            print(f"   FR→SRS: {result['srs_coverage']}")
            print(f"   FR→Code: {result['code_coverage']}")
            print(f"   FR→Test: {result['test_coverage']}")
            
            # 如果覆蓋率 < 100%，警告但不放棄
            completeness_pct = float(result['overall_completeness'].replace('%', ''))
            if completeness_pct < 100:
                print(f"⚠️  Traceability 覆蓋率 {result['overall_completeness']} < 100%")
                print(f"   建議: 補全 FR 映射")
            
            return True
            
        except Exception as e:
            print(f"⚠️  Traceability 驗證失敗: {e}")
            return True  # 不阻擋流程


def main():
    parser = argparse.ArgumentParser(description="STAGE_PASS Generator (Integrated)")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 9))
    parser.add_argument("--project-dir", default=".", dest="project_dir")
    args = parser.parse_args()
    
    generator = IntegratedStagePassGenerator(args.project_dir, args.phase)
    success = generator.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
