# -*- coding: utf-8 -*-
"""Render a Markdown report from a JSON data file and a template.

Usage:
    python render_report.py --data data.json [--template weekly_report.md] --out report.md

- Template placeholders look like ``{field}``; list values render as
  Markdown bullet lists; missing fields are left verbatim (with a
  warning) so problems stay visible instead of being silently dropped.
- Standard library only.
"""
import argparse
import json
import sys
from pathlib import Path


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_list(value: list) -> str:
    return "\n".join(f"- {item}" for item in value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Markdown report.")
    parser.add_argument("--data", required=True, help="JSON data file path")
    parser.add_argument(
        "--template",
        default=str(Path(__file__).resolve().parent.parent / "templates" / "weekly_report.md"),
        help="Template path (default: bundled weekly_report.md)",
    )
    parser.add_argument("--out", required=True, help="Output Markdown path")
    args = parser.parse_args()

    data_path = Path(args.data)
    template_path = Path(args.template)
    out_path = Path(args.out)

    if not data_path.is_file():
        print(f"ERROR: data file not found: {data_path}", file=sys.stderr)
        return 2
    if not template_path.is_file():
        print(f"ERROR: template not found: {template_path}", file=sys.stderr)
        return 2

    data = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("ERROR: data JSON must be an object", file=sys.stderr)
        return 2

    template = template_path.read_text(encoding="utf-8")

    fields = {}
    missing_check = {
        seg.split("}")[0]
        for seg in template.split("{")[1:]
        if "}" in seg and seg.split("}")[0].replace("_", "").isalnum()
    }
    for key in sorted(missing_check):
        if key not in data:
            print(f"WARN: field '{key}' missing from data, placeholder kept")
            fields[key] = "{" + key + "}"
            continue
        value = data[key]
        fields[key] = _render_list(value) if isinstance(value, list) else str(value)

    out_path.write_text(template.format_map(_SafeDict(fields)), encoding="utf-8")
    print(f"OK: wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
