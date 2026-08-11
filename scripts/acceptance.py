#!/usr/bin/env python3
"""The deterministic sensor a room clears against.

This is the only place in the run loop that runs anything outside the process,
and the only place a verdict can come from something other than the save. That
isolation is the point: everywhere else, "did this room clear" is arithmetic on
data the engine owns; here it is the repo's own test command answering.

Content never carries a shell string. The symbolic name in `acceptance.cmd` is
resolved against `.spire/ascension.json`, which `/spire:ascend` wrote from the
class YAML — so the allowlist is a file the user already approved, and this
module never parses YAML or invents a command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402
from runstate import Run  # noqa: E402


def resolve_command(repo: str, symbol: str) -> str | None:
    """Map a symbolic acceptance command onto this repo's real one.

    Content never carries a shell string. `/spire:ascend` already wrote the
    repo's lint and test commands into .spire/ascension.json after resolving
    them from the class YAML, so that file is the allowlist — which satisfies
    mcp-client.md's rule that acceptance commands equal the class `commands.*`
    or are user-confirmed, without this module ever parsing YAML.
    """
    config_path = paths.ascension_path(repo)
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    value = config.get(f"{symbol}_cmd")
    return value if isinstance(value, str) and value.strip() else None


def run_acceptance(run: Run, room: dict) -> dict:
    """Execute the room's acceptance check and report honestly.

    "Honestly" is doing work here. `content/enemies.json` opens by saying every
    entry needs a checkable acceptance, and that a room clearing on human
    judgement is an event rather than a fight. So a type this function cannot
    evaluate must say *that*, not `manual` — reporting a judgement call for a
    check that was simply never built tells the player the room is one kind of
    thing when it is another, which is the same dishonesty the sensor rule on
    intents exists to prevent.
    """
    acceptance = room.get("acceptance") or {}
    kind = acceptance.get("type")

    if kind == "file_exists":
        path = acceptance.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"result": "unconfigured", "detail": acceptance.get("expect", ""),
                    "reason": "This gate checks for a file but names no path."}
        target = os.path.normpath(os.path.join(run.repo, path))
        found = os.path.exists(target)
        return {"result": "pass" if found else "fail", "path": path,
                "detail": acceptance.get("expect", ""),
                "reason": f"{path} {'exists' if found else 'is not there'}."}

    if kind != "command":
        if kind:
            return {"result": "unconfigured", "detail": acceptance.get("expect", ""),
                    "reason": f"This gate declares {kind}, which the engine does not evaluate yet."}
        return {"result": "manual", "detail": acceptance.get("expect", ""),
                "reason": "This room clears on a judgement call, not a command."}

    command = resolve_command(run.repo, acceptance.get("cmd", ""))
    if command is None:
        return {"result": "unconfigured", "detail": acceptance.get("cmd", ""),
                "reason": "No command configured for this gate. Run /spire:ascend to bind one."}

    repeats = int(acceptance.get("repeat", 1))
    tail = ""
    for _ in range(max(1, repeats)):
        try:
            proc = subprocess.run(
                command, shell=True, cwd=run.repo, capture_output=True,
                text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {"result": "fail", "command": command, "reason": "timed out after 300s"}
        except OSError as exc:
            return {"result": "fail", "command": command, "reason": str(exc)}
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        if proc.returncode != 0:
            return {"result": "fail", "command": command, "exit_code": proc.returncode,
                    "log": tail}
    return {"result": "pass", "command": command, "exit_code": 0, "log": tail}
