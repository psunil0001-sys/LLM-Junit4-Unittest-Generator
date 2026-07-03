# ponytail: repo-root sys.path shim — import this module before UnitTest_gen imports
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Re-export for scripts that can import after a manual path insert
ensure_repo_on_path = lambda: None
