"""Structural guard: the detection layer must not be able to see the answers.

Two things are off-limits to anything under ``surveillance/detect/``:

  * ``counterparty_account_id`` -- real venues do not hand you clean counterparty
    attribution. Reading it would let the graph builder recover wash rings by lookup
    rather than by inference, and the reported recall would measure nothing.
  * ground-truth labels -- reading them at detection time is self-grading.

This is enforced by parsing the AST rather than grepping, so a name split across lines,
buried in an f-string, or reached through an attribute chain is still caught.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DETECT_DIR = Path(__file__).resolve().parent.parent / "surveillance" / "detect"

FORBIDDEN_NAMES = {"counterparty_account_id", "ground_truth_path", "read_labels", "ScenarioLabel"}
FORBIDDEN_IMPORT_PREFIXES = (
    "surveillance.generator.ground_truth",
    "surveillance.generator.scenarios",
)


def _detect_modules() -> list[Path]:
    return sorted(p for p in DETECT_DIR.rglob("*.py") if p.name != "__init__.py")


def test_detect_package_exists():
    assert DETECT_DIR.is_dir()


@pytest.mark.parametrize("path", _detect_modules() or [None], ids=lambda p: p.name if p else "none")
def test_detection_modules_cannot_see_labels(path: Path | None):
    if path is None:
        pytest.skip("no detection modules yet; this guard activates from Phase 3 onward")

    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            offenders.append(f"attribute {node.attr!r} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            offenders.append(f"name {node.id!r} (line {node.lineno})")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for bad in FORBIDDEN_NAMES:
                if bad in node.value:
                    offenders.append(f"string containing {bad!r} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(f"import from {node.module!r} (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    offenders.append(f"import {alias.name!r} (line {node.lineno})")

    assert not offenders, (
        f"{path.relative_to(DETECT_DIR.parent.parent)} reaches for ground truth: "
        + "; ".join(offenders)
    )
