"""SQLite persistence for check results."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    direction TEXT NOT NULL,
    buy_dex TEXT NOT NULL,
    sell_dex TEXT NOT NULL,
    start_usdc REAL NOT NULL,
    mid_sol REAL NOT NULL,
    final_usdc REAL NOT NULL,
    gross_profit_usd REAL NOT NULL,
    sol_price_usd REAL,
    network_fee_usd REAL NOT NULL,
    net_edge_usd REAL NOT NULL,
    reference_edge_usd REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks (ts);
"""


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_check(result: dict):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO checks
               (ts, direction, buy_dex, sell_dex, start_usdc, mid_sol, final_usdc,
                gross_profit_usd, sol_price_usd, network_fee_usd, net_edge_usd,
                reference_edge_usd, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result.get("ts", datetime.now(timezone.utc).isoformat()),
                result.get("direction", ""),
                result.get("buy_dex", ""),
                result.get("sell_dex", ""),
                result.get("start_usdc", 0.0),
                result.get("mid_sol", 0.0),
                result.get("final_usdc", 0.0),
                result.get("gross_profit_usd", 0.0),
                result.get("sol_price_usd"),
                result.get("network_fee_usd", 0.0),
                result.get("net_edge_usd", 0.0),
                result.get("reference_edge_usd"),
                result.get("error"),
            ),
        )


def stats_since(since_iso: str) -> dict:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM checks WHERE ts >= ? AND error IS NULL ORDER BY net_edge_usd DESC",
            (since_iso,),
        ).fetchall()
        error_count = conn.execute(
            "SELECT COUNT(*) c FROM checks WHERE ts >= ? AND error IS NOT NULL", (since_iso,)
        ).fetchone()[0]

    if not rows:
        return {"count": 0, "error_count": error_count}

    edges = [r["net_edge_usd"] for r in rows]
    positive = [e for e in edges if e > 0]
    best = dict(rows[0])

    return {
        "count": len(rows),
        "error_count": error_count,
        "positive_count": len(positive),
        "min_edge": min(edges),
        "max_edge": max(edges),
        "avg_edge": sum(edges) / len(edges),
        "best": best,
    }
