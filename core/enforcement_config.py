#!/usr/bin/env python3
"""
Enforcement Configuration - 統一設定與強制執行。
"""
Enforcement Configuration - 統一設定
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any
import os
import json


class EnforcementMode(Enum):
    LOCAL = "local"
    SELF_HOSTED = "self_hosted"
    CLOUD = "cloud"


class Platform(Enum):
    NONE = "none"
    GITHUB = "github"
    GITLAB = "gitlab"
    JENKINS = "jenkins"
    AZURE = "azure"
    BITBUCKET = "bitbucket"


@dataclass
class EnforcementConfig:
    mode: EnforcementMode = EnforcementMode.LOCAL
    platform: Platform = Platform.NONE
    enforce_on_commit: bool = True
    enforce_on_push: bool = True
    enforce_on_pr: bool = True
    enforce_on_merge: bool = True
    strict_mode: bool = True
    allow_bypass: bool = False
    quality_gate_threshold: float = 90.0
    security_threshold: float = 95.0
    coverage_threshold: float = 80.0
    platform_config: Dict[str, Any] = field(default_factory=dict)
    enable_registry: bool = True
    enable_constitution_check: bool = True
    enable_policy_engine: bool = True

    @classmethod
    def load(cls, config_path: str = ".methodology/enforcement.json") -> "EnforcementConfig":
        env_config = os.environ.get('METHODOLOGY_ENFORCEMENT_CONFIG')
        if env_config:
            return cls.from_json(env_config)
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return cls.from_dict(json.load(f))
        return cls()

    @classmethod
    def from_dict(cls, data: Dict) -> "EnforcementConfig":
        return cls(
            mode=EnforcementMode(data.get('mode', 'local')),
            platform=Platform(data.get('platform', 'none')),
            enforce_on_commit=data.get('enforce_on_commit', True),
            enforce_on_push=data.get('enforce_on_push', True),
            enforce_on_pr=data.get('enforce_on_pr', True),
            enforce_on_merge=data.get('enforce_on_merge', True),
            strict_mode=data.get('strict_mode', True),
            allow_bypass=data.get('allow_bypass', False),
            quality_gate_threshold=data.get('quality_gate_threshold', 90.0),
            security_threshold=data.get('security_threshold', 95.0),
            coverage_threshold=data.get('coverage_threshold', 80.0),
            platform_config=data.get('platform_config', {}),
            enable_registry=data.get('enable_registry', True),
            enable_constitution_check=data.get('enable_constitution_check', True),
            enable_policy_engine=data.get('enable_policy_engine', True),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "EnforcementConfig":
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> Dict:
        return {
            'mode': self.mode.value, 'platform': self.platform.value,
            'enforce_on_commit': self.enforce_on_commit,
            'enforce_on_push': self.enforce_on_push,
            'enforce_on_pr': self.enforce_on_pr,
            'enforce_on_merge': self.enforce_on_merge,
            'strict_mode': self.strict_mode, 'allow_bypass': self.allow_bypass,
            'quality_gate_threshold': self.quality_gate_threshold,
            'security_threshold': self.security_threshold,
            'coverage_threshold': self.coverage_threshold,
            'platform_config': self.platform_config,
            'enable_registry': self.enable_registry,
            'enable_constitution_check': self.enable_constitution_check,
            'enable_policy_engine': self.enable_policy_engine,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def save(self, config_path: str = ".methodology/enforcement.json"):
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w') as f:
            f.write(self.to_json())


class ConfigGenerator:
    @staticmethod
    def local_only() -> EnforcementConfig:
        return EnforcementConfig(
            mode=EnforcementMode.LOCAL, platform=Platform.NONE,
            enforce_on_commit=True, enforce_on_push=False,
            enforce_on_pr=False, enforce_on_merge=False
        )

    @staticmethod
    def github_actions() -> EnforcementConfig:
        return EnforcementConfig(
            mode=EnforcementMode.CLOUD, platform=Platform.GITHUB,
            enforce_on_commit=True, enforce_on_push=True,
            enforce_on_pr=True, enforce_on_merge=True,
            platform_config={'workflow_file': '.github/workflows/enforcement.yml'}
        )

    @staticmethod
    def auto_detect() -> EnforcementConfig:
        if os.environ.get('GITHUB_ACTIONS'):
            return ConfigGenerator.github_actions()
        return ConfigGenerator.local_only()
