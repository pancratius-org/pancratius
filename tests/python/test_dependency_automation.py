import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _updates_for(ecosystem: str) -> dict[str, Any]:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    matches = [item for item in config["updates"] if item["package-ecosystem"] == ecosystem]
    assert len(matches) == 1
    return matches[0]


def test_npm_security_updates_repair_the_dependency_graph_together() -> None:
    npm = _updates_for("npm")
    security = npm["groups"]["security"]

    assert security == {
        "applies-to": "security-updates",
        "patterns": ["*"],
    }
    assert "audit/fixtures/**" in npm["exclude-paths"]


def test_uv_updates_cover_both_locked_python_projects() -> None:
    uv = _updates_for("uv")

    assert set(uv["directories"]) == {"/", "/intent-ai"}
    assert uv["groups"]["python"]["patterns"] == ["*"]


def test_uv_linux_lock_matches_the_aqua_binary_layout() -> None:
    lock = tomllib.loads((ROOT / "mise.lock").read_text())
    linux = lock["tools"]["uv"][0]["platforms.linux-x64"]

    assert linux["url"].endswith("uv-x86_64-unknown-linux-musl.tar.gz")
