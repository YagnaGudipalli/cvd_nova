"""Relational ledger — the audit record of what the system did.

This store answers questions the other two cannot: what was researched, when,
which sources were refused and why, which claims were withheld, and how the run
scored. It is deliberately the only store that is always available, so the
system remains honest and inspectable with no external provider configured.

SQLite for the case study; the schema is plain enough to move to PostgreSQL
without rewriting queries, which is required before this is multi-user.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATABASE_PATH = DATA_DIR / "research.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    city          TEXT NOT NULL,
    city_key      TEXT NOT NULL,
    country       TEXT,
    status        TEXT NOT NULL,
    rounds        INTEGER NOT NULL DEFAULT 1,
    started_at    TEXT NOT NULL,
    completed_at  TEXT NOT NULL,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_city ON research_runs(city_key, id DESC);

CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES research_runs(id),
    url            TEXT NOT NULL,
    title          TEXT NOT NULL,
    dimension      TEXT,
    crawl_allowed  INTEGER,
    crawl_reason   TEXT,
    fetched        INTEGER NOT NULL DEFAULT 0,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_run ON sources(run_id);

CREATE TABLE IF NOT EXISTS facts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES research_runs(id),
    city_key            TEXT NOT NULL,
    claim               TEXT NOT NULL,
    dimension           TEXT NOT NULL,
    geographic_scope    TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0,
    source_url          TEXT NOT NULL,
    payload             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_city ON facts(city_key);

CREATE TABLE IF NOT EXISTS gaps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES research_runs(id),
    city_key   TEXT NOT NULL,
    topic      TEXT NOT NULL,
    dimension  TEXT,
    kind       TEXT NOT NULL,
    reason     TEXT NOT NULL,
    source_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_gaps_city ON gaps(city_key);

CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES research_runs(id),
    city_key   TEXT NOT NULL,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    source_url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_city ON entities(city_key);
"""


def _key(city: str) -> str:
    return city.strip().lower()


