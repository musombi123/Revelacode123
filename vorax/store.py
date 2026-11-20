"""SQLite storage for verified T-Lang only; no prompts or teacher prose."""
from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from pathlib import Path
from .vlang import fingerprint, validate_template

class TemplateStore:
    def __init__(self,path: str="data/legend.sqlite3"):
        self.path=path
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, body TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS child_state (child_id TEXT PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()
    def add(self,t: dict):
        validate_template(t)
        body=json.dumps(t,sort_keys=True,separators=(",",":"),ensure_ascii=False); fp=fingerprint(t)
        with self._connect() as db:
            old=db.execute("SELECT fingerprint FROM templates WHERE id=?",(t["id"],)).fetchone()
            if old and old["fingerprint"]!=fp: raise ValueError("A different template already uses this ID")
            dup=db.execute("SELECT id FROM templates WHERE fingerprint=?",(fp,)).fetchone()
            if dup and dup["id"]!=t["id"]: raise ValueError(f"Duplicate procedure already exists as {dup['id']}")
            db.execute("INSERT OR IGNORE INTO templates(id,body,fingerprint) VALUES(?,?,?)",(t["id"],body,fp))
    def get(self,template_id: str):
        with self._connect() as db: row=db.execute("SELECT body FROM templates WHERE id=?",(template_id,)).fetchone()
        return json.loads(row["body"]) if row else None
    def all(self):
        with self._connect() as db: rows=db.execute("SELECT body FROM templates ORDER BY id").fetchall()
        return [json.loads(row["body"]) for row in rows]
    def record_failure(self,child_id: str,threshold: int=100)->int:
        with self._connect() as db:
            db.execute("""INSERT INTO child_state(child_id,consecutive_failures,blocked) VALUES(?,1,0)
            ON CONFLICT(child_id) DO UPDATE SET consecutive_failures=consecutive_failures+1,
            blocked=CASE WHEN consecutive_failures+1>=? THEN 1 ELSE blocked END,updated_at=CURRENT_TIMESTAMP""",(child_id,threshold))
            row=db.execute("SELECT consecutive_failures FROM child_state WHERE child_id=?",(child_id,)).fetchone()
        return int(row["consecutive_failures"])
    def record_success(self,child_id: str):
        with self._connect() as db:
            db.execute("""INSERT INTO child_state(child_id,consecutive_failures,blocked) VALUES(?,0,0)
            ON CONFLICT(child_id) DO UPDATE SET consecutive_failures=0,updated_at=CURRENT_TIMESTAMP""",(child_id,))
    def child_blocked(self,child_id: str)->bool:
        with self._connect() as db: row=db.execute("SELECT blocked FROM child_state WHERE child_id=?",(child_id,)).fetchone()
        return bool(row["blocked"]) if row else False
