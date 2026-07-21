#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lint a Celerp module without installing the app.

Runs the same structural checks the loader runs at startup, so you catch
problems in seconds instead of on a failed boot:
  - the folder has an __init__.py with a PLUGIN_MANIFEST
  - the manifest has the required identity fields and at least one slot/route
  - the module name is not in the reserved `celerp-` namespace
  - no source file imports a protected celerp internal (revenue-gated; the
    loader rejects modules that do)

Usage:  python lint.py path/to/your-module-folder
Exit 0 = clean, 1 = problems (printed).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Kept in sync with celerp/modules/loader.py _PROTECTED_BSL_INTERNALS.
PROTECTED = {
    "celerp.session_gate", "celerp.ai.service", "celerp.ai.quota",
    "celerp.gateway", "celerp.connectors",
}
REQUIRED_FIELDS = ("name", "version", "display_name", "license")
# min_celerp_version is optional, but when set it must be a dotted version so
# the loader's comparison means something.
MIN_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")


def _load_manifest(init_file: Path) -> tuple[dict | None, str | None]:
    """Return (manifest, error). A syntax error in `__init__.py` is the most
    common first mistake, so it is reported by name instead of raised as a
    traceback - the point of this script is to name the problem."""
    try:
        tree = ast.parse(init_file.read_text())
    except SyntaxError as exc:
        return None, f"could not parse the file (line {exc.lineno}: {exc.msg})"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not be read ({exc})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "PLUGIN_MANIFEST":
                    try:
                        return ast.literal_eval(node.value), None
                    except Exception:
                        return None, None
    return None, None


def _protected_imports(py_file: Path) -> set[str]:
    hits: set[str] = set()
    try:
        tree = ast.parse(py_file.read_text())
    except Exception:
        return hits
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for p in PROTECTED:
                if name == p or name.startswith(p + "."):
                    hits.add(p)
    return hits


def lint(folder: Path) -> list[str]:
    problems: list[str] = []
    init_file = folder / "__init__.py"
    if not init_file.exists():
        return [f"{folder}: no __init__.py (a module folder must have one)"]

    manifest, error = _load_manifest(init_file)
    if error is not None:
        return [f"{init_file}: {error}"]
    if manifest is None:
        return [f"{init_file}: no parseable PLUGIN_MANIFEST dict"]

    for field in REQUIRED_FIELDS:
        if not manifest.get(field):
            problems.append(f"manifest missing required field: {field!r}")
    name = str(manifest.get("name", ""))
    if name.startswith("celerp-"):
        problems.append(f"name {name!r} uses the reserved `celerp-` namespace - "
                        "prefix with your own vendor name")
    # Celerp installs a module under its manifest name, whatever the folder is
    # called, so renaming only one of the two lands the module somewhere the
    # author is not looking (or on top of the module they copied).
    if name and folder.name != name:
        problems.append(f"folder name {folder.name!r} does not match "
                        f"PLUGIN_MANIFEST['name'] {name!r} - Celerp installs modules "
                        f"under the manifest name, so this would install as {name!r}")
    min_version = manifest.get("min_celerp_version")
    if min_version is not None and not MIN_VERSION_RE.match(str(min_version)):
        problems.append(f"min_celerp_version {min_version!r} is not a dotted version "
                        "number like '1.4.2' - the version check would not work")
    if not (manifest.get("slots") or manifest.get("api_routes") or manifest.get("ui_routes")):
        problems.append("manifest declares no slots and no routes - the module does nothing")

    for py_file in folder.rglob("*.py"):
        hits = _protected_imports(py_file)
        for h in sorted(hits):
            problems.append(f"{py_file.relative_to(folder)}: imports protected internal {h!r} "
                            "- the loader will reject this module")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python lint.py path/to/your-module-folder")
        return 2
    folder = Path(sys.argv[1]).resolve()
    problems = lint(folder)
    if problems:
        print(f"✗ {folder.name}: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"✓ {folder.name}: looks good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