def terms(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", value.lower())}


class ResearchStore:
    """Reproducible record of every research run."""

    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        with self._connect() as connection:
            self._migrate(connection)
            connection.executescript(SCHEMA)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Set aside a pre-v2 ledger rather than failing or destroying it.

        The v1 schema had no city_key and no gaps or entities tables. Old tables
        are renamed so the historical rows remain readable by hand, and the
        current schema is then created cleanly.
        """
        existing = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "research_runs" not in existing:
            return
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(research_runs)").fetchall()}
        if "city_key" in columns:
            return
        for table in ("research_runs", "sources", "facts"):
            if table in existing and f"{table}_v1" not in existing:
                connection.execute(f"ALTER TABLE {table} RENAME TO {table}_v1")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # ------------------------------------------------------------------ #

    def save_run(self, run: dict[str, Any]) -> int:
        city_key = _key(run["city"])
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO research_runs (city, city_key, country, status, rounds, started_at, completed_at, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run["city"],
                    city_key,
                    run.get("country"),
                    run["status"],
                    run.get("rounds", 1),
                    run["started_at"],
                    run["completed_at"],
                    json.dumps(run),
                ),
            )
            run_id = int(cursor.lastrowid)

            connection.executemany(
                """INSERT INTO sources (run_id, url, title, dimension, crawl_allowed, crawl_reason, fetched, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id,
                        source["url"],
                        source["title"],
                        source.get("dimension"),
                        None if source.get("crawl_allowed") is None else int(source["crawl_allowed"]),
                        source.get("crawlability_reason", ""),
                        int(source.get("fetched", False)),
                        json.dumps(source),
                    )
                    for source in run.get("sources", [])
                ],
            )
            connection.executemany(
                """INSERT INTO facts (run_id, city_key, claim, dimension, geographic_scope, verification_status, confidence, source_url, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id,
                        city_key,
                        fact["claim"],
                        fact.get("dimension", ""),
                        fact.get("geographic_scope", "unknown"),
                        fact["verification_status"],
                        float(fact.get("confidence", 0)),
                        fact["source_url"],
                        json.dumps(fact),
                    )
                    for fact in run.get("facts", [])
                ],
            )
            connection.executemany(
                "INSERT INTO gaps (run_id, city_key, topic, dimension, kind, reason, source_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (run_id, city_key, gap["topic"], gap.get("dimension", ""), gap.get("kind", "missing"), gap["reason"], gap.get("source_url"))
                    for gap in run.get("gaps", [])
                ],
            )
            connection.executemany(
                "INSERT INTO entities (run_id, city_key, name, type, source_url) VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, city_key, entity["name"], entity["type"], fact["source_url"])
                    for fact in run.get("facts", [])
                    for entity in fact.get("entities", [])
                    if entity.get("name")
                ],
            )
        return run_id

    def update_graph_status(self, city: str, job: dict[str, Any]) -> None:
        """Write a finished background graph job back into the latest run record.

        The run is saved before the graph is built, so without this the ledger
        would say "building" forever and the downloaded brief would never know
        whether the graph succeeded.
        """
        written, total = job.get("episodes_written", 0), job.get("episodes_total", 0)
        detail = {
            "completed": f"Graph built: {written} of {total} episodes written to Graphiti/Neo4j.",
            "partial": f"Graph partially built: {written} of {total} episodes written; {job.get('episodes_failed', 0)} failed.",
            "failed": f"Graph build failed: 0 of {total} episodes written.",
            "skipped": "Graph build skipped: provider not configured.",
            "superseded": "Superseded by a newer research run for this city.",
        }.get(job.get("status"), f"Graph status: {job.get('status')}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, payload FROM research_runs WHERE city_key = ? ORDER BY id DESC LIMIT 1", (_key(city),)
            ).fetchone()
            if not row:
                return
            payload = json.loads(row["payload"])
            graph = payload.setdefault("stores", {}).setdefault("graph", {})
            graph.update(
                status={"completed": "ready", "partial": "partial"}.get(job.get("status"), job.get("status")),
                indexed_records=written,
                episodes_total=total,
                detail=detail,
                errors=job.get("errors", [])[:5],
                finished_at=job.get("finished_at"),
            )
            connection.execute("UPDATE research_runs SET payload = ? WHERE id = ?", (json.dumps(payload), row["id"]))

    def latest_run(self, city: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM research_runs WHERE city_key = ? ORDER BY id DESC LIMIT 1", (_key(city),)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_runs(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.id, r.city, r.country, r.status, r.rounds, r.started_at, r.completed_at,
                          (SELECT COUNT(*) FROM facts f WHERE f.run_id = r.id) AS fact_count,
                          (SELECT COUNT(*) FROM gaps g WHERE g.run_id = r.id) AS gap_count,
                          (SELECT COUNT(*) FROM sources s WHERE s.run_id = r.id) AS source_count,
                          r.payload
                   FROM research_runs r ORDER BY r.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        runs = []
        for row in rows:
            metrics = json.loads(row["payload"]).get("metrics", {})
            runs.append(
                {
                    "id": row["id"],
                    "city": row["city"],
                    "country": row["country"],
                    "status": row["status"],
                    "rounds": row["rounds"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "facts": row["fact_count"],
                    "gaps": row["gap_count"],
                    "sources": row["source_count"],
                    "duration_ms": metrics.get("total_duration_ms", 0),
                    "dimensions_covered": metrics.get("dimensions_covered", 0),
                    "depth": json.loads(row["payload"]).get("depth", "balanced"),
                }
            )
        return runs

    def search_facts(self, city: str, question: str, limit: int = 6) -> list[dict[str, Any]]:
        """Lexical fallback over the verified ledger.

        Used when the vector store is unavailable, so retrieval degrades rather
        than disappears.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM facts WHERE city_key = ?
                   ORDER BY confidence DESC, id DESC LIMIT 400""",
                (_key(city),),
            ).fetchall()
        query_terms = terms(question)
        if not query_terms:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        seen: set[str] = set()
        for row in rows:
            fact = json.loads(row["payload"])
            if fact["claim"] in seen:
                continue
            seen.add(fact["claim"])
            haystack = terms(f"{fact.get('claim', '')} {fact.get('evidence_quote', '')} {fact.get('dimension_label', '')}")
            overlap = len(query_terms & haystack)
            if overlap:
                scored.append((overlap + float(fact.get("confidence", 0)), {**fact, "origin": "ledger"}))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [fact for _, fact in scored[:limit]]

    def entities(self, city: str, limit: int = 80) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT name, type, COUNT(*) AS mentions, MIN(source_url) AS source_url
                   FROM entities WHERE city_key = ?
                   GROUP BY lower(name), type ORDER BY mentions DESC, name LIMIT ?""",
                (_key(city), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def city_history(self, city: str) -> list[dict[str, Any]]:
        """Every run for one city, oldest first — the institutional memory view."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, status, rounds, completed_at,
                          (SELECT COUNT(*) FROM facts f WHERE f.run_id = r.id) AS fact_count,
                          (SELECT COUNT(*) FROM gaps g WHERE g.run_id = r.id) AS gap_count
                   FROM research_runs r WHERE city_key = ? ORDER BY id""",
                (_key(city),),
            ).fetchall()
        return [dict(row) for row in rows]


research_store = ResearchStore()
