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
    LOGGER.info(
        "Upserted | id=%s | event=%s | specimen=%s | question=%s",
        row.get("id"),
        row.get("event"),
        row.get("specimen"),
        str(row.get("question", ""))[:80].replace("\n", " "),
    )


def _ensure_specimen_questions_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.specimen_questions (
            id UUID PRIMARY KEY,
            question STRING NOT NULL,
            tournament STRING NOT NULL,
            division STRING NOT NULL,
            options JSONB NULL DEFAULT '[]':::JSONB,
            answers JSONB NOT NULL,
            subtopics JSONB NULL DEFAULT '[]':::JSONB,
            difficulty DECIMAL NULL DEFAULT 0.5:::DECIMAL,
            event STRING NOT NULL,
            random_f FLOAT8 NULL DEFAULT random(),
            created_at TIMESTAMPTZ NULL DEFAULT now():::TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NULL DEFAULT now():::TIMESTAMPTZ,
            question_type STRING NULL AS (
              CASE
                WHEN (jsonb_typeof(options) = 'array':::STRING)
                 AND (jsonb_array_length(options) >= 2:::INT8)
                THEN 'mcq':::STRING
                ELSE 'frq':::STRING
              END
            ) STORED,
            pure_id BOOL NULL DEFAULT false,
            rm_type STRING NULL,
            specimen STRING NOT NULL,
            statesNationals BOOL NOT NULL DEFAULT false
        )
        """
    )

def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest specimen_questions JSON files into CockroachDB.")
    ap.add_argument("--data-dir", default="/Users/lm/Bugbo/scripts/data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-level", default="DEBUG")
    ap.add_argument("--rocks", action="store_true")
    ap.add_argument("--entomology", action="store_true")
    ap.add_argument("--water", action="store_true")
    ap.add_argument("--event", action="append", default=[], help="Custom filename base (without extension)")
    args = ap.parse_args()

    _setup_logging(args.log_level)
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")

    data_dir = Path(args.data_dir)
    files = sorted([p for p in data_dir.iterdir() if p.suffix in {".json", ".jsonl"}])
    if args.rocks or args.entomology or args.water or args.event:
        selected: List[Path] = []
        if args.rocks:
            selected.append(data_dir / "rocks_questions.json")
            selected.append(data_dir / "rocks_questions.jsonl")
        if args.entomology:
            selected.append(data_dir / "entomology_questions.json")
            selected.append(data_dir / "entomology_questions.jsonl")
        if args.water:
            selected.append(data_dir / "water_questions.json")
            selected.append(data_dir / "water_questions.jsonl")
        for ev in args.event:
            selected.append(data_dir / f"{ev}.json")
            selected.append(data_dir / f"{ev}.jsonl")
        files = [p for p in selected if p.exists()]
    if not files:
        LOGGER.error("No json/jsonl files found in %s", data_dir)
        return 1

    conn = None if args.dry_run else _connect_db()
    cur = None if args.dry_run else conn.cursor()

    total = 0
    seen_ids = set()
    try:
        if cur:
            _ensure_specimen_questions_table(cur)
        for path in files:
            LOGGER.info("Processing %s", path.name)
            items = _iter_json_items(path)
            LOGGER.info("Items: %d", len(items))
            if args.dry_run:
                total += len(items)
                continue
            for row in items:
                rid = row.get("id")
                if rid in seen_ids:
                    LOGGER.warning("Duplicate id in input: %s", rid)
                    continue
                seen_ids.add(rid)
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
