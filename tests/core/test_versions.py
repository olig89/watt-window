"""The page's version must match the integration's, or the reload banner lies."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / "custom_components" / "watt_window"


def test_panel_version_matches_manifest():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    js = (ROOT / "www" / "watt-window-panel.js").read_text(encoding="utf-8")
    panel = re.search(r'const PANEL_VERSION = "([^"]+)"', js).group(1)
    assert panel == manifest["version"]


def test_card_version_matches_manifest():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    js = (ROOT / "www" / "watt-window-card.js").read_text(encoding="utf-8")
    card = re.search(r'const CARD_VERSION = "([^"]+)"', js).group(1)
    assert card == manifest["version"]


def test_only_public_restore_state_api():
    # 0.20.2 read RestoreStateData.last_states, which Home Assistant 2026.11 doesn't have:
    # the integration failed to start while the tests (on an older HA) passed.
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "last_states" not in text and "RestoreStateData" not in text, path
