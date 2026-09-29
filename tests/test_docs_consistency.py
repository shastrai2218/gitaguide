"""Operator documentation must not point at things that no longer exist.

Each check here is a sentence that was found wrong on 30 Sep 2026, after the
API moved from Render to a VM. Prose drifts more quietly than code: nothing
fails when a runbook names a dashboard that has been deleted, or recommends a
pooler port the tests forbid, or cites a directory that was never created.
So the specific sentences are held here, where drifting fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
DEPLOY_AWS = (ROOT / "docs" / "DEPLOY_AWS.md").read_text(encoding="utf-8")
DATA_MIGRATION = (ROOT / "docs" / "DATA_MIGRATION.md").read_text(encoding="utf-8")
REFERENCES_PY = (ROOT / "app" / "pipeline" / "references.py").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of one ``## heading`` up to the next ``## ``."""
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


# DEPLOY.md step 4b: accounts are switched on in .env.vm, not in Render
def test_the_accounts_step_names_the_vm_env_file_not_render() -> None:
    step = _section(DEPLOY, "## Step 4b")
    assert "GITAGUIDE_CLERK_ISSUER" in step
    assert "Render env var" not in step, "Render no longer exists; the issuer goes in .env.vm"
    assert ".env.vm" in step


# DATA_MIGRATION.md: the pooler advice must agree with the tests
def test_the_migration_doc_does_not_recommend_the_transaction_pooler() -> None:
    """``tests/test_deploy_config.py`` forbids 6543; the prose must not sell it."""
    assert not re.search(r"\*\*Use the pooler on port 6543", DATA_MIGRATION)
    assert "session" in DATA_MIGRATION.lower() and ":5432/" in DATA_MIGRATION
    example_dsns = re.findall(r"GITAGUIDE_POSTGRES_DSN=\S+", DATA_MIGRATION)
    assert example_dsns, "the migration doc lost its DSN example"
    for dsn in example_dsns:
        assert ":6543/" not in dsn, f"example DSN still uses the transaction pooler: {dsn}"


def test_the_migration_doc_does_not_send_the_operator_to_render() -> None:
    assert "and in Render for the service" not in DATA_MIGRATION


# references.py: every docs path a docstring cites must exist
def test_every_docs_path_cited_in_references_py_exists() -> None:
    """The (b') decision was said to be 'recorded in docs/legal/', which was
    never created. A citation to a path is a promise a reader will follow."""
    cited = set(re.findall(r"``(docs/[^`]+)``", REFERENCES_PY))
    for path in cited:
        assert (ROOT / path).exists(), f"references.py cites {path}, which does not exist"


# DEPLOY_AWS.md: the keep-alive is scheduled, not merely mentioned
def test_the_runbook_installs_the_aura_keepalive_cron() -> None:
    day_to_day = _section(DEPLOY_AWS, "## Day to day")
    assert "keepalive_neo4j" in day_to_day, "the keep-alive script is not in the runbook"
    assert re.search(r"^\s*\(crontab -l.*keepalive_neo4j.*\| crontab -\s*$", day_to_day, re.M), (
        "the runbook must give the crontab line that installs the daily run, "
        "not only describe it"
    )
    # The scheduled command must be the one the script's own docstring gives.
    script = (ROOT / "scripts" / "keepalive_neo4j.py").read_text(encoding="utf-8")
    assert "docker exec gitaguide-api python -m scripts.keepalive_neo4j" in script
    assert "docker exec gitaguide-api python -m scripts.keepalive_neo4j" in day_to_day
