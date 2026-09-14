"""Configuration precedence.

The service reads two env files: a shared one at the repo root and an optional
service-local one. Getting the order wrong is silent — the closest, most
specific file is simply ignored — so it is pinned here.
"""

from __future__ import annotations

from app.config import Settings


def test_the_root_env_file_is_the_baseline_and_is_listed_first():
    # pydantic-settings gives priority to the LAST file, so the shared file must
    # come first for a service-local override to have any effect.
    assert Settings.model_config["env_file"] == ("../../.env", ".env")


def test_a_service_local_env_file_overrides_the_shared_one(tmp_path):
    shared = tmp_path / "root.env"
    shared.write_text("CALLE_DEFAULT_REGION=ROOT\nMAX_REPLANS=7\n")
    local = tmp_path / "local.env"
    local.write_text("CALLE_DEFAULT_REGION=LOCAL\n")

    settings = Settings(_env_file=(shared, local))

    assert settings.CALLE_DEFAULT_REGION == "LOCAL"
    # Keys the local file does not mention still come from the shared baseline.
    assert settings.MAX_REPLANS == 7


def test_real_environment_variables_beat_both_files(tmp_path, monkeypatch):
    shared = tmp_path / "root.env"
    shared.write_text("CALLE_DEFAULT_REGION=ROOT\n")
    local = tmp_path / "local.env"
    local.write_text("CALLE_DEFAULT_REGION=LOCAL\n")
    monkeypatch.setenv("CALLE_DEFAULT_REGION", "SHELL")

    assert Settings(_env_file=(shared, local)).CALLE_DEFAULT_REGION == "SHELL"
