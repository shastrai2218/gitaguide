"""Every package a CI job imports must be on that job's install line.

Third time this failed the same way:

* 17 Sep 2026 - ``app/auth.py`` imported ``jwt``; the unit-test job did not
  install PyJWT (fixed in ``9a47e82``).
* 17 Sep 2026 - three ``test_embeddings.py`` tests imported ``onnxruntime``
  (made to skip in ``1c68bb2``).
* 19 Sep 2026 - ``test_keepalive_neo4j.py`` monkeypatched ``"neo4j.GraphDatabase.driver"``
  by name, which imports ``neo4j``; the job did not install it, and master was
  red for eleven days.

Each time the tests passed on every developer machine, because a developer
machine has ``requirements.txt`` installed and the CI job deliberately has a
small offline subset. Nothing local ever compared the two. This does, from a
developer machine: it reads each CI job's ``pip install`` line, walks the
module-level import graph from that job's entry files (through first-party
modules, so ``tests -> app.main -> app.auth -> jwt`` counts), also counts
string targets handed to ``monkeypatch.setattr`` / ``mock.patch`` (the neo4j
case), maps modules to distributions with the local package metadata, and
asserts every one is installed by the job - directly or as a dependency of
something that is. Lazy imports inside functions and imports inside ``try``
blocks are exempt, which is exactly the escape hatch the eval scripts and
``scripts/keepalive_neo4j.py`` already use on purpose.
"""

from __future__ import annotations

import ast
import importlib.metadata as metadata
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

FIRST_PARTY = {"app", "corpus", "kg", "eval", "scripts", "tests"}
STDLIB = set(sys.stdlib_module_names) | {"__future__"}

#: The Python jobs in ci.yml and what each one runs, i.e. where the import
#: walk starts. A new Python job must be added here to be covered.
JOBS: dict[str, list[str]] = {
    "tests": ["tests"],
    "safety-redteam": ["eval/test_safety_redteam.py"],
    "retrieval-eval": ["eval/retrieval_eval.py"],
}


# ci.yml: the install line of each job
def _job_block(job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  \S|\Z)", CI, re.MULTILINE | re.DOTALL)
    assert match, f"job {job!r} is not in ci.yml; update JOBS in this test"
    return match.group(1)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> tuple[str, set[str]]:
    """``"PyJWT[crypto]>=2"`` -> (``pyjwt``, {``crypto``})."""
    spec = spec.strip().strip("\"'")
    match = re.match(r"([A-Za-z0-9_.\-]+)(?:\[([^\]]*)\])?", spec)
    assert match, f"cannot read requirement {spec!r}"
    extras = {e.strip().lower() for e in (match.group(2) or "").split(",") if e.strip()}
    return _normalise(match.group(1)), extras


def _installed_by(job: str) -> dict[str, set[str]]:
    """What the job's ``pip install`` lines name, with requested extras."""
    block = _job_block(job)
    joined = re.sub(r"\\\n\s*", " ", block)  # continuation lines
    installs: dict[str, set[str]] = {}
    for line in joined.splitlines():
        line = line.strip()
        if not line.startswith("pip install"):
            continue
        for token in line[len("pip install") :].split():
            if token.startswith("-"):
                continue
            name, extras = _requirement_name(token)
            installs.setdefault(name, set()).update(extras)
    assert installs, f"job {job!r} has no `pip install` line"
    return installs


_REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[([^\]]*)\])?[^;]*(?:;\s*(.*))?$")


def _closure(installs: dict[str, set[str]]) -> set[str]:
    """Distributions the job ends up with: the named ones plus what they pull in.

    Read from the *local* metadata, which is why this runs on a developer
    machine where everything is installed. Extras are honoured (``PyJWT[crypto]``
    brings ``cryptography``; plain ``PyJWT`` would not); other markers are
    assumed true, which can only make the closure larger, never smaller.
    """
    have: set[str] = set()
    todo = list(installs.items())
    while todo:
        name, extras = todo.pop()
        if name in have:
            continue
        have.add(name)
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue  # not installed locally; cannot see past it
        for requirement in requires:
            match = _REQ_RE.match(requirement)
            if not match:
                continue
            dep, dep_extras, marker = match.groups()
            if marker:
                wanted = re.findall(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker)
                if wanted and not any(w.lower() in extras for w in wanted):
                    continue
            dep_extra_set = {e.strip().lower() for e in (dep_extras or "").split(",") if e.strip()}
            todo.append((_normalise(dep), dep_extra_set))
    return have


# The import graph
def _module_file(module: str) -> Path | None:
    """``app.api.beta`` -> its file, for first-party modules only."""
    if module.split(".")[0] not in FIRST_PARTY:
        return None
    parts = module.split(".")
    for candidate in (ROOT.joinpath(*parts).with_suffix(".py"), ROOT.joinpath(*parts, "__init__.py")):
        if candidate.exists():
            return candidate
    return None


