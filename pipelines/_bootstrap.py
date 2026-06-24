"""Make the local research package importable when scripts run from the repo."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_PACKAGE_ROOT = REPO_ROOT / "packages" / "research"

if str(RESEARCH_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_PACKAGE_ROOT))
