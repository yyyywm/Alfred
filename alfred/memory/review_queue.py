"""Durable turn inbox and resumable sleep-time review."""

import json
import logging
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from uuid import uuid4

from .consolidate_state import AUTO_CONSOLIDATE_MIN_HOURS, AUTO_CONSOLIDATE_MIN_TURNS

logger = logging.getLogger(__name__)
_worker_guard = threading.Lock()
_batch_size = 12


def _path(config):
    directory = config.path(config.paths.history_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "review_queue.sqlite3"


@contextmanager
def _db(config):
    with closing(sqlite3.connect(_path(config))) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS turns (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT UNIQUE NOT NULL, session TEXT NOT NULL,
            user_text TEXT NOT NULL, reply TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY, first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL,
            drafts TEXT, completed REAL, error TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
        )""")
        yield conn


def enqueue(config, session_id, user_text, reply):
    ref = f"{session_id}/{uuid4().hex}"
    with _db(config) as conn:
        conn.execute(
            "INSERT INTO turns(ref, session, user_text, reply) VALUES (?, ?, ?, ?)",
            (ref, session_id, user_text, reply),
        )
    return ref


def originals(config, query, *, session_id=None, limit=5, source_ref=None):
    if not _path(config).exists():
        return []
    terms = query.lower().split()
    if not terms and not source_ref:
        return []
    clauses, values = [], []
    if session_id:
        clauses.append("session = ?")
        values.append(session_id)
    if source_ref:
        clauses.append("ref = ?")
        values.append(source_ref)
    else:
        for term in terms:
            clauses.append("(instr(lower(user_text), ?) OR instr(lower(reply), ?))")
            values.extend((term, term))
    with _db(config) as conn:
        return conn.execute(
            "SELECT ref, user_text, reply FROM turns WHERE " + " AND ".join(clauses)
            + " ORDER BY seq DESC LIMIT ?",
            (*values, max(1, min(limit, 20))),
        ).fetchall()


def status(config):
    if not _path(config).exists():
        return {"pending": 0, "batch": None, "error": None}
    with _db(config) as conn:
        pending = conn.execute("SELECT count(*) FROM turns WHERE done = 0").fetchone()[0]
        batch = conn.execute(
            "SELECT id, error FROM batches WHERE completed IS NULL LIMIT 1"
        ).fetchone()
    return {"pending": pending, "batch": batch[0] if batch else None,
            "error": batch[1] if batch else None}


def save_review(config, batch_id, record):
    with _db(config) as conn:
        conn.execute("INSERT OR IGNORE INTO reviews VALUES (?, ?)",
                     (batch_id, json.dumps(record, ensure_ascii=False)))


def reviews(config):
    if not _path(config).exists():
        return []
    with _db(config) as conn:
        return [(key, json.loads(record)) for key, record in conn.execute(
            "SELECT id, record FROM reviews WHERE record != '' ORDER BY rowid"
        )]


def dismiss_review(config, batch_id):
    with _db(config) as conn:
        conn.execute("UPDATE reviews SET record = '' WHERE id = ?", (batch_id,))


def forget_session(config, session_id):
    if not _path(config).exists():
        return
    with _db(config) as conn:
        conn.execute("DELETE FROM turns WHERE session = ?", (session_id,))


def run(config, *, force=False):
    from .consolidate import apply_unattended, generate_drafts

    with closing(sqlite3.connect(_path(config).with_suffix(".lock"), timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                return False
            raise
        with _db(config) as conn:
            batch = conn.execute(
                "SELECT id, first_seq, last_seq, drafts FROM batches WHERE completed IS NULL"
            ).fetchone()
            if not batch:
                seqs = conn.execute(
                    "SELECT seq FROM turns WHERE done = 0 ORDER BY seq LIMIT ?", (_batch_size,)
                ).fetchall()
                last = conn.execute("SELECT max(completed) FROM batches").fetchone()[0]
                cooling_down = last is not None and time.time() - last < AUTO_CONSOLIDATE_MIN_HOURS * 3600
                if not seqs or (not force and (len(seqs) < AUTO_CONSOLIDATE_MIN_TURNS or cooling_down)):
                    return False
                batch = (uuid4().hex, seqs[0][0], seqs[-1][0], None)
                conn.execute("INSERT INTO batches(id, first_seq, last_seq, drafts) VALUES (?,?,?,?)", batch)
        batch_id, first_seq, last_seq, saved = batch
        try:
            if saved is None:
                with _db(config) as conn:
                    rows = conn.execute(
                        "SELECT ref, user_text, reply FROM turns WHERE seq BETWEEN ? AND ? ORDER BY seq",
                        (first_seq, last_seq),
                    ).fetchall()
                sources = {ref: user_text[:4000] for ref, user_text, _ in rows}
                transcripts = json.dumps([
                    {"ref": ref, "user": sources[ref], "assistant": reply[:4000]}
                    for ref, _, reply in rows
                ], ensure_ascii=False)
                drafts = generate_drafts(config, transcripts=transcripts, sources=sources)
                if not isinstance(drafts, dict) or "error" in drafts:
                    raise ValueError("复盘未生成有效草稿")
                saved = json.dumps(drafts, ensure_ascii=False)
                with _db(config) as conn:
                    conn.execute("UPDATE batches SET drafts = ? WHERE id = ?", (saved, batch_id))
            apply_unattended(config, json.loads(saved), batch_id=batch_id)
            with _db(config) as conn:
                conn.execute("UPDATE turns SET done = 1 WHERE seq BETWEEN ? AND ?", (first_seq, last_seq))
                conn.execute("UPDATE batches SET completed = ?, error = NULL, drafts = NULL WHERE id = ?",
                             (time.time(), batch_id))
        except Exception as exc:
            with _db(config) as conn:
                conn.execute("UPDATE batches SET error = ? WHERE id = ?",
                             (type(exc).__name__, batch_id))
            raise
    return True


def kick(config):
    if not _worker_guard.acquire(blocking=False):
        return

    def worker():
        try:
            run(config)
        except Exception:
            logger.exception("自动复盘失败；批次保留供下次重试")
        finally:
            _worker_guard.release()

    try:
        threading.Thread(target=worker, name="alfred-review", daemon=True).start()
    except Exception:
        _worker_guard.release()
        raise
