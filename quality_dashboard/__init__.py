"""quality_dashboard -- Auto-research backend (P1-P5, P7, P8).

Modules:
    dashboard           -- QualityDashboard: 9-dimension evaluation, HTML report, trend/hotspot/evolution
    auto_research_loop  -- AutoResearchLoop: iterative automated quality improvement
    agent_auto_research -- AgentDrivenAutoResearch: AI Agent-driven quality improvement
"""

from .dashboard import (
    QualityDashboard,
    DimensionScore,
    IterationResult,
    run_tool,
)

from .auto_research_loop import (
    AutoResearchLoop,
    ImprovementAction,
    ImprovementStrategy,
    CoverageImprovement,
    LintingImprovement,
    ErrorHandlingImprovement,
)

from .agent_auto_research import (
    AgentDrivenAutoResearch,
    AgentResult,
    IterationRecord,
    PROGRAMS,
)

__all__ = [
    # dashboard
    "QualityDashboard",
    "DimensionScore",
    "IterationResult",
    "run_tool",
    # auto_research_loop
    "AutoResearchLoop",
    "ImprovementAction",
    "ImprovementStrategy",
    "CoverageImprovement",
    "LintingImprovement",
    "ErrorHandlingImprovement",
    # agent_auto_research
    "AgentDrivenAutoResearch",
    "AgentResult",
    "IterationRecord",
    "PROGRAMS",
]
