import json
import sqlite3
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATABASE_PATH = DATA_DIR / "research.db"


class ResearchStore:
    """Relational ledger for reproducible research runs and evidence."""

    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    country TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES research_runs(id),
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    crawl_allowed INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES research_runs(id),
                    claim TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def save_run(self, run: dict[str, Any]) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO research_runs (city, country, status, started_at, completed_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (run["city"], run.get("country"), run["status"], run["started_at"], run["completed_at"], json.dumps(run)),
            )
            run_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO sources (run_id, url, title, crawl_allowed, payload) VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, source["url"], source["title"], int(source.get("crawl_allowed", False)), json.dumps(source))
                    for source in run.get("sources", [])
                ],
            )
            connection.executemany(
                "INSERT INTO facts (run_id, claim, verification_status, source_url, payload) VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, fact["claim"], fact["verification_status"], fact["source_url"], json.dumps(fact))
                    for fact in run.get("facts", [])
                ],
            )
        return int(run_id)

    def latest_run(self, city: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM research_runs WHERE lower(city) = lower(?) ORDER BY id DESC LIMIT 1",
                (city,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_runs(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, city, country, status, started_at, completed_at, payload FROM research_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "city": row["city"],
                "country": row["country"],
                "status": row["status"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "facts": len(json.loads(row["payload"]).get("facts", [])),
                "gaps": len(json.loads(row["payload"]).get("gaps", [])),
                "duration_ms": json.loads(row["payload"]).get("metrics", {}).get("total_duration_ms", 0),
            }
            for row in rows
        ]


research_store = ResearchStore()