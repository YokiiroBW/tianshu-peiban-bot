"""Update the checked-in wire contracts from an immutable upstream checkout.

Usage: python scripts/sync_snowluma_catalog.py /path/to/SnowLuma
Only public interface metadata is retained; no upstream implementation is copied.
"""

import argparse
import json
import subprocess
from pathlib import Path


def contract(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"description", "examples", "title"}:
                continue
            if key in {
                "properties",
                "patternProperties",
                "$defs",
                "definitions",
                "dependentSchemas",
            }:
                # Property names such as title/description are wire fields, not annotations.
                result[key] = {name: contract(schema) for name, schema in item.items()}
            elif key in {"const", "default", "enum", "dependentRequired"}:
                result[key] = item
            else:
                result[key] = contract(item)
        return result
    if isinstance(value, list):
        return [contract(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(args.checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    source = "packages/mcp/src/generated/catalog.json"
    catalog = json.loads(
        subprocess.check_output(["git", "-C", str(args.checkout), "show", revision + ":" + source])
    )
    entries = []
    for action in catalog["actions"]:
        entries.append(
            {
                "name": action["name"],
                "aliases": action.get("aliases", []),
                "summary": action.get("summary", action["name"]),
                "category": action["category"],
                "read_only": action["readOnly"],
                "stream": action.get("stream", False),
                "input_schema": contract(action["inputSchema"]),
                "output_schema": contract(action.get("returnsSchema", {})),
                "invariants": action.get("invariants", []),
            }
        )
    output = Path(__file__).resolve().parents[1] / "src/tianshu/channels/snowluma/catalog.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "source": "https://github.com/SnowLuma/SnowLuma",
                "revision": revision,
                "source_path": source,
                "actions": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} contracts from {revision}")


if __name__ == "__main__":
    main()
