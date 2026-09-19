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
