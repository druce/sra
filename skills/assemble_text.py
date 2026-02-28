#!/usr/bin/env python3
"""Assemble intro, body, and conclusion into a single report markdown file."""

import argparse
import json
import sys
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import setup_logging

logger = setup_logging(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble report sections")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument("--workdir", required=True, help="Working directory")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    artifacts = workdir / "artifacts"

    intro_path = artifacts / "intro.md"
    body_path = artifacts / "assembled_body.md"
    conclusion_path = artifacts / "conclusion.md"
    output_path = artifacts / "report_body.md"

    sections = []
    for label, path in [("intro", intro_path), ("body", body_path), ("conclusion", conclusion_path)]:
        if not path.exists():
            logger.error(f"Missing {label} file: {path}")
            return 1
        sections.append(path.read_text().strip())
        logger.info(f"Read {label}: {path} ({len(sections[-1])} chars)")

    assembled = "\n\n".join(sections) + "\n"
    output_path.write_text(assembled)
    logger.info(f"Assembled report written to {output_path} ({len(assembled)} chars)")

    manifest = {
        "status": "complete",
        "artifacts": [
            {
                "name": "body_text",
                "path": "artifacts/report_body.md",
                "format": "md",
                "source": "assembled",
                "summary": f"Full report: intro + body + conclusion ({len(assembled)} chars)",
            }
        ],
        "error": None,
    }
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