class _ModuleLevelImports(ast.NodeVisitor):
    """Imports that run when the module is imported: top level, ``if`` blocks,
    class bodies - but not function bodies, ``try`` bodies (optional imports)
    or ``if TYPE_CHECKING`` blocks."""

    def __init__(self) -> None:
        self.modules: set[str] = set()
        self.patched: set[str] = set()

    def visit_FunctionDef(self, node: ast.AST) -> None:
        self._scan_patch_targets(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            for child in handler.body:
                self.visit(child)
        for child in node.orelse + node.finalbody:
            self.visit(child)

    def visit_If(self, node: ast.If) -> None:
        test = ast.unparse(node.test)
        if "TYPE_CHECKING" in test:
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.modules.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self.modules.add(node.module)
            # ``from scripts import keepalive_neo4j`` names a module, not an attribute.
            for alias in node.names:
                if _module_file(f"{node.module}.{alias.name}"):
                    self.modules.add(f"{node.module}.{alias.name}")

    def _scan_patch_targets(self, node: ast.AST) -> None:
        """``monkeypatch.setattr("neo4j.GraphDatabase.driver", ...)`` imports neo4j."""
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            func = ast.unparse(call.func)
            if not (func.endswith(".setattr") or func.endswith("patch") or func.endswith("patch.object")):
                continue
            target = call.args[0]
            if isinstance(target, ast.Constant) and isinstance(target.value, str) and "." in target.value:
                self.patched.add(target.value.split(".")[0])


def _third_party_imports(entry_files: list[Path]) -> dict[str, set[str]]:
    """module -> the files that need it, walking first-party imports transitively."""
    needed: dict[str, set[str]] = {}
    seen: set[Path] = set()
    todo = list(entry_files)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        visitor = _ModuleLevelImports()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for module in visitor.modules | visitor.patched:
            top = module.split(".")[0]
            if top in STDLIB:
                continue
            if top in FIRST_PARTY:
                target = _module_file(module) or _module_file(top)
                if target is not None:
                    todo.append(target)
                    # ``from app import x`` also runs app/__init__.py
                    package_init = ROOT / top / "__init__.py"
                    if package_init.exists():
                        todo.append(package_init)
                continue
            needed.setdefault(top, set()).add(rel)
    return needed


def _entry_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        path = ROOT / root
        if path.is_dir():
            files.extend(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
        else:
            files.append(path)
    return files


def _distributions_for(module: str) -> set[str]:
    """Which distribution(s) provide a top-level module, from local metadata.

    ``jwt`` -> ``pyjwt``, ``sqlalchemy`` -> ``sqlalchemy``. Unknown locally means
    the module itself is not installed here either; the name is used as-is.
    """
    dists = metadata.packages_distributions().get(module)
    return {_normalise(d) for d in dists} if dists else {_normalise(module)}


# The assertions
@pytest.mark.parametrize("job", sorted(JOBS))
def test_every_import_a_ci_job_makes_is_on_its_install_line(job: str) -> None:
    installs = _installed_by(job)
    available = _closure(installs)
    needed = _third_party_imports(_entry_files(JOBS[job]))

    missing: list[str] = []
    for module, files in sorted(needed.items()):
        if _distributions_for(module) & available:
            continue
        where = ", ".join(sorted(files)[:3]) + (" ..." if len(files) > 3 else "")
        missing.append(f"  {module!r} (dist {'/'.join(sorted(_distributions_for(module)))}) <- {where}")

    assert not missing, (
        f"CI job {job!r} imports packages its `pip install` line does not provide:\n"
        + "\n".join(missing)
        + f"\n\nThe job installs: {', '.join(sorted(installs))}.\n"
        "Either add the package to that line in .github/workflows/ci.yml, or make the "
        "import lazy (inside the function that needs it, or `pytest.importorskip`) if "
        "the job really should run without it. Tests pass locally because "
        "requirements.txt is installed here; CI has only the line above."
    )


def test_the_walk_sees_through_first_party_modules() -> None:
    """``tests/`` reaches ``jwt`` only via ``app.main -> app.api.beta -> app.auth``.
    If the transitive walk ever breaks, this is the case that proves it."""
    needed = _third_party_imports(_entry_files(["tests"]))
    assert "jwt" in needed, "the transitive walk no longer reaches app.auth's PyJWT import"
    assert any(f.startswith("app/") for f in needed["jwt"])


def test_the_walk_counts_monkeypatch_string_targets() -> None:
    """The neo4j case: no ``import neo4j`` anywhere, but a string target."""
    needed = _third_party_imports([ROOT / "tests" / "test_keepalive_neo4j.py"])
    assert "neo4j" in needed, "monkeypatch string targets are no longer counted"


def test_lazy_imports_are_exempt() -> None:
    """``scripts/keepalive_neo4j.py`` imports neo4j inside main(); that must not
    count on its own - it is precisely the pattern that keeps a job light."""
    needed = _third_party_imports([ROOT / "scripts" / "keepalive_neo4j.py"])
    assert "neo4j" not in needed


def test_extras_bring_their_dependencies() -> None:
    """``PyJWT[crypto]`` provides ``cryptography``; plain ``PyJWT`` would not."""
    assert "cryptography" in _closure({"pyjwt": {"crypto"}})
    assert "cryptography" not in _closure({"pyjwt": set()})
