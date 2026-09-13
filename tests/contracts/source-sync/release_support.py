"""The tests consume release rules; the release never imports tests or old candidates."""
import importlib.util
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[3] / "docs/development/candidates/source-sync/release-ready/source-sync/v1"
spec = importlib.util.spec_from_file_location("source_sync_v1_rules", RELEASE / "rules.py")
RULES = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RULES)
