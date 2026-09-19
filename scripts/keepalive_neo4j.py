"""Touch the Aura instance so it is not paused for inactivity.

AuraDB Free pauses an instance after about three days without a query. The first
request after that fails until someone resumes the instance in the Aura console,
and nothing in a quiet beta would touch it in between. This makes one small
read-only query, so run it daily from cron on the API host::

    17 3 * * * docker exec gitaguide-api python -m scripts.keepalive_neo4j >> ~/keepalive.log 2>&1

It counts the ``Verse`` nodes and exits 0 only if the database answered with at
least one, so a paused, unreachable or accidentally emptied graph is reported
rather than silently "touched". Connection details come from
``GITAGUIDE_NEO4J_*``; it prints the host but never a credential.

Exit codes: 0 answered, 1 unreachable or empty, 2 not configured.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings

#: A graph with fewer verses than this is not the graph we deployed.
MIN_VERSES = 1

COUNT_VERSES = "MATCH (v:Verse) RETURN count(v) AS verses"


def count_verses(driver: Any, database: str) -> int:
    with driver.session(database=database) as session:
        record = session.run(COUNT_VERSES).single()
    return int(record["verses"])


def main() -> int:
    settings = get_settings()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")

    if not settings.neo4j_password:
        print(f"{stamp} [FAIL] GITAGUIDE_NEO4J_PASSWORD is not set; nothing to keep alive", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase

    try:
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
        )
        try:
            driver.verify_connectivity()
            verses = count_verses(driver, settings.neo4j_database)
        finally:
            driver.close()
    except Exception as exc:  # any failure to reach or query is the finding
        print(
            f"{stamp} [FAIL] could not query {settings.neo4j_uri}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "  If this is AuraDB Free the instance may be paused for inactivity: "
            "resume it at https://console.neo4j.io",
            file=sys.stderr,
        )
        return 1

    if verses < MIN_VERSES:
        print(
            f"{stamp} [FAIL] {settings.neo4j_uri} answered but holds {verses} verses; "
            "the graph is empty or is not the deployed one",
            file=sys.stderr,
        )
        return 1

    print(f"{stamp} [ok] {settings.neo4j_uri} answered: {verses} verses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
