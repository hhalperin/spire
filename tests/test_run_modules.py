"""The run loop's shape: seven modules, one import direction, one front door.

`scripts/run.py` was one 1562-line file. Splitting it only stays a *split* if two
things hold, and neither is visible in a diff:

  * every name the old module exposed is still reachable as `run.<name>`, so the
    split is a move rather than a new interface, and
  * the siblings never reach back into `run.py`, so the graph stays a line the
    next person can read top to bottom instead of a knot.

Both lists are derived from the source. A hand-written roster of module names or
exported symbols is precisely the shape of test this repo has been bitten by:
it passes forever while the thing it names drifts underneath it.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import run

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

# The run loop, in dependency order. `run.py` sits on top and imports all of them.
RUN_LOOP = ["gamedata", "runstate", "rooms", "acceptance", "events", "rewards", "serialize"]


def tree(module: str) -> ast.Module:
    return ast.parse((SCRIPTS / f"{module}.py").read_text(encoding="utf-8"))


def public_names(module: str) -> set[str]:
    """Every top-level name the module *defines* — not the ones it imports."""
    names = set()
    for node in tree(module).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def imported_modules(module: str) -> set[str]:
    names = set()
    for node in ast.walk(tree(module)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_every_module_in_the_run_loop_exists():
    """The roster above is a claim about the filesystem; hold it to that."""
    for module in RUN_LOOP:
        assert (SCRIPTS / f"{module}.py").is_file(), f"scripts/{module}.py is missing"


def test_run_re_exports_every_name_its_modules_define():
    """`run.<anything>` still resolves, for every name the split moved out.

    Derived from the modules rather than listed here, so a function added to
    `rewards.py` and forgotten in `run.py`'s import block fails immediately
    instead of the day someone reaches for it through the front door.
    """
    for module in RUN_LOOP:
        for name in public_names(module):
            assert hasattr(run, name), (
                f"{module}.{name} is not re-exported by run.py — "
                "add it to the import block and to __all__"
            )


def test_runs_public_roster_is_not_stale():
    """`__all__` is what makes the re-exports survive a lint pass; keep it honest."""
    for name in run.__all__:
        assert hasattr(run, name), f"run.__all__ names {name}, which does not exist"

    defined_here = public_names("run") - {"HANDLERS"}
    for name in defined_here:
        if name.startswith("cmd_") or name == "require_room":
            continue  # dispatch internals, not the front door
        assert name in run.__all__, f"run.py defines {name} but __all__ does not list it"


def test_the_run_loop_never_imports_run():
    """One direction only. `run.py` is the entry point, not a shared library.

    A sibling importing `run` would make the graph circular the moment `run.py`
    imports it back — and Python would report that as an AttributeError on a
    half-initialised module, several frames from the actual mistake.
    """
    for module in RUN_LOOP:
        assert "run" not in imported_modules(module), (
            f"scripts/{module}.py imports run.py — the run loop's siblings must not "
            "depend on the entry point"
        )


def test_every_module_imports_on_its_own():
    """Importing one module must not require `run.py` to have gone first.

    In a *fresh interpreter*, deliberately. This file imports `run` at the top,
    which drags every sibling into `sys.modules`, so an in-process
    `importlib.import_module` here is a cache hit that would pass no matter what
    — the exact vacuous shape the docstring above warns about. A subprocess is
    the only way to actually exercise each module's own bootstrap.

    This is what catches a cycle. Python reports one as an AttributeError on a
    half-initialised module, several frames from the import that caused it.
    """
    for module in RUN_LOOP:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=SCRIPTS.parent, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
        )
        assert proc.returncode == 0, f"import {module} failed:\n{proc.stderr}"
