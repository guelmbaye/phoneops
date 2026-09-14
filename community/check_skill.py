#!/usr/bin/env python3
"""Their validator's checks, runnable before pushing.

`validate_repository.py` stops at the first error, which makes preparing a PR a
sequence of push-and-wait cycles. These are the same requirements, taken from
the validator source rather than CONTRIBUTING.md — which omits both mandatory
reference files (CALLE-AI/awesome-phone-call-agents#452).

    python3 community/check_skill.py community/mission-replan-loop
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml

REQUIRED = ["SKILL.md", "references/safety.md", "references/examples.md"]

#: From the Agent Skills spec the validator implements.
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LIMITS = {"name": 64, "description": 1024, "compatibility": 500}


def check(root: pathlib.Path) -> list[str]:
    problems: list[str] = []

    for required in REQUIRED:
        if not (root / required).exists():
            problems.append(f"missing required file: {required}")
    if not (root / "SKILL.md").exists():
        return problems

    # "Skill directory must not include README.md; move long-form guidance to
    # docs/" — and the rule is not limited to the top level.
    for readme in root.rglob("README.md"):
        problems.append(f"skill directories must not contain a README: {readme}")

    text = (root / "SKILL.md").read_text()
    matched = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not matched:
        problems.append("SKILL.md has no frontmatter")
    else:
        # Their parser reads the block line by line as `key: value`; it is not a
        # YAML parser. A folded scalar (`description: >`) is valid YAML and is
        # rejected as "Invalid frontmatter line", so parsing this correctly here
        # would pass a file their validator refuses. Model theirs, not YAML.
        for line in matched.group(1).splitlines():
            if not re.match(r"^[A-Za-z_][\w-]*:\s*\S.*$", line):
                problems.append(f"invalid frontmatter line (must be `key: value`): {line!r}")

        front = yaml.safe_load(matched.group(1)) or {}
        if front.get("name") != root.resolve().name:
            problems.append(
                f"frontmatter name {front.get('name')!r} != directory {root.resolve().name!r}"
            )
        if not front.get("description"):
            problems.append("frontmatter has no description")
        name = str(front.get("name", ""))
        if name and not NAME.match(name):
            problems.append(
                f"name {name!r} must be lowercase alphanumerics joined by single hyphens"
            )
        for field, limit in LIMITS.items():
            value = front.get(field)
            if isinstance(value, str) and len(value) > limit:
                problems.append(f"{field} is {len(value)} characters, over the {limit} limit")

    for link in re.findall(r"`((?:references|examples|schemas|assets|scripts)/[\w./-]+)`", text):
        if not (root / link).exists():
            problems.append(f"SKILL.md references a missing path: {link}")
        if ".." in link:
            problems.append(f"resource path escapes the skill directory: {link}")

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".yaml", ".json"}:
            continue
        body = path.read_text()
        if re.search(r"[éèêàçùîôû]", body):
            problems.append(f"non-English characters in {path}")
        for number in re.findall(r"\+\d[\d\s().-]{7,}", body):
            digits = re.sub(r"\D", "", number)
            if not digits.startswith("1555"):
                problems.append(f"possibly real phone number in {path}: {number}")

    for path in sorted(root.glob("examples/*.yaml")):
        try:
            yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            problems.append(f"{path} does not parse: {exc}")
    for path in sorted(root.glob("schemas/*.json")):
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{path} does not parse: {exc}")

    return problems


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "community/mission-replan-loop")
    found = check(target)
    print("\n".join(f"  {item}" for item in found) or f"  {target}: all checks pass")
    raise SystemExit(1 if found else 0)
