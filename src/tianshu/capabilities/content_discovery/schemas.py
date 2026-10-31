"""The builtin action and host port consume the same packaged local wire definition."""

import json
from pathlib import Path

CONTRACT = json.loads(
    (Path(__file__).parents[1] / "plugins/builtin/content-discovery/contract.json").read_text(
        encoding="utf-8"
    )
)
INPUT, OUTPUT = CONTRACT["input"], CONTRACT["output"]
