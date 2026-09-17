"""Small SQLite store for player definitions, runs, and activity."""
import contextlib
import datetime
import json
from pathlib import Path
import sqlite3


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    instructions TEXT NOT NULL DEFAULT '',
                    auto_start INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS player_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    outcome TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS player_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES player_runs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS player_events_run ON player_events(run_id, id DESC);
            """)
            count = db.execute("SELECT COUNT(*) FROM players").fetchone()[0]
            if count == 0:
                now = utc_now()
                db.execute(
                    "INSERT INTO players(id,name,model,instructions,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    ("ada", "Ada", "scripted", "Stay connected and ready for commands.", now, now),
                )

    def players(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM players ORDER BY created_at")]

    def player(self, player_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
            return dict(row) if row else None

    def update_player(self, player_id, values):
        allowed = {"name", "model", "instructions", "auto_start"}
        updates = {key: values[key] for key in allowed if key in values}
        if not updates:
            return self.player(player_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join(key + " = ?" for key in updates)
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE players SET " + assignments + " WHERE id = ?",
                [int(value) if key == "auto_start" else value for key, value in updates.items()] + [player_id],
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown AI player.")
        return self.player(player_id)

    def start_run(self, player_id):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO player_runs(player_id,started_at) VALUES(?,?)", (player_id, utc_now())
            )
            return cursor.lastrowid

    def finish_run(self, run_id, outcome, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE player_runs SET ended_at=?, outcome=?, error=? WHERE id=? AND ended_at IS NULL",
                (utc_now(), outcome, error, run_id),
            )

    def event(self, run_id, kind, payload):
        with self.connect() as db:
            db.execute(
                "INSERT INTO player_events(run_id,created_at,kind,payload) VALUES(?,?,?,?)",
                (run_id, utc_now(), kind, json.dumps(payload, separators=(",", ":"))),
            )

    def player_summary(self, player_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT started_at,ended_at,outcome FROM player_runs WHERE player_id=?", (player_id,)
            ).fetchall()
            now = datetime.datetime.now(datetime.timezone.utc)
            seconds = 0
            for row in rows:
                start = datetime.datetime.fromisoformat(row["started_at"])
                end = datetime.datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else now
                seconds += max(0, int((end - start).total_seconds()))
            recent = db.execute(
                """SELECT e.created_at,e.kind,e.payload FROM player_events e
                   JOIN player_runs r ON r.id=e.run_id WHERE r.player_id=?
                   ORDER BY e.id DESC LIMIT 30""",
                (player_id,),
            ).fetchall()
            return {
                "runs": len(rows),
                "completed_runs": sum(row["outcome"] == "stopped" for row in rows),
                "failed_runs": sum(row["outcome"] == "failed" for row in rows),
                "online_seconds": seconds,
                "events": [
                    {"created_at": row["created_at"], "kind": row["kind"], "payload": json.loads(row["payload"])}
                    for row in recent
                ],
            }
