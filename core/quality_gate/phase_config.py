"""
phase_config.py — Per-phase configuration for IntegratedStagePassGenerator.
"""
from typing import Any, Dict

PHASE_CONFIG: Dict[int, Dict[str, Any]] = {
    1: {"name": "Requirements",    "requires_pytest": False},
    2: {"name": "Architecture",    "requires_pytest": False},
    3: {"name": "Implementation",  "requires_pytest": True},
    4: {"name": "Testing",         "requires_pytest": True},
    5: {"name": "Verification",    "requires_pytest": False},
    6: {"name": "Quality",         "requires_pytest": False},
    7: {"name": "Risk Assessment", "requires_pytest": False},
    8: {"name": "Config Mgmt",     "requires_pytest": False},
    9: {"name": "Maintenance",     "requires_pytest": True},
}
