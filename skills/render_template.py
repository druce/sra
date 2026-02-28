#!/usr/bin/env python3
"""
Render a Jinja2 template with variables from JSON files and text files.

Usage:
    ./skills/render_template.py --template templates/assemble_report.md.j2 \
        --output work/AMD_20260225/artifacts/report_body.md \
        --json work/AMD_20260225/artifacts/profile.json \
        --file intro=work/AMD_20260225/artifacts/intro.md \
        --file body=work/AMD_20260225/artifacts/assembled_body.md \
        --file conclusion=work/AMD_20260225/artifacts/conclusion.md

Exit codes:
    0  success
    2  error (missing file, bad template, etc.)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

from jinja2 import Environment, FileSystemLoader, TemplateError

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import ensure_directory, setup_logging  # noqa: E402

logger = setup_logging(__name__)


def load_json_vars(json_path: Path) -> Dict[str, Any]:
    """
    Load variables from a JSON file.

    Args:
        json_path: Path to the JSON file.

    Returns:
        Dictionary of variables from the JSON file.

    Raises:
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with json_path.open("r") as f:
        return json.load(f)


def parse_file_spec(spec: str) -> Tuple[str, str]:
    """
    Parse a key=path spec and read the file contents.

    Args:
        spec: String in the format "key=path/to/file".

    Returns:
        Tuple of (key, file_contents).

    Raises:
        ValueError: If spec is not in key=path format.
        FileNotFoundError: If the file does not exist.
    """
    if "=" not in spec:
        raise ValueError(f"--file argument must be in key=path format, got: {spec}")

    key, path_str = spec.split("=", 1)
    path = Path(path_str)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with path.open("r") as f:
        return key, f.read()


def render_template(
    template_path: Path,
    output_path: Path,
    variables: Dict[str, Any],
) -> Path:
    """
    Render a Jinja2 template with the given variables and write to output.

    Args:
        template_path: Path to the Jinja2 template file.
        output_path: Path where rendered output will be written.
        variables: Dictionary of template variables.

    Returns:
        The output path.

    Raises:
        jinja2.TemplateError: If the template cannot be rendered.
    """
    template_dir = str(template_path.parent)
    template_name = template_path.name

    env = Environment(
        loader=FileSystemLoader(template_dir),
        keep_trailing_newline=True,
    )

    template = env.get_template(template_name)
    rendered = template.render(**variables)

    ensure_directory(output_path.parent)
    with output_path.open("w") as f:
        f.write(rendered)

    return output_path


def main() -> int:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Render a Jinja2 template with variables from JSON and text files.",
    )
    parser.add_argument("--template", required=True, help="Path to Jinja2 template")
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument(
        "--json",
        action="append",
        default=[],
        dest="json_files",
        help="JSON file to load as template variables (can be repeated)",
    )
    parser.add_argument(
        "--file",
        nargs="+",
        default=[],
        dest="file_vars",
        help="File(s) to load as named variables: key=path [key=path ...]",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        import logging

        logging.getLogger().setLevel(logging.DEBUG)

    template_path = Path(args.template)
    output_path = Path(args.output)

    if not template_path.exists():
        logger.error("Template not found: %s", template_path)
        print(json.dumps({"status": "error", "artifacts": [], "error": f"Template not found: {template_path}"}))
        return 2

    # Build variables dict
    variables: Dict[str, Any] = {}

    # Load JSON files (later files override earlier ones)
    for json_path_str in args.json_files:
        json_path = Path(json_path_str)
        if not json_path.exists():
            logger.error("JSON file not found: %s", json_path)
            print(json.dumps({"status": "error", "artifacts": [], "error": f"JSON file not found: {json_path}"}))
            return 2
        logger.info("Loading variables from %s", json_path)
        variables.update(load_json_vars(json_path))

    # Load file variables
    for spec in args.file_vars:
        try:
            key, content = parse_file_spec(spec)
            logger.info("Loaded file variable '%s'", key)
            variables[key] = content
        except (ValueError, FileNotFoundError) as e:
            logger.error(str(e))
            print(json.dumps({"status": "error", "artifacts": [], "error": str(e)}))
            return 2

    # Render
    try:
        logger.info("Rendering template: %s", template_path)
        render_template(template_path, output_path, variables)
        logger.info("✓ Output written to %s", output_path)
    except TemplateError as e:
        logger.error("Template rendering failed: %s", e)
        print(json.dumps({"status": "error", "artifacts": [], "error": f"Template error: {e}"}))
        return 2

    # JSON manifest to stdout
    output_format = output_path.suffix.lstrip(".")
    manifest = {
        "status": "complete",
        "artifacts": [
            {
                "name": "rendered_output",
                "path": str(output_path),
                "format": output_format,
            },
        ],
        "error": None,
    }
    print(json.dumps(manifest))

    return 0


if __name__ == "__main__":
    sys.exit(main())
