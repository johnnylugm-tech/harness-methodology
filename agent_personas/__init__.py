"""
Agent Personas - 角色人格定義庫

提供預設的 Agent 人格定義，包括：
- Architect: 架構師
- Developer: 開發者
- Reviewer: 審查者
- QAEngineer: QA 工程師
- ProductManager: 產品經理
- DevOps: DevOps 工程師

使用方式：

    from agent_personas import generate_persona_prompt

    # 單純取得 prompt
    prompt = generate_persona_prompt("developer", task="寫登入功能")

    # 使用 spawn_with_persona 自動套用
    from agent_spawner import spawn_with_persona
    session = spawn_with_persona(role="developer", task="寫登入功能")
"""

from .persona import Persona, generate_persona_prompt

# 預設 persona 工廠
PERSONAS = {
    "architect": "architect",
    "developer": "developer",
    "reviewer": "reviewer",
    "qa": "qa",
    "pm": "pm",
    "devops": "devops",
}


def get_persona(persona_type: str) -> Persona:
    """取得指定類型的 Persona 物件"""
    from .persona import Persona

    personas = {
        "architect": Persona(
            name="Architect Agent",
            role="System Architect",
            personality="Strategic, big-picture thinker, prioritizes scalability and maintainability"
        ),
        "developer": Persona(
            name="Developer Agent",
            role="Software Developer",
            personality="Practical, efficiency-focused, follows best practices"
        ),
        "reviewer": Persona(
            name="Reviewer Agent",
            role="Code Reviewer",
            personality="Detail-oriented, critical thinker, focuses on quality and best practices"
        ),
        "qa": Persona(
            name="QA Engineer Agent",
            role="Quality Assurance Engineer",
            personality="Thorough, systematic, prioritizes test coverage and edge cases"
        ),
        "pm": Persona(
            name="Product Manager Agent",
            role="Product Manager",
            personality="User-centric, data-driven, balances business and technical needs"
        ),
        "devops": Persona(
            name="DevOps Agent",
            role="DevOps Engineer",
            personality="Automation-first, reliability-focused, prioritizes CI/CD and monitoring"
        ),
    }

    return personas.get(persona_type.lower())


__all__ = [
    'Persona',
    'generate_persona_prompt',
    'get_persona',
    'PERSONAS',
]
