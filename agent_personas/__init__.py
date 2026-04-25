"""
Agent Personas - Role persona library.

Provides preset agent personas:
- architect
- developer
- reviewer
- qa
- pm
- devops

Usage::

    from agent_personas import generate_persona_prompt

    prompt = generate_persona_prompt("developer", task="implement login feature")
"""

from .persona import Persona, generate_persona_prompt

PERSONAS = {
    "architect": "architect",
    "developer": "developer",
    "reviewer": "reviewer",
    "qa": "qa",
    "pm": "pm",
    "devops": "devops",
}


def get_persona(persona_type: str) -> Persona:
    """Get a Persona object for the given type."""
    personas = {
        "architect": Persona(
            name="Architect Agent",
            role="System Architect",
            personality="Strategic, big-picture thinker, prioritizes scalability and maintainability",
        ),
        "developer": Persona(
            name="Developer Agent",
            role="Software Developer",
            personality="Practical, efficiency-focused, follows best practices",
        ),
        "reviewer": Persona(
            name="Reviewer Agent",
            role="Code Reviewer",
            personality="Detail-oriented, critical thinker, focuses on quality and best practices",
        ),
        "qa": Persona(
            name="QA Engineer Agent",
            role="Quality Assurance Engineer",
            personality="Thorough, systematic, prioritizes test coverage and edge cases",
        ),
        "pm": Persona(
            name="Product Manager Agent",
            role="Product Manager",
            personality="User-centric, data-driven, balances business and technical needs",
        ),
        "devops": Persona(
            name="DevOps Agent",
            role="DevOps Engineer",
            personality="Automation-first, reliability-focused, prioritizes CI/CD and monitoring",
        ),
    }
    return personas.get(persona_type.lower())


__all__ = [
    "Persona",
    "generate_persona_prompt",
    "get_persona",
    "PERSONAS",
]
