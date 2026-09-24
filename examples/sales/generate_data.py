"""Regenerate examples/sales/data/sales.xlsx and expected/defects.json.

Usage: python examples/sales/generate_data.py [--rows 5000] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from powerbi_agent.data.samples import generate_sales, write_xlsx

HERE = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    frame, defects = generate_sales(args.rows, seed=args.seed)
    write_xlsx(frame, HERE / "data" / "sales.xlsx")
    expected = HERE / "expected" / "defects.json"
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_text(json.dumps(defects.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {frame.height} rows to {HERE / 'data' / 'sales.xlsx'}")


if __name__ == "__main__":
    main()
