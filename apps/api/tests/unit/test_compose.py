"""The committed compose files must survive YAML folding.

`command: >` keeps the newline of any more-indented line, so a multi-line
`sh -c "..."` reaches the container as several commands. The first deployment
died on `--forwarded-allow-ips=*: not found`, after the migrations had run.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[4]
COMPOSE = [ROOT / "docker-compose.yml"]


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_no_service_command_contains_a_newline(path):
    document = yaml.safe_load(path.read_text())

    broken = {
        name: service["command"]
        for name, service in document["services"].items()
        if isinstance(service.get("command"), str) and "\n" in service["command"].strip()
    }
    assert broken == {}, f"folded over several lines, sh will split it: {broken}"


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_a_long_running_command_execs_so_signals_reach_it(path):
    """Without `exec`, SIGTERM stops at the shell and the container is killed
    after the timeout instead of shutting down."""
    document = yaml.safe_load(path.read_text())

    for name, service in document["services"].items():
        command = service.get("command")
        if isinstance(command, str) and "uvicorn" in command and "&&" in command:
            assert "exec uvicorn" in command, f"{name}: uvicorn should replace the shell"
