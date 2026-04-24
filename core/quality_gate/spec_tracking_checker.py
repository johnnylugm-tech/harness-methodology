#!/usr/bin/env python3
"""
規格追蹤檢查器
檢查 SPEC_TRACKING.md 是否存在且完整

使用方法：
    from quality_gate.spec_tracking_checker import SpecTrackingChecker
    checker = SpecTrackingChecker("/path/to/project")
    result = checker.run()
"""

import os
import re
from typing import Dict, List, Optional
from pathlib import Path


class SpecTrackingChecker:
    """規格追蹤完整性檢查器"""
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        # 支援多個可能的位置
        self.spec_file_candidates = [
            self.project_root / "SPEC_TRACKING.md",
            self.project_root / "01-requirements" / "SPEC_TRACKING.md",
            self.project_root / "01-specify" / "SPEC_TRACKING.md",
            self.project_root / "requirements" / "SPEC_TRACKING.md",
        ]
        self.template_file = Path(__file__).parent.parent / "templates" / "SPEC_TRACKING.md"
        self.spec_file = None
        for candidate in self.spec_file_candidates:
            if candidate.exists():
                self.spec_file = candidate
                break
        if self.spec_file is None:
            self.spec_file = self.spec_file_candidates[0]  # 預設第一個
    
    def check_exists(self) -> bool:
        """檢查 SPEC_TRACKING.md 是否存在"""
        return any(c.exists() for c in self.spec_file_candidates)
    
    def check_completeness(self) -> Dict:
        """檢查規格追蹤完整性"""
        if not self.check_exists():
            return {
                "complete": False,
                "missing": ["SPEC_TRACKING.md 不存在"],
                "errors": []
            }
        
        content = self.spec_file.read_text(encoding="utf-8")
        missing = []
        errors = []
        
        # 檢查核心功能表格是否存在
        if not self._has_table(content, "核心功能"):
            missing.append("核心功能表格")
        
        # 檢查是否有狀態欄位
        if "狀態" not in content:
            missing.append("狀態欄位")
        
        # 檢查是否有更新記錄
        if not self._has_update_log(content):
            missing.append("更新紀錄")
        
        # 檢查所有條目是否有狀態
        entries_without_status = self._find_entries_without_status(content)
        if entries_without_status:
            for entry in entries_without_status:
                missing.append(f"條目缺少狀態: {entry}")
        
        return {
            "complete": len(missing) == 0,
            "missing": missing,
            "errors": errors
        }
    
    def _has_table(self, content: str, table_name: str) -> bool:
        """檢查是否有指定的表格"""
        pattern = rf"{table_name}.*\|.*\|"
        return bool(re.search(pattern, content, re.DOTALL))
    
    def _has_update_log(self, content: str) -> bool:
        """檢查是否有更新紀錄"""
        return "更新紀錄" in content and "日期" in content and "|" in content
    
    def _find_entries_without_status(self, content: str) -> List[str]:
        """找出沒有狀態的條目"""
        entries = []
        # 找到所有表格行
        lines = content.split("\n")
        for line in lines:
            if "|" in line and not line.strip().startswith("|"):
                # 檢查是否是資料行（不是標題行或分隔行）
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4 and not any(x in parts[1] for x in ["規格", "規格要求", "項目"]):
                    # 檢查最後一個非空欄位是否為狀態
                    status_col = None
                    for p in reversed(parts):
                        if p:
                            status_col = p
                            break
                    if status_col and not any(x in status_col for x in ["✅", "⚠️", "❌", "完成", "待處理", "未實現"]):
                        entries.append(parts[1] if len(parts) > 1 else "Unknown")
        return entries
    
    def run(self) -> bool:
        """執行規格追蹤檢查（向後相容，返回布林）"""
        if not self.check_exists():
            return False
        return self.check_completeness()["complete"]
    
    def run_enforcement(self) -> Dict:
        """
        執行規格追蹤檢查（for Enforcement 整合）
        
        Returns:
            Dict with keys:
                - exists: bool
                - completeness: int (0-100)
                - complete: bool
                - missing: List[str]
                - errors: List[str]
        """
        exists = self.check_exists()
        if not exists:
            return {
                "exists": False,
                "completeness": 0,
                "complete": False,
                "missing": ["SPEC_TRACKING.md 不存在"],
                "errors": []
            }
        
        completeness_result = self.check_completeness()
        content = self.spec_file.read_text(encoding="utf-8")
        stats = self._count_status(content)
        
        total = sum(stats.values())
        completed = stats.get("✅ 完成", 0)
        
        # 計算完整度百分比
        completeness_pct = int((completed / max(total, 1)) * 100) if total > 0 else 0
        
        return {
            "exists": True,
            "completeness": completeness_pct,
            "complete": completeness_result["complete"],
            "missing": completeness_result["missing"],
            "errors": completeness_result["errors"],
            "stats": stats
        }
    
    def print_report(self):
        """打印規格追蹤報告"""
        if not self.check_exists():
            print("❌ SPEC_TRACKING.md 不存在")
            print("   執行 'python3 cli.py spec-track init' 初始化")
            return
        
        completeness = self.check_completeness()
        
        print("=" * 50)
        print("規格追蹤報告")
        print("=" * 50)
        
        if completeness["complete"]:
            print("✅ 規格追蹤完整")
        else:
            print("❌ 規格追蹤不完整")
        
        if completeness["missing"]:
            print("\n缺失項目:")
            for item in completeness["missing"]:
                print(f"  • {item}")
        
        # 讀取並顯示狀態統計
        content = self.spec_file.read_text(encoding="utf-8")
        stats = self._count_status(content)
        if stats:
            print("\n狀態統計:")
            for status, count in stats.items():
                print(f"  {status}: {count}")
    
    def _count_status(self, content: str) -> Dict[str, int]:
        """統計各狀態數量"""
        stats = {
            "✅ 完成": 0,
            "⚠️ 待處理": 0,
            "❌ 未實現": 0
        }
        
        for line in content.split("\n"):
            if "✅" in line:
                stats["✅ 完成"] += 1
            elif "⚠️" in line:
                stats["⚠️ 待處理"] += 1
            elif "❌" in line:
                stats["❌ 未實現"] += 1
        
        return stats


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="規格追蹤檢查器")
    parser.add_argument("project_root", nargs="?", default=".",
                       help="專案根目錄 (預設: 目前目錄)")
    parser.add_argument("--json", action="store_true", help="JSON 輸出")
    
    args = parser.parse_args()
    
    checker = SpecTrackingChecker(args.project_root)
    
    if not args.json:
        result = checker.run()
        checker.print_report()
        return 0 if result else 1
    else:
        completeness = checker.check_completeness()
        print(completeness)
        return 0 if completeness["complete"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
