"""Bootstrap tests for the initial audio_cue_locator project structure.

These validate the M1-01 acceptance criteria: the package imports cleanly,
exposes separate Core/Application/Infrastructure areas, and Core stays
independent of FFmpeg, FastAPI, Pydantic-as-transport, and SQLite.
"""

import ast
import importlib
from pathlib import Path

FORBIDDEN_CORE_IMPORT_ROOTS = {
    "ffmpeg",
    "fastapi",
    "pydantic",
    "sqlite3",
}


def test_top_level_package_is_importable():
    module = importlib.import_module("audio_cue_locator")
    assert module is not None


def test_core_application_infrastructure_subpackages_are_importable():
    assert importlib.import_module("audio_cue_locator.core") is not None
    assert importlib.import_module("audio_cue_locator.application") is not None
    assert importlib.import_module("audio_cue_locator.infrastructure") is not None


def test_core_does_not_import_forbidden_dependencies():
    core_package = importlib.import_module("audio_cue_locator.core")
    core_dir = Path(core_package.__file__).parent

    for source_path in core_dir.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {node.module.split(".")[0]} if node.module else set()
            else:
                continue
            forbidden = roots & FORBIDDEN_CORE_IMPORT_ROOTS
            assert not forbidden, (
                f"{source_path} imports forbidden dependency roots: {forbidden}"
            )
