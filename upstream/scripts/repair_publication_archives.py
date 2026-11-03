"""Explicit, evidence-based repair. Run with the desktop app closed; dry-run by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.home.resolve() / "data" / "binggo.db"
    if not db.is_file():
        parser.error("指定目录没有现有数据库")
    os.environ["BINGGO_HOME"] = str(args.home.resolve())
    os.environ["BINGGO_DISABLE_BACKGROUND"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.activity_archive import restore_publication_archives

    evidence_bytes = args.evidence.read_bytes()
    evidence = json.loads(evidence_bytes.decode("utf-8-sig"))
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("select count(*) from jobs where state in ('running','queued')").fetchone()[0]:
            parser.error("仍有运行或排队中的任务，请结束后再修复")
        uids = [uid for (uid,) in conn.execute("select uid from account_state where key='archives'")]
        now = int(time.time())
        planned = {uid: restore_publication_archives(uid, evidence, now=now) for uid in uids}
        result = {"applied": args.apply, "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                  "evidence_time": evidence["time"], "restored_ids": planned}
        if args.apply and any(planned.values()):
            backup_dir = (args.backup_dir or db.parent / "backups").resolve()
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"binggo.before-publication-restore.{time.time_ns()}.db"
            with sqlite3.connect(backup_path) as backup:
                conn.backup(backup)
            result["backup"] = str(backup_path)
            for uid, ids in planned.items():
                if ids:
                    restored = restore_publication_archives(uid, evidence, now=now, dry_run=False)
                    if restored != ids:
                        raise RuntimeError("归档状态发生变化，已保留备份，请重新检查")
            (backup_dir / "publication-restore-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"applied": args.apply, "counts": {uid: len(ids) for uid, ids in planned.items()},
                      "backup": result.get("backup")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
