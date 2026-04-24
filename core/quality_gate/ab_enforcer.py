#!/usr/bin/env python3
"""
A/B Enforcement - 強制 A/B 協作驗證

功能：
1. 驗證 Developer ≠ Reviewer（不同 session）
2. 驗證每個 Phase 有 A/B 來回對話
3. 驗證 QA ≠ Developer（Phase 4）

使用方式：
    from quality_gate.ab_enforcer import ABEnforcer
    
    enforcer = ABEnforcer("/path/to/project")
    result = enforcer.verify_developer_reviewer_separation("phase_1")
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ABEnforcer:
    """
    A/B 協作強制驗證器
    
    用於驗證 Developer 和 Reviewer 是否真正協作，
    確保不是同一個人自行完成所有工作。
    """
    
    def __init__(self, project_path: str):
        """
        初始化 ABEnforcer
        
        Args:
            project_path: 專案根目錄路徑
        """
        self.project_path = Path(project_path)
        self.development_log_path = self.project_path / "DEVELOPMENT_LOG.md"
    
    def verify_developer_reviewer_separation(self, phase: str) -> Dict:
        """
        驗證 Developer 與 Reviewer 不是同一人
        
        讀取 DEVELOPMENT_LOG Phase X 的 session_id，
        確認 Developer session ≠ Reviewer session。
        
        Args:
            phase: Phase 識別碼（如 "phase_1", "phase_2"）
            
        Returns:
            Dict: {
                "separated": bool,           # 是否分離
                "developer_session": str,    # Developer session
                "reviewer_session": str,     # Reviewer session
                "details": Dict             # 詳細資訊
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
        
        # 1. 提取該 Phase 的內容
        phase_content = self._extract_phase_content(content, phase)
        
        if not phase_content:
            return {
                "separated": False,
                "developer_session": None,
                "reviewer_session": None,
                "error": f"Phase {phase} not found in DEVELOPMENT_LOG"
            }
        
        # 2. 找出 Developer 和 Reviewer 的 session
        developer_session = self._extract_session(phase_content, "developer")
        reviewer_session = self._extract_session(phase_content, "reviewer")
        
        # 3. 判斷是否分離
        if developer_session and reviewer_session:
            # 檢查 session 是否不同
            # 可能是完整的 session_id，也可能是部分標記
            dev_normalized = self._normalize_session(developer_session)
            rev_normalized = self._normalize_session(reviewer_session)
            
            separated = dev_normalized != rev_normalized and dev_normalized and rev_normalized
        elif developer_session and not reviewer_session:
            # 有 Developer 但沒有 Reviewer - 分離失敗
            separated = False
        elif not developer_session and reviewer_session:
            # 有 Reviewer 但沒有 Developer - 分離失敗
            separated = False
        else:
            # 都沒有找到，視為未分離
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
        驗證 A/B 有實際對話（非單方面審查）
        
        檢查 DEVELOPMENT_LOG 是否有來回對話：
        - 不是只有「Developer 產出」「Reviewer 通過」
        - 而是有「Developer 回應 Reviewer 意見」的記錄
        
        Args:
            phase: Phase 識別碼
            
        Returns:
            Dict: {
                "has_dialogue": bool,      # 是否有對話
                "dialogue_count": int,    # 對話回合數
                "dialogue_examples": List[str],  # 對話範例
                "details": Dict           # 詳細資訊
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
        
        # 1. 提取該 Phase 的內容
        phase_content = self._extract_phase_content(content, phase)
        
        if not phase_content:
            return {
                "has_dialogue": False,
                "dialogue_count": 0,
                "dialogue_examples": [],
                "error": f"Phase {phase} not found in DEVELOPMENT_LOG"
            }
        
        # 2. 尋找來回對話的跡象
        dialogue_indicators = [
            # Developer 回應 Reviewer 意見
            r"回應.*?[Rr]eviewer",
            r"[Rr]eviewer.*?意見.*?修改",
            r"[Rr]eviewer.*?建議.*?採納",
            r"根據.*?[Rr]eviewer.*?調整",
            # 來回標記
            r"→.*?←",  # 來回箭頭
            r"Developer.*?回覆",
            r"[Rr]eviewer.*?回覆",
            # 修正迭代
            r"修正.*?\d+次",
            r"迭代.*?\d+次",
            r"修改.*?次",
            r" Revision \d+",
            r"版本.*?\d+",
            # Reviewer 提出意見
            r"[Rr]eviewer.*?提出",
            r"[Rr]eviewer.*?指出",
            r"[Rr]eviewer.*?發現",
            r"[Rr]eviewer.*?建議",
        ]
        
        dialogue_count = 0
        dialogue_examples = []
        
        for pattern in dialogue_indicators:
            matches = re.findall(pattern, phase_content, re.IGNORECASE)
            if matches:
                dialogue_count += len(matches)
                # 保留前 3 個範例
                for match in matches[:3]:
                    if len(dialogue_examples) < 3:
                        dialogue_examples.append(match.strip()[:100])
        
        # 3. 判斷是否有真正對話
        # 標準：至少有一次來回（有 Reviewer 意見 + Developer 回應）
        has_dialogue = dialogue_count >= 2
        
        # 額外檢查：如果只有 Developer 產出和 Reviewer 通過，沒有來回
        simple_patterns = [
            r"Developer.*?產出",
            r"[Rr]eviewer.*?通過",
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
        驗證 Phase 4 Tester ≠ Phase 3 Developer
        
        確保測試人員與開發人員不同，避免自我測試。
        
        Returns:
            Dict: {
                "separated": bool,           # 是否分離
                "developer_session": str,   # Phase 3 Developer session
                "tester_session": str,      # Phase 4 Tester session
                "details": Dict              # 詳細資訊
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
        
        # 1. 找出 Phase 3 的 Developer session
        phase3_content = self._extract_phase_content(content, "phase_3")
        developer_session = self._extract_session(phase3_content, "developer") if phase3_content else None
        
        # 2. 找出 Phase 4 的 Tester session
        phase4_content = self._extract_phase_content(content, "phase_4")
        tester_session = self._extract_session(phase4_content, "tester") if phase4_content else None
        
        # 如果找不到 Tester，嘗試找 QA
        if not tester_session:
            tester_session = self._extract_session(phase4_content, "qa") if phase4_content else None
        
        # 3. 判斷是否分離
        if developer_session and tester_session:
            dev_normalized = self._normalize_session(developer_session)
            test_normalized = self._normalize_session(tester_session)
            
            separated = dev_normalized != test_normalized and dev_normalized and test_normalized
        elif developer_session and not tester_session:
            # 有 Developer 但沒有 Tester - 視為未分離
            separated = False
        elif not developer_session and tester_session:
            # 有 Tester 但沒有 Developer - 視為未分離
            separated = False
        else:
            # 都沒有找到，視為未分離
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
        執行所有 A/B 驗證檢查
        
        這是主要入口點，執行所有 A/B 協作驗證。
        
        Args:
            phase: Phase 編號
            
        Returns:
            Dict: 包含所有驗證結果的綜合報告
        """
        phase_str = f"phase_{phase}"
        
        return {
            "developer_reviewer_separation": self.verify_developer_reviewer_separation(phase_str),
            "ab_dialogue_exists": self.verify_ab_dialogue_exists(phase_str),
            "qa_not_developer": self.verify_qa_not_developer() if phase == 4 else None
        }
    
    def _extract_phase_content(self, content: str, phase: str) -> str:
        """
        從 DEVELOPMENT_LOG 中提取特定 Phase 的內容
        
        Args:
            content: 完整內容
            phase: Phase 識別碼
            
        Returns:
            str: 該 Phase 的內容
        """
        # 處理 phase_1 -> Phase 1 等格式
        phase_pattern = phase.replace("_", " ").title()
        # 確保 phase_str 格式正確
        phase_str = phase if phase.startswith("phase_") else f"phase_{phase}"
        
        # 嘗試多種匹配模式
        patterns = [
            # Phase 1 / Phase1
            rf"(?:Phase\s*1|Phase1).*?(?=(?:Phase\s*\d|Phase\d|$))",
            rf"##\s*Phase\s*{phase_str.split('_')[1]}.*?(?=##\s*Phase|$)",
            # 找 Phase 標題到下一個 Phase 標題之間的內容
            rf"(?:#{{1,6}}\s*)?[Pp]hase\s*{phase_str.split('_')[1]}.*?(?=(?:#{{1,6}}\s*)?[Pp]hase\s*\d|$)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                return match.group(0)
        
        # 如果無法精確匹配，返回整個 content（保守做法）
        return content
    
    def _extract_session(self, content: str, role: str) -> Optional[str]:
        """
        從內容中提取特定角色的 session
        
        Args:
            content: 內容
            role: 角色（developer, reviewer, tester, qa）
            
        Returns:
            Optional[str]: session 標識
        """
        # 優先查找 session_id
        session_patterns = [
            rf"[Ss]ession[-]?[Ii][Dd][：:]\s*([a-zA-Z0-9-]+)",
            rf"[Rr]untime[：:]\s*[Ss]ub[-]?[Aa]gent.*?[Ss]ession",
            rf"{role}.*?[Ss]ession[：:]\s*([a-zA-Z0-9-]+)",
            rf"[Ss]ub[-]?[Aa]gent.*?{role}.*?([a-zA-Z0-9-]+)",
        ]
        
        for pattern in session_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1)
        
        # 如果沒有 session_id，嘗試找角色名稱作為標記
        role_patterns = [
            rf"[Dd]eveloper\s*[Aa]gent",
            rf"[Rr]eviewer\s*[Aa]gent",
            rf"[Tt]ester\s*[Aa]gent",
            rf"[Qq][Aa]\s*[Aa]gent",
        ]
        
        role_lower = role.lower()
        for pattern in role_patterns:
            if role_lower in pattern.lower():
                if re.search(pattern, content, re.IGNORECASE):
                    return f"inferred_{role_lower}_agent"
        
        return None
    
    def _normalize_session(self, session: str) -> str:
        """
        標準化 session 標識，以便比較
        
        Args:
            session: session 標識
            
        Returns:
            str: 標準化後的標識
        """
        if not session:
            return ""
        
        # 移除特殊字符，轉為小寫
        normalized = re.sub(r'[^a-zA-Z0-9]', '', session.lower())
        
        # 如果是 inferred_ 開頭，保留以便識別
        return normalized


# ===== 快速函式入口 =====

def verify_ab_separation(project_path: str, phase: int) -> Dict:
    """
    快速驗證 Developer 和 Reviewer 分離
    
    Args:
        project_path: 專案根目錄路徑
        phase: Phase 編號
        
    Returns:
        Dict: 驗證結果
    """
    enforcer = ABEnforcer(project_path)
    return enforcer.verify_developer_reviewer_separation(f"phase_{phase}")


def verify_ab_dialogue(project_path: str, phase: int) -> Dict:
    """
    快速驗證 A/B 對話存在
    
    Args:
        project_path: 專案根目錄路徑
        phase: Phase 編號
        
    Returns:
        Dict: 驗證結果
    """
    enforcer = ABEnforcer(project_path)
    return enforcer.verify_ab_dialogue_exists(f"phase_{phase}")


if __name__ == "__main__":
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
    
    # Developer/Reviewer 分離
    sep = enforcer.verify_developer_reviewer_separation(phase_str)
    print(f"Developer/Reviewer Separation: {sep['separated']}")
    print(f"  Developer session: {sep.get('developer_session', 'N/A')}")
    print(f"  Reviewer session: {sep.get('reviewer_session', 'N/A')}")
    
    # A/B 對話
    dial = enforcer.verify_ab_dialogue_exists(phase_str)
    print(f"  Has Dialogue: {dial['has_dialogue']}")
    print(f"  Dialogue Count: {dial['dialogue_count']}")
    
    # Phase 4 特殊檢查
    if phase == 4:
        qa = enforcer.verify_qa_not_developer()
        print(f"QA/Developer Separation: {qa['separated']}")
        print(f"  Developer session: {qa.get('developer_session', 'N/A')}")
        print(f"  Tester session: {qa.get('tester_session', 'N/A')}")