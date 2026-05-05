"""
Unit tests for CRGBridge.
"""

import os
from pathlib import Path
from unittest.mock import patch
import pytest
from harness.crg_bridge import CRGBridge


class TestCRGBridgeSSIRoot:
    """Tests for _ssi_root() fallback to embedded harness/ssi/."""

    def test_ssi_root_uses_env_var_when_set(self):
        with patch.dict(os.environ, {"SSI_ROOT": "/custom/ssi"}):
            bridge = CRGBridge()
            assert bridge._ssi_root() == "/custom/ssi"

    def test_ssi_root_defaults_to_embedded(self):
        """Without SSI_ROOT env var, should point to harness/ssi/ beside crg_bridge.py."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove SSI_ROOT if present
            os.environ.pop("SSI_ROOT", None)
            bridge = CRGBridge()
            result = bridge._ssi_root()
            assert result.endswith(str(Path("harness") / "ssi")), (
                f"Expected path ending in harness/ssi, got: {result}"
            )

    def test_ssi_root_embedded_path_exists(self):
        """The default embedded path must actually exist."""
        os.environ.pop("SSI_ROOT", None)
        bridge = CRGBridge()
        path = Path(bridge._ssi_root())
        assert path.exists(), f"Embedded ssi path does not exist: {path}"

    def test_ssi_root_env_takes_precedence_over_embedded(self):
        """Env var must win even when harness/ssi/ exists."""
        with patch.dict(os.environ, {"SSI_ROOT": "/override/path"}):
            bridge = CRGBridge()
            assert bridge._ssi_root() == "/override/path"
