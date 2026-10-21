"""SQLite 本地存储：K 线缓存、快照积累与元数据。"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    asset_id TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts       TEXT NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    amount   REAL,
    PRIMARY KEY (asset_id, interval, ts)
);
CREATE INDEX IF NOT EXISTS idx_bars_asset ON bars(asset_id, interval, ts);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ------------------------------------------------------------------ 写入
    def upsert_bars(self, asset_id: str, interval: str, bars: Iterable[Sequence[Any]]) -> int:
        rows = []
        for b in bars:
            ts, o, h, l, c = b[0], b[1], b[2], b[3], b[4]
            if ts is None or c is None:
                continue
            vol = b[5] if len(b) > 5 else None
            amt = b[6] if len(b) > 6 else None
            rows.append((asset_id, interval, str(ts)[:19], o, h, l, c, vol, amt))
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO bars(asset_id,interval,ts,open,high,low,close,volume,amount)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, str(value)))
            self._conn.commit()

    def delete_bars(self, asset_id: str, interval: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bars WHERE asset_id=? AND interval=?", (asset_id, interval))
            self._conn.commit()

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    # ------------------------------------------------------------------ 读取
    def get_bars(self, asset_id: str, interval: str = "day", limit: int = 0) -> List[List[Any]]:
        sql = (
            "SELECT ts,open,high,low,close,volume,amount FROM bars "
            "WHERE asset_id=? AND interval=? ORDER BY ts ASC"
        )
        with self._lock:
            rows = self._conn.execute(sql, (asset_id, interval)).fetchall()
        data = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]
        if limit and len(data) > limit:
            data = data[-limit:]
        return data

    def last_ts(self, asset_id: str, interval: str = "day") -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ts) FROM bars WHERE asset_id=? AND interval=?", (asset_id, interval)
            ).fetchone()
        return row[0] if row and row[0] else None

    def count(self, asset_id: str, interval: str = "day") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM bars WHERE asset_id=? AND interval=?", (asset_id, interval)
            ).fetchone()
        return int(row[0]) if row else 0

    def recent_closes(self, asset_id: str, n: int = 40) -> List[float]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT close FROM bars WHERE asset_id=? AND interval='day' ORDER BY ts DESC LIMIT ?",
                (asset_id, n),
            ).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None][::-1]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
            assets = self._conn.execute("SELECT COUNT(DISTINCT asset_id) FROM bars").fetchone()[0]
        return {"bars": int(total), "assets": int(assets), "path": self.path}

    # ------------------------------------------------------------------ 导出
    def export_csv(self, asset_id: str, interval: str, out_dir: str) -> str:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{asset_id.replace('.', '_')}_{interval}.csv")
        rows = self.get_bars(asset_id, interval)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write("date,open,high,low,close,volume\n")
            for r in rows:
                fh.write(",".join("" if v is None else str(v) for v in r) + "\n")
        return path

    def close(self) -> None:
        with self._lock:
            self._conn.close()
