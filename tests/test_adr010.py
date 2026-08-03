"""ADR-010 guard: the ETL must never import a live stats.nba.com endpoint.

stats.nba.com blacklists datacenter IP ranges and fingerprints TLS handshakes,
so any live call breaks the moment the pipeline runs on a GitHub Actions
runner. `nba_api` is permitted for exactly one thing — `stats.static.players`,
which reads a table bundled inside the package and issues no HTTP request.

WHY THIS IS AN AST CHECK AND NOT A grep:
the obvious implementation is `grep -rn "nba_api.stats.endpoints" etl/`, and it
is wrong. Every module that documents this rule necessarily *names* the
forbidden import, so the guard fires on its own documentation — which is
exactly what happened in CI: a docstring in etl/crosswalk.py explaining that
endpoints may never be imported was itself reported as a violation.

Parsing the AST distinguishes an import statement from a mention of one. Text
matching cannot: a comment, a docstring and real code are the same bytes.
"""

from __future__ import annotations

import ast
from pathlib import Path

ETL = Path(__file__).resolve().parent.parent / "etl"

FORBIDDEN_ROOT = "nba_api.stats.endpoints"
ALLOWED = "nba_api.stats.static"


def _violations(path: Path) -> list[str]:
    """Return human-readable violations for one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    for node in ast.walk(tree):
        # import nba_api.stats.endpoints[...]
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == FORBIDDEN_ROOT or alias.name.startswith(
                    FORBIDDEN_ROOT + "."
                ):
                    found.append(f"{path}:{node.lineno}: import {alias.name}")

        # from nba_api.stats.endpoints[...] import X
        # from nba_api.stats import endpoints
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == FORBIDDEN_ROOT or module.startswith(FORBIDDEN_ROOT + "."):
                found.append(f"{path}:{node.lineno}: from {module} import ...")
            elif module == "nba_api.stats":
                for alias in node.names:
                    if alias.name == "endpoints":
                        found.append(
                            f"{path}:{node.lineno}: from nba_api.stats import endpoints"
                        )

    return found


def test_etl_never_imports_live_nba_endpoints():
    modules = sorted(ETL.rglob("*.py"))
    assert modules, f"no Python modules found under {ETL} — is the path right?"

    violations = [v for m in modules for v in _violations(m)]
    assert not violations, (
        "ADR-010 violation — etl/ imports a live stats.nba.com endpoint.\n"
        "stats.nba.com blocks datacenter IPs, so this breaks the nightly "
        "GitHub Actions build.\n"
        f"Only {ALLOWED} (offline, bundled data) is permitted.\n\n"
        + "\n".join(violations)
    )


def test_guard_detects_a_real_violation(tmp_path):
    """The guard is worthless if it cannot fail. Prove it catches real imports
    while ignoring the same text in comments and docstrings."""
    offender = tmp_path / "offender.py"

    for source in (
        "import nba_api.stats.endpoints",
        "import nba_api.stats.endpoints.commonplayerinfo",
        "from nba_api.stats.endpoints import commonplayerinfo",
        "from nba_api.stats import endpoints",
    ):
        offender.write_text(source, encoding="utf-8")
        assert _violations(offender), f"guard missed a real violation: {source!r}"

    # The false-positive case that actually broke CI.
    offender.write_text(
        '"""Never import ``nba_api.stats.endpoints`` here."""\n'
        "# from nba_api.stats import endpoints  <- forbidden\n"
        "from nba_api.stats.static import players\n"
        'NOTE = "nba_api.stats.endpoints is banned"\n',
        encoding="utf-8",
    )
    assert not _violations(offender), (
        "guard fired on a comment/docstring/string that merely names the "
        "forbidden import — this is the bug it was rewritten to fix"
    )
