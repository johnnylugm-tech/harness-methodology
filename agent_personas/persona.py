#!/usr/bin/env python3
"""
Persona Generator
=================
生成 Agent 人格 Prompt
"""

from typing import Optional, Dict

class Persona:
    """Agent 人格基類"""

    def __init__(self, name: str, role: str, personality: str):
        self.name = name
        self.role = role
        self.personality = personality

    def generate_prompt(self, task: Optional[str] = None) -> str:
        """生成人格 Prompt"""
        prompt = f"""You are {self.name}.
Role: {self.role}
Personality: {self.personality}
"""
        if task:
            prompt += f"\nYour task: {task}\n"
        return prompt

def generate_persona_prompt(persona_type: str, task: Optional[str] = None) -> str:
    """根據類型生成 Prompt"""

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

    persona = personas.get(persona_type.lower())
    if not persona:
        raise ValueError(f"Unknown persona type: {persona_type}")

    return persona.generate_prompt(task)
