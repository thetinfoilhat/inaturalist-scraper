#!/usr/bin/env python3
"""Ingest specimen_questions JSON/JSONL from scripts/data into CockroachDB."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Dict, List

try:
    import psycopg2
except Exception as exc:
    raise SystemExit("Please install: pip install psycopg2-binary") from exc

LOGGER = logging.getLogger("questions-ingest")


def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.DEBUG)
    logging.basicConfig(level=lvl, format="[%(levelname)s] %(message)s")

def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _connect_db() -> "psycopg2.extensions.connection":
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Missing DATABASE_URL for DB connection.")
    rootcert = os.environ.get("PGSSLROOTCERT")
    if rootcert and "sslrootcert=" not in dsn:
        cert_path = rootcert
        if not os.path.isfile(cert_path):
            temp_path = "/tmp/bugbo_pgrootcert.pem"
            try:
                if "BEGIN CERTIFICATE" in rootcert:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(rootcert)
                else:
                    import base64

                    data = base64.b64decode(rootcert)
                    with open(temp_path, "wb") as f:
                        f.write(data)
                cert_path = temp_path
                LOGGER.info("Wrote PGSSLROOTCERT to %s", cert_path)
            except Exception as exc:
                raise SystemExit(f"Invalid PGSSLROOTCERT value: {exc}") from exc
        joiner = "&" if "?" in dsn else "?"
        dsn = f"{dsn}{joiner}sslrootcert={cert_path}"
    return psycopg2.connect(dsn)


def _iter_json_items(path: Path) -> List[Dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    items: List[Dict[str, object]] = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            items.extend(data)
        else:
            raise ValueError("JSON root not list")
    except Exception:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def _upsert(cur, row: Dict[str, object]) -> None:
    cur.execute(
        """
        INSERT INTO public.specimen_questions (
            id, question, tournament, division, options, answers, subtopics,
            difficulty, event, pure_id, rm_type, specimen, statesNationals
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            question = EXCLUDED.question,
            tournament = EXCLUDED.tournament,
            division = EXCLUDED.division,
            options = EXCLUDED.options,
            answers = EXCLUDED.answers,
            subtopics = EXCLUDED.subtopics,
            difficulty = EXCLUDED.difficulty,
            event = EXCLUDED.event,
            pure_id = EXCLUDED.pure_id,
            rm_type = EXCLUDED.rm_type,
            specimen = EXCLUDED.specimen,
            statesNationals = EXCLUDED.statesNationals,
            updated_at = now()
        """,
        (
            row.get("id"),
            row.get("question"),
            row.get("tournament"),
            row.get("division"),
            json.dumps(row.get("options", [])),
            json.dumps(row.get("answers", [])),
            json.dumps(row.get("subtopics", [])),
            row.get("difficulty", 0.5),
            row.get("event"),
            bool(row.get("pure_id", False)),
            row.get("rm_type"),
            row.get("specimen"),
            bool(row.get("statesNationals", False)),
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest specimen_questions JSON files into CockroachDB.")
    ap.add_argument("--data-dir", default="/Users/lm/Bugbo/scripts/data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-level", default="DEBUG")
    args = ap.parse_args()

    _setup_logging(args.log_level)
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")

    data_dir = Path(args.data_dir)
    files = sorted([p for p in data_dir.iterdir() if p.suffix in {".json", ".jsonl"}])
    if not files:
        LOGGER.error("No json/jsonl files found in %s", data_dir)
        return 1

    conn = None if args.dry_run else _connect_db()
    cur = None if args.dry_run else conn.cursor()

    total = 0
    try:
        for path in files:
            LOGGER.info("Processing %s", path.name)
            items = _iter_json_items(path)
            LOGGER.info("Items: %d", len(items))
            if args.dry_run:
                total += len(items)
                continue
            for row in items:
                _upsert(cur, row)
                total += 1
            conn.commit()
        LOGGER.info("Done. Total rows upserted: %d", total)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
