import json
import os
from datetime import datetime, timedelta, timezone

import libsql


_conn = None

_FIELDS = [
    "count",
    "min_all",
    "min_qual",
    "eff_min_all",
    "eff_min_qual",
    "rec_price",
    "rec_eff",
]


def _link():
    global _conn
    if _conn is None:
        url = os.environ.get("STORE_URL", "").strip()
        token = os.environ.get("STORE_TOKEN", "").strip()
        if not url:
            raise RuntimeError("STORE_URL is not set.")
        _conn = libsql.connect(database=url, auth_token=token)
    return _conn


def init():
    conn = _link()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS samples ("
        "ts TEXT PRIMARY KEY, count INTEGER, "
        "min_all REAL, min_qual REAL, "
        "eff_min_all REAL, eff_min_qual REAL, "
        "rec_price REAL, rec_eff REAL)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.commit()
    if os.environ.get("STORE_RESET", "").strip().lower() in ("1", "true", "yes"):
        conn.execute("DELETE FROM samples")
        conn.execute("DELETE FROM meta")
        conn.commit()


def _clean(value):
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cutoff(retention_days):
    if not retention_days:
        return None
    moment = datetime.now(timezone.utc) - timedelta(days=retention_days)
    return moment.replace(microsecond=0).isoformat()


def append_sample(ts, summary, retention_days):
    conn = _link()
    row = [ts, int(summary.get("count") or 0)]
    for key in _FIELDS[1:]:
        row.append(_clean(summary.get(key)))
    conn.execute(
        "INSERT OR REPLACE INTO samples "
        "(ts, count, min_all, min_qual, eff_min_all, eff_min_qual, rec_price, rec_eff) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    cutoff = _cutoff(retention_days)
    if cutoff:
        conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
    conn.commit()


def load_history():
    conn = _link()
    cur = conn.execute(
        "SELECT ts, count, min_all, min_qual, eff_min_all, eff_min_qual, "
        "rec_price, rec_eff FROM samples ORDER BY ts"
    )
    out = []
    for r in cur.fetchall():
        out.append({
            "timestamp": r[0],
            "count": r[1],
            "min_all": r[2],
            "min_qual": r[3],
            "eff_min_all": r[4],
            "eff_min_qual": r[5],
            "rec_price": r[6],
            "rec_eff": r[7],
        })
    return out


def get_meta(key, default):
    conn = _link()
    cur = conn.execute("SELECT v FROM meta WHERE k = ?", (key,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def set_meta(key, value):
    conn = _link()
    conn.execute(
        "INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()
