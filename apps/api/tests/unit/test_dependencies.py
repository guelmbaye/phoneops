"""Every third-party import must be declared.

`cryptography` was imported by the Telnyx signature check and never added to
requirements.txt. It is present transitively in a development environment, so
the whole suite passed and the container died on startup with
`ModuleNotFoundError` — after the migrations had already run.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Distribution name -> import name, where they differ.
IMPORT_NAME = {"python_dotenv": "dotenv"}


def _declared() -> set[str]:
    names: set[str] = set()
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        dist = line.split("==")[0].split("[")[0].lower().replace("-", "_")
        names.add(IMPORT_NAME.get(dist, dist))
    return names


def _imported() -> set[str]:
    used: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                used |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                used.add(node.module.split(".")[0])
    return used


def test_no_module_is_imported_without_being_declared():
    missing = sorted(_imported() - set(sys.stdlib_module_names) - _declared() - {"app"})

    assert missing == [], f"imported by app/ but absent from requirements.txt: {missing}"
