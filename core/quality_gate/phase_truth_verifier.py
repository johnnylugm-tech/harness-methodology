#!/usr/bin/env python3
"""
Phase Truth Verifier
====================
驗證某個 Phase 是否真的通過了。

輸出：
- 自動檢查的分數
- 需要手動確認的項目清單

使用方法:
    from quality_gate.phase_truth_verifier import PhaseTruthVerifier

    verifier = PhaseTruthVerifier("/path/to/project", 3)
    result = verifier.verify()
"""

import os
import subprocess
import json
import sys
import re
from pathlib import Path
from quality_gate.phase_paths import PHASE_ARTIFACT_PATHS
from typing import Dict, List, Tuple

# Ensure methodology-v2 root is in sys.path for enforcement import
_script_dir = Path(__file__).resolve().parent  # quality_gate/
_methodology_root = _script_dir.parent  # methodology-v2/
if str(_methodology_root) not in sys.path:
    sys.path.insert(0, str(_methodology_root))


class PhaseTruthVerifier:
    """Phase 真相驗證器"""

    # 權重配置
    WEIGHTS = {
        "framework_block": 0.35,      # FrameworkEnforcer BLOCK
        "session_log": 0.25,           # Sessions_spawn.log
        "pytest_pass": 0.25,          # pytest 實際通過
        "coverage": 0.15,             # 覆蓋率達標
    }

    def __init__(self, project_root: str, phase: int):
        self.project_root = Path(project_root)
        self.phase = phase
        self.results = {}

    def check_framework_block(self) -> Tuple[bool, float, str]:
        """檢查 FrameworkEnforcer BLOCK"""
        try:
            # Add enforcement to path if needed
            enforcement_path = self.project_root / "enforcement"
            if enforcement_path.exists():
                if str(enforcement_path) not in sys.path:
                    sys.path.insert(0, str(self.project_root))

            from enforcement.framework_enforcer import FrameworkEnforcer
            enforcer = FrameworkEnforcer(str(self.project_root), phase=self.phase)
            result = enforcer.run(level="BLOCK")

            passed = result.passed
            score = 100.0 if passed else 0.0
            details = f"{len(result.block_checks)} 項檢查, {len(result.violations)} 項違規"

            return passed, score, details
        except ImportError as e:
            return False, 0.0, f"無法導入 FrameworkEnforcer: {e}"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_session_log(self) -> Tuple[bool, float, str]:
        """檢查 Sessions_spawn.log"""
        log_file = self.project_root / "sessions_spawn.log"

        if not log_file.exists():
            return False, 0.0, "sessions_spawn.log 不存在"

        try:
            content = log_file.read_text().strip()
            
            # 支援兩種格式：
            # 1. 逐行 JSON（每行一個 JSON object）
            # 2. 單一 JSON（包含 sessions array）
            roles = set()
            sessions = set()
            
            # 嘗試作為整體 JSON 解析
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "sessions" in data:
                    # 格式 2: {"sessions": [...]}
                    for entry in data.get("sessions", []):
                        roles.add(entry.get("role", ""))
                        sessions.add(entry.get("session_id", ""))
                elif isinstance(data, list):
                    # 格式 1: 直接是 list
                    for entry in data:
                        roles.add(entry.get("role", ""))
                        sessions.add(entry.get("session_id", ""))
                else:
                    # 可能是單一 entry
                    roles.add(data.get("role", ""))
                    sessions.add(data.get("session_id", ""))
            except json.JSONDecodeError:
                # 嘗試逐行解析
                lines = [l for l in content.split("\n") if l]
                for line in lines:
                    try:
                        entry = json.loads(line)
                        roles.add(entry.get("role", ""))
                        sessions.add(entry.get("session_id", ""))
                    except:
                        pass

            has_ab = len(roles) >= 2
            has_sessions = len(sessions) >= 2

            score = 100.0 if has_ab and has_sessions else 50.0 if has_sessions else 0.0
            details = f"{len(sessions)} 筆記錄, {len(roles)} 個角色, {len(sessions)} 個 session"

            return has_ab and has_sessions, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_pytest(self) -> Tuple[bool, float, str]:
        """檢查 pytest 實際通過"""
        try:
            result = subprocess.run(
                ["pytest", "--tb=no", "-q"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120
            )

            passed = result.returncode == 0
            score = 100.0 if passed else 0.0
            details = "pytest 全部通過" if passed else "pytest 有失敗"

            return passed, score, details
        except subprocess.TimeoutExpired:
            return False, 0.0, "pytest 執行超時"
        except FileNotFoundError:
            return False, 0.0, "pytest 未找到"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_coverage(self) -> Tuple[bool, float, str]:
        """檢查覆蓋率"""
        threshold = 70

        try:
            result = subprocess.run(
                ["pytest", "--cov=.","--cov-report=term-missing","--tb=no","-q"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120
            )

            # 嘗試從輸出解析覆蓋率
            output = result.stdout + result.stderr

            coverage_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
            if coverage_match:
                coverage = int(coverage_match.group(1))
            else:
                coverage_match = re.search(r" coverage: (\d+)%", output)
                coverage = int(coverage_match.group(1)) if coverage_match else 0

            passed = coverage >= threshold
            score = min(100.0, coverage) if passed else coverage
            details = f"覆蓋率 {coverage}% (門檻 {threshold}%)"

            return passed, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def get_manual_checklist(self) -> List[Dict]:
        """生成需要手動確認的項目"""

        # Phase 目錄映射（支援多種命名慣例）
        phase_dirs = {
            1: ["01-requirements", "01-specify", "requirements", "specify"],
            2: ["02-architecture", "02-plan", "architecture", "plan", "docs"],
            3: ["03-implementation", "03-implement", "implementation", "implement", "src"],
            4: ["04-testing", "04-verify", "testing", "verify"],
            5: ["05-verify", "05-system-test", "verify"],
            6: ["06-quality", "quality"],
            7: ["07-risk", "risk"],
            8: ["08-config", "08-configuration", "config", "configuration"],
        }

        phase_artifacts = {
            1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
            2: ["02-architecture/SAD.md", "02-architecture/adr/001-fastapi-proxy-layer.md", "02-architecture/adr/002-redis-caching-strategy.md", "02-architecture/adr/003-circuit-breaker-resilience.md", "02-architecture/adr/004-text-chunking-strategy.md", "02-architecture/adr/005-cli-click-framework.md", "02-architecture/adr/006-audio-converter-ffmpeg.md"],
            3: [
                # Standard path
                "03-implementation/src/",
                "03-implementation/tests/",
                "03-implementation/COMPLIANCE_MATRIX.md",
                # Alternative: app/ structure (e.g., tts-kokoro-v613)
                "app/",
                "app/processing/",
                "app/synth/",
                "app/infrastructure/",
                "tests/",
            ],
            4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
            5: ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md", "05-verify/MONITORING_PLAN.md"],
            6: ["06-quality/QUALITY_REPORT.md", "06-quality/MONITORING_PLAN.md"],
            7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
            8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
        }

        checklist = []

        # 根據 Phase 添加需要確認的項目（檢查多個可能位置）
        if self.phase in phase_artifacts:
            dirs_to_check = [None] + phase_dirs.get(self.phase, [])  # None = root directory
            
            for artifact in phase_artifacts[self.phase]:
                exists = False
                found_path = artifact
                
                for dir_prefix in dirs_to_check:
                    if dir_prefix:
                        path = self.project_root / dir_prefix / artifact
                    else:
                        path = self.project_root / artifact
                    
                    if path.exists():
                        exists = True
                        found_path = f"{dir_prefix}/{artifact}" if dir_prefix else artifact
                        break
                
                checklist.append({
                    "item": found_path,
                    "status": "✅ 存在" if exists else "❌ 缺失",
                    "action": f"隨機選 1 處，確認內容不是空洞的 template"
                })

        # 通用檢查
        checklist.extend([
            {
                "item": "DEVELOPMENT_LOG.md",
                "status": "✅ 存在" if (self.project_root / "DEVELOPMENT_LOG.md").exists() else "❌ 缺失",
                "action": "查看是否有實際命令輸出（不是截圖，是文字）"
            },
            {
                "item": "sessions_spawn.log",
                "status": "✅ 存在" if (self.project_root / "sessions_spawn.log").exists() else "❌ 缺失",
                "action": "隨機選 1 筆記錄，確認 task 描述合理"
            },
        ])

        return checklist

    def verify(self) -> Dict:
        """執行完整驗證"""

        print("=" * 60)
        print(f"Phase {self.phase} 真相驗證")
        print("=" * 60)
        print()

        # 執行各項檢查（根據 Phase 調整權重）
        # Phase 1-2: 只有 BLOCK + session_log，權重調整
        if self.phase < 3:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.60),
                ("Sessions_spawn.log", self.check_session_log, 0.40),
            ]
        # Phase 3-4: 4 項檢查（含 pytest/coverage）
        elif self.phase <= 4:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.35),
                ("Sessions_spawn.log", self.check_session_log, 0.25),
                ("pytest 實際通過", self.check_pytest, 0.25),
                ("測試覆蓋率達標", self.check_coverage, 0.15),
            ]
        # Phase 5-8: 只有 BLOCK + session_log（無代碼階段）
        else:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.60),
                ("Sessions_spawn.log", self.check_session_log, 0.40),
            ]

        total_score = 0.0
        results = []

        for name, check_func, weight in checks:
            passed, score, details = check_func()

            weighted_score = score * weight
            total_score += weighted_score

            status = "✅" if passed else "❌"
            results.append({
                "name": name,
                "passed": passed,
                "score": score,
                "details": details,
                "weight": weight,
            })

            print(f"{status} {name:<30} {details}")

        print()
        print("=" * 60)
        verdict = "✅ 可能真實" if total_score >= 70 else "❌ 高度可疑"
        print(f"總分：{total_score:.0f}% - {verdict}")
        print("=" * 60)
        print()

        # 輸出需要手動確認的項目
        print("【需要 Johnny 手動確認】")
        print()

        checklist = self.get_manual_checklist()
        for i, item in enumerate(checklist, 1):
            print(f"{i}. [{item['item']}]")
            print(f"   狀態：{item['status']}")
            print(f"   → {item['action']}")
            print()

        return {
            "phase": self.phase,
            "total_score": total_score,
            "passed": total_score >= 70,
            "checks": results,
            "checklist": checklist,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Phase Truth Verifier")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 9),
                        help="Phase number (1-8)")
    parser.add_argument("--project", default=".", help="Project root path")

    args = parser.parse_args()

    verifier = PhaseTruthVerifier(args.project, args.phase)
    result = verifier.verify()

    sys.exit(0 if result["passed"] else 1)
