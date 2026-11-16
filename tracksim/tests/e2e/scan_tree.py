"""Tree hygiene checks S1 (imports), S5 (import side effects) and S7 (packaging)."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

import _support as support

STDLIB = sys.stdlib_module_names

SIDE_EFFECT_CHILD = r"""
import importlib, os, socket, subprocess, sys
src, names = sys.argv[1], sys.argv[2:]
sys.path.insert(0, src)
sys.dont_write_bytecode = True
calls = []
def forbidden(kind):
    def raiser(*args, **kwargs):
        calls.append(kind)
        raise RuntimeError(kind + " called during import")
    return raiser
subprocess.Popen = forbidden("subprocess.Popen")
socket.socket = forbidden("socket.socket")
failures = {}
for name in names:
    try:
        importlib.import_module(name)
    except BaseException as exc:
        failures[name] = repr(exc)
import json
print(json.dumps({"calls": calls, "failures": failures, "cwd": sorted(os.listdir("."))}))
"""

IMPORT_CHILD = r"""
import importlib, sys
src, name = sys.argv[1], sys.argv[2]
sys.path.insert(0, src)
sys.dont_write_bytecode = True
module = importlib.import_module(name)
location = getattr(module, "__file__", "") or ""
print(location.startswith(src))
"""


def python_files() -> list[Path]:
    return sorted(
        p for p in support.ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )


def package_modules() -> list[str]:
    names = []
    for path in sorted((support.SRC / "tracksim").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(support.SRC).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return names


def resolves_in_package(module: str) -> bool:
    parts = module.split(".")
    base = support.SRC.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def import_problems() -> tuple[list[dict], int]:
    problems, inspected = [], 0
    for path in python_files():
        inspected += 1
        rel = path.relative_to(support.ROOT).as_posix()
        in_package = support.SRC in path.parents
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [(alias.name, None) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if not in_package:
                        problems.append({"file": rel, "line": node.lineno,
                                         "import": "relative import outside the package"})
                        continue
                    anchor = path.parent
                    for _ in range(node.level - 1):
                        anchor = anchor.parent
                    base = anchor / (node.module.replace(".", "/") if node.module else "")
                    if node.module:
                        ok = base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()
                        if not ok:
                            problems.append({"file": rel, "line": node.lineno,
                                             "import": "." * node.level + node.module})
                    else:
                        for alias in node.names:
                            if not (anchor / f"{alias.name}.py").is_file():
                                problems.append({"file": rel, "line": node.lineno,
                                                 "import": "." * node.level + alias.name})
                    continue
                targets = [(node.module, [a.name for a in node.names])]
            for name, members in targets:
                top = name.split(".")[0]
                if top == "tracksim":
                    if not resolves_in_package(name):
                        problems.append({"file": rel, "line": node.lineno, "import": name})
                elif top == "_support" and not in_package:
                    continue
                elif top not in STDLIB:
                    problems.append({"file": rel, "line": node.lineno, "import": name})
    return problems, inspected


def check_s1():
    problems, inspected = import_problems()
    modules = [m for m in package_modules() if not m.endswith("__main__")]
    failed = {}
    for name in modules:
        proc = support.run_code(IMPORT_CHILD, support.SRC, name, isolated=True)
        if proc.returncode != 0 or proc.stdout.strip() != "True":
            failed[name] = (proc.stderr.strip().splitlines() or ["not loaded from src"])[-1]
    observed = {"files_inspected": inspected, "import_problems": problems,
                "modules_imported": len(modules), "import_failures": failed}
    expected = {"files_inspected": inspected, "import_problems": [],
                "modules_imported": len(modules), "import_failures": {}}
    return observed, expected


def check_s5():
    modules = [m for m in package_modules() if not m.endswith("__main__")]
    with tempfile.TemporaryDirectory(prefix="e2e-empty-") as cwd:
        proc = support.run_code(SIDE_EFFECT_CHILD, support.SRC, *modules,
                                cwd=cwd, isolated=True)
    if proc.returncode != 0:
        return {"stderr": proc.stderr[-1500:]}, {"returncode": 0}
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    observed = {"modules": len(modules), **data}
    expected = {"modules": len(modules), "calls": [], "failures": {}, "cwd": []}
    return observed, expected


def check_s7():
    import tomllib

    path = support.ROOT / "pyproject.toml"
    data = tomllib.loads(path.read_text())
    project = data.get("project", {})
    observed = {"dependencies": project.get("dependencies"),
                "requires-python": project.get("requires-python")}
    return observed, {"dependencies": [], "requires-python": ">=3.11"}


def main() -> int:
    args = support.parse_args(__doc__)
    support.use_source()
    report = support.Report("scan_tree", args.evidence)
    report.check("S1", check_s1)
    report.check("S5", check_s5)
    report.check("S7", check_s7)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
