#!/usr/bin/env python3
"""
Ingest Bugbo ID questions from rocks_id_questions.json, water_id_questions.json,
entomology_id_questions.json into id_questions. For each MCQ+FRQ pair: derive
specimen from the MCQ answer, match images in rocks/ water/ inat_images/ by
alphabetical-only comparison (e.g. Tiger's Eye -> tigerseye), upload to
Cloudinary, and insert into specimen_pictures.

- Uses new deterministic UUIDs for id_questions and specimen_pictures (does not
  reuse IDs from the JSON).
- id_questions: same shape as id_events (id, question, tournament, division,
  options, answers, subtopics, difficulty, event, images, pure_id, rm_type).
- specimen_pictures: id, specimen, cloudinary_link, event_name. specimen = answer from
  the question pair (MCQ options[answers[0]] or FRQ answers[0]); event_name from
  question event; cloudinary_link from upload secure_url.
"""
import base64
import argparse
import json
import logging
import os
import re
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Tuple

import cloudinary
import cloudinary.uploader
import psycopg
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

logging.basicConfig(level=logging.INFO, format="[ingest-id-questions] %(message)s")
log = logging.getLogger(__name__)

ROOT_CERT_B64 = (
    "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUZhekNDQTFPZ0F3SUJBZ0lSQUlJUXo3RFNRT05aUkdQZ3UyT0Npd0F3RFFZSktvWklodmNOQVFFTEJRQXcKVHpFTE1Ba0dBMVVFQmhNQ1ZWTXhLVEFuQmdOVkJBb1RJRWx1ZEdWeWJtVjBJRk5sWTNWeWFYUjVJRkpsYzJWaApjbU5vSUVkeWIzVndNUlV3RXdZRFZRUURFd3hKVTFKSElGSnZiM1FnV0RFd0hoY05NVFV3TmpBME1URXdORE00CldoY05NelV3TmpBME1URXdORE00V2pCUE1Rc3dDUVlEVlFRR0V3SlZVekVwTUNjR0ExVUVDaE1nU1c1MFpYSnUKWlhRZ1UyVmpkWEpwZEhrZ1VtVnpaV0Z5WTJnZ1IzSnZkWEF4RlRBVEJnTlZCQU1UREVsVFVrY2dVbTl2ZENCWQpNVENDQWlJd0RRWUpLb1pJaHZjTkFRRUJCUUFEZ2dJUEFEQ0NBZ29DZ2dJQkFLM29KSFAwRkRmem01NHJWeWdjCmg3N2N0OTg0a0l4dVBPWlhvSGozZGNLaS92VnFidllBVHlqYjNtaUdiRVNUdHJGai9SUVNhNzhmMHVveG15RisKMFRNOHVrajEzWG5mczdqL0V2RWhta3ZCaW9aeGFVcG1abXlQZmp4d3Y2MHBJZ2J6NU1EbWdLN2lTNCszbVg2VQpBNS9UUjVkOG1VZ2pVK2c0cms4S2I0TXUwVWxYaklCMHR0b3YwRGlOZXdOd0lSdDE4akE4K28rdTNkcGpxK3NXClQ4S09FVXQrend2by83VjNMdlN5ZTByZ1RCSWxESENOQXltZzRWTWs3QlBaN2htL0VMTktqRCtKbzJGUjNxeUgKQjVUMFkzSHNMdUp2VzVpQjRZbGNOSGxzZHU4N2tHSjU1dHVrbWk4bXhkQVE0UTdlMlJDT0Z2dTM5NmozeCtVQwpCNWlQTmdpVjUrSTNsZzAyZFo3N0RuS3hIWnU4QS9sSkJkaUIzUVcwS3RaQjZhd0JkcFVLRDlqZjFiMFNIelV2CktCZHMwcGpCcUFsa2QyNUhON3JPckZsZWFKMS9jdGFKeFFaQktUNVpQdDBtOVNUSkVhZGFvMHhBSDBhaG1iV24KT2xGdWhqdWVmWEtuRWdWNFdlMCtVWGdWQ3dPUGpkQXZCYkkrZTBvY1MzTUZFdnpHNnVCUUUzeERrM1N6eW5UbgpqaDhCQ05BdzFGdHhOclFIdXNFd01GeEl0NEk3bUtaOVlJcWlveW1DekxxOWd3UWJvb01EUWFIV0JmRWJ3cmJ3CnFIeUdPMGFvU0NxSTNIYWFkcjhmYXFVOUdZL3JPUE5rM3NnckRRb28vL2ZiNGhWQzFDTFFKMTNoZWY0WTUzQ0kKclU3bTJZczZ4dDBuVVc3L3ZHVDFNME5QQWdNQkFBR2pRakJBTUE0R0ExVWREd0VCL3dRRUF3SUJCakFQQmdOVgpIUk1CQWY4RUJUQURBUUgvTUIwR0ExVWREZ1FXQkJSNXRGbm1lN2JsNUFGemdBaUl5QnBZOXVtYmJqQU5CZ2txCmhraUc5dzBCQVFzRkFBT0NBZ0VBVlI5WXFieXlxRkRRRExIWUdta2dKeWtJckdGMVhJcHUrSUxsYVMvVjlsWkwKdWJoekVGblRJWmQrNTB4eCs3TFNZSzA1cUF2cUZ5RldoZkZRRGxucnp1Qlo2YnJKRmUrR25ZK0VnUGJrNlpHUQozQmViWWh0RjhHYVYwbnh2d3VvNzd4L1B5OWF1Si9HcHNNaXUvWDErbXZvaUJPdi8yWC9xa1NzaXNSY09qL0tLCk5GdFkyUHdCeVZTNXVDYk1pb2d6aVV3dGhEeUMzKzZXVndXNkxMdjN4TGZIVGp1Q3ZqSElJbk56a3RIQ2dLUTUKT1JBekk0Sk1QSitHc2xXWUhiNHBob3dpbTU3aWF6dFhPb0p3VGR3Sng0bkxDZ2ROYk9oZGpzbnZ6cXZIdTdVcgpUa1hXU3RBbXpPVnl5Z2hxcFpYakZhSDNwTzNKTEYrbCsvK3NLQUl1dnRkN3UrTnhlNUFXMHdkZVJsTjhOd2RDCmpOUEVscHpWbWJVcTRKVWFnRWl1VERrSHpzeEhwRktWSzdxNCs2M1NNMU45NVIxTmJkV2hzY2RDYitaQUp6VmMKb3lpM0I0M25qVE9RNXlPZisxQ2NlV3hHMWJRVnM1WnVmcHNNbGpxNFVpMC8xbHZoK3dqQ2hQNGtxS09KMnF4cQo0Umdxc2FoRFlWdlRIOXc3alhieUxlaU5kZDhYTTJ3OVUvdDd5MEZmLzl5aTBHRTQ0WmE0ckYyTE45ZDExVFBBCm1SR3VuVUhCY25XRXZnSkJRbDluSkVpVTBac252Z2MvdWJoUGdYUlI0WHEzN1owajRyN2cxU2dFRXp3eEE1N2QKZW15UHhnY1l4bi9lUjQ0L0tKNEVCcytsVkRSM3ZleUptK2tYUTk5YjIxLytqaDVYb3MxQW5YNWlJdHJlR0NjPQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0tCi0tLS0tQkVHSU4gQ0VSVElGSUNBVEUtLS0tLQpNSUlDR3pDQ0FhR2dBd0lCQWdJUVFkS2QwWExxN3FlQXdTeHM2UytIVWpBS0JnZ3Foa2pPUFFRREF6QlBNUXN3CkNRWURWUVFHRXdKVlV6RXBNQ2NHQTFVRUNoTWdTVzUwWlhKdVpYUWdVMlZqZFhKcGRIa2dVbVZ6WldGeVkyZ2cKUjNKdmRYQXhGVEFUQmdOVkJBTVRERWxUVWtjZ1VtOXZkQ0JZTWpBZUZ3MHlNREE1TURRd01EQXdNREJhRncwMApNREE1TVRjeE5qQXdNREJhTUU4eEN6QUpCZ05WQkFZVEFsVlRNU2t3SndZRFZRUUtFeUJKYm5SbGNtNWxkQ0JUClpXTjFjbWwwZVNCU1pYTmxZWEpqYUNCSGNtOTFjREVWTUJNR0ExVUVBeE1NU1ZOU1J5QlNiMjkwSUZneU1IWXcKRUFZSEtvWkl6ajBDQVFZRks0RUVBQ0lEWWdBRXpadlZuNENEQ3V3SlN2TVdTajVjejNlczNtY0ZEUjBIdHR3VworMXFMRk52aWNXREV1a1dWRVltTzZnYmY5eW9XSEtTNXhjVXk0QVBnSG9JWU9JdlhSZGdLYW03bUFIZjdBbEY5Ckl0Z0ticHBiZDkvdytrSHNPZHgxeW1nSERCL3FvMEl3UURBT0JnTlZIUThCQWY4RUJBTUNBUVl3RHdZRFZSMFQKQVFIL0JBVXdBd0VCL3pBZEJnTlZIUTRFRmdRVWZFS1dydDVMU0R2Nmt2aWVqTTl0aTZseU41VXdDZ1lJS29aSQp6ajBFQXdNRGFBQXdaUUl3ZTNsT1JsQ0V3a1NIUmh0RmNQOVltZDcwL2FUU1ZhWWdMWFRXTkx4Qm8xQmZBU2RXCnRMNG5kUWF2RWk1MW1JMzhBakVBaS9WM2JOVElaYXJnQ3l6dUZKMG5ONlQ1VTZWUjVDbUQxL2lRTVZ0Q253cjEKL3E0QWFPZU1TUSsyYjF0YkZmTG4KLS0tLS1FTkQgQ0VSVElGSUNBVEUtLS0tLQ=="
)

NS = uuid.NAMESPACE_URL
EXT = (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bmp")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def alpha_only(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def natural_sort_key(path: str) -> list:
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def connect_db():
    cert_path = None
    try:
        db_url = os.getenv("DATABASE_URL")
        pgssl_b64 = os.getenv("PGSSLROOTCERT")
        if pgssl_b64:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as f:
                f.write(base64.b64decode(pgssl_b64).decode("utf-8"))
                cert_path = f.name
        else:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as f:
                f.write(base64.b64decode(ROOT_CERT_B64).decode("utf-8"))
                cert_path = f.name
        if db_url:
            sep = "&" if "?" in db_url else "?"
            if "sslmode=" not in db_url and "sslrootcert=" not in db_url:
                db_url = f"{db_url}{sep}sslmode=verify-full&sslrootcert={cert_path}"
            dsn = db_url
        else:
            dsn = (
                "postgresql://kudos:64vJDxPV2kl9MSz3B168Ug@"
                "scioly-19617.j77.aws-us-east-2.cockroachlabs.cloud:26257/defaultdb?"
                f"sslmode=verify-full&sslrootcert={cert_path}"
            )
        conn = psycopg.connect(dsn)
        conn.autocommit = True
        log.info("Connected to CockroachDB")
        return conn
    finally:
        if cert_path:
            try:
                os.unlink(cert_path)
            except Exception:
                pass


def ensure_id_questions_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.id_questions (
              id UUID PRIMARY KEY,
              question STRING NOT NULL,
              tournament STRING NOT NULL,
              division STRING NOT NULL,
              options JSONB DEFAULT '[]',
              answers JSONB NOT NULL,
              subtopics JSONB DEFAULT '[]',
              difficulty DECIMAL DEFAULT 0.5,
              event STRING NOT NULL,
              images JSONB DEFAULT '[]',
              pure_id BOOL DEFAULT false,
              rm_type STRING,
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )


def ensure_specimen_pictures_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.specimen_pictures (
              id UUID PRIMARY KEY,
              specimen STRING[] NOT NULL,
              cloudinary_link STRING NOT NULL,
              event_name STRING NOT NULL
            )
            """
        )


def upsert_id_question(conn, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.id_questions
            (id, question, tournament, division, options, answers, subtopics, difficulty, event, images, pure_id, rm_type)
            VALUES (%(id)s, %(question)s, %(tournament)s, %(division)s, %(options)s::jsonb, %(answers)s::jsonb, %(subtopics)s::jsonb, %(difficulty)s, %(event)s, %(images)s::jsonb, %(pure_id)s, %(rm_type)s)
            ON CONFLICT (id) DO UPDATE SET
              question = EXCLUDED.question,
              tournament = EXCLUDED.tournament,
              division = EXCLUDED.division,
              options = EXCLUDED.options,
              answers = EXCLUDED.answers,
              subtopics = EXCLUDED.subtopics,
              difficulty = EXCLUDED.difficulty,
              event = EXCLUDED.event,
              images = EXCLUDED.images,
              pure_id = EXCLUDED.pure_id,
              rm_type = EXCLUDED.rm_type,
              updated_at = now()
            """,
            {
                "id": row["id"],
                "question": row["question"],
                "tournament": row["tournament"],
                "division": row["division"],
                "options": json.dumps(row.get("options") or []),
                "answers": json.dumps(row.get("answers") or []),
                "subtopics": json.dumps(row.get("subtopics") or []),
                "difficulty": float(row.get("difficulty", 0.5)),
                "event": row["event"],
                "images": json.dumps(row.get("images") or []),
                "pure_id": bool(row.get("pure_id", False)),
                "rm_type": row.get("rm_type"),
            },
        )
    log.info("Upserted question %s (%s)", row.get("id"), row.get("event"))


def _prepare_for_upload(src_path: str, max_dim: Tuple[int, int] = (1600, 1200)) -> str:
    try:
        with Image.open(src_path) as im:
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail(max_dim, Image.LANCZOS)
            for quality in (85, 80, 75, 70, 65, 60):
                tmp = BytesIO()
                im.save(tmp, format="WEBP", quality=quality, method=6)
                if tmp.tell() < MAX_UPLOAD_BYTES:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".webp") as f:
                        f.write(tmp.getvalue())
                        return f.name
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webp") as f:
                im.save(f, format="WEBP", quality=55, method=6)
                return f.name
    except Exception as e:
        log.warning("Pillow could not open %s: %s. Using raw path.", src_path, e)
        return src_path


def upload_to_cloudinary(path: str, image_name: str, folder: str) -> Optional[str]:
    try:
        clean = re.sub(r"[\s()\.\-]+", "_", (image_name or "").lower()).strip("_") or "img"
        public_id = f"{folder}/{clean}"
        prepped = _prepare_for_upload(path)
        res = cloudinary.uploader.upload(
            prepped,
            public_id=public_id,
            resource_type="image",
            format="webp",
            overwrite=True,
            transformation=[{"width": 800, "height": 600, "crop": "limit", "quality": "auto"}],
        )
        if prepped != path:
            try:
                os.unlink(prepped)
            except Exception:
                pass
        url = res.get("secure_url")
        if not url:
            log.warning("Cloudinary upload missing secure_url for %s: %s", public_id, res)
            return None
        return url
    except Exception as e:
        log.warning("Failed to upload %s: %s", path, e)
        return None


def find_matching_images(image_dir: str, specimen: str) -> List[str]:
    want = alpha_only(specimen)
    if not want:
        return []
    out: List[str] = []
    try:
        for f in os.listdir(image_dir):
            base, ext = os.path.splitext(f)
            if ext.lower() not in EXT:
                continue
            base = re.sub(r"\(\d+\)$", "", base).strip()
            if alpha_only(base) == want:
                out.append(os.path.join(image_dir, f))
    except (OSError, PermissionError) as e:
        log.warning("Cannot list %s: %s", image_dir, e)
    out.sort(key=natural_sort_key)
    return out


def normalize_specimen_names(raw: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for name in raw:
        cleaned = re.sub(r"\s{2,}", " ", str(name).strip())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def pair_mcq_frq(items: List[dict]) -> List[Tuple[Optional[dict], Optional[dict], str, List[str]]]:
    pairs: List[Tuple[Optional[dict], Optional[dict], str, List[str]]] = []
    i = 0
    while i < len(items):
        a = items[i] if i < len(items) else None
        b = items[i + 1] if i + 1 < len(items) else None
        mcq = a if (a and a.get("options")) else (b if (b and b.get("options")) else None)
        frq = b if mcq is a else a
        opt = (mcq or {}).get("options") or []
        ans = (mcq or {}).get("answers") or []
        specimen = str(opt[int(ans[0])]) if (ans and ans[0] is not None and opt and 0 <= int(ans[0]) < len(opt)) else None
        if not specimen and frq:
            a0 = (frq.get("answers") or [None])[0]
            specimen = str(a0) if a0 is not None else None
        raw_names: List[str] = []
        if mcq and isinstance(mcq.get("specimen_names"), list):
            raw_names.extend(mcq.get("specimen_names") or [])
        if frq and isinstance(frq.get("specimen_names"), list):
            raw_names.extend(frq.get("specimen_names") or [])
        if specimen:
            raw_names.append(specimen)
        specimen_names = normalize_specimen_names(raw_names)
        if not specimen:
            i += 2
            continue
        if not specimen_names:
            specimen_names = [specimen]
        pairs.append((mcq, frq, specimen, specimen_names))
        i += 2
    return pairs


def insert_specimen_picture(
    conn, row_id: str, specimen_names: List[str], cloudinary_link: str, event_name: str
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.specimen_pictures (id, specimen, cloudinary_link, event_name)
            VALUES (%(id)s, %(specimen)s::STRING[], %(cloudinary_link)s, %(event_name)s)
            ON CONFLICT (id) DO UPDATE SET cloudinary_link = EXCLUDED.cloudinary_link
            """,
            {
                "id": row_id,
                "specimen": specimen_names,
                "cloudinary_link": cloudinary_link,
                "event_name": event_name,
            },
        )
    log.info("Upserted specimen picture %s (%s)", ", ".join(specimen_names), event_name)


def load_json(path: str) -> List[dict]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def run_event(
    conn,
    event_key: str,
    json_path: str,
    image_dir: str,
    cloudinary_folder: str,
    cloudinary_config: dict,
    upload_cache: dict,
) -> None:
    cloudinary.config(**cloudinary_config, secure=True)
    items = load_json(json_path)
    pairs = pair_mcq_frq(items)
    log.info("[%s] %d MCQ+FRQ pairs from %s", event_key, len(pairs), json_path)
    for mcq, frq, specimen, specimen_names in pairs:
        spec_key = alpha_only(specimen)
        id_mcq = str(uuid.uuid5(NS, f"bugbo:id_questions:{event_key}:{spec_key}:mcq"))
        id_frq = str(uuid.uuid5(NS, f"bugbo:id_questions:{event_key}:{spec_key}:frq"))
        if mcq:
            row = {
                "id": id_mcq,
                "question": mcq.get("question", ""),
                "tournament": mcq.get("tournament", "ID Event"),
                "division": mcq.get("division", "B/C"),
                "options": mcq.get("options", []),
                "answers": mcq.get("answers", []),
                "subtopics": mcq.get("subtopics", []),
                "difficulty": mcq.get("difficulty", 0.4),
                "event": mcq.get("event", ""),
                "images": [],
                "pure_id": True,
                "rm_type": mcq.get("rm_type"),
            }
            log.info("[%s] Upserting MCQ: %s", event_key, row.get("question", "")[:120])
            upsert_id_question(conn, row)
        if frq:
            row = {
                "id": id_frq,
                "question": frq.get("question", ""),
                "tournament": frq.get("tournament", "ID Event"),
                "division": frq.get("division", "B/C"),
                "options": frq.get("options", []),
                "answers": frq.get("answers", []),
                "subtopics": frq.get("subtopics", []),
                "difficulty": frq.get("difficulty", 0.6),
                "event": frq.get("event", ""),
                "images": [],
                "pure_id": True,
                "rm_type": frq.get("rm_type"),
            }
            log.info("[%s] Upserting FRQ: %s", event_key, row.get("question", "")[:120])
            upsert_id_question(conn, row)
        event_name = (mcq or frq or {}).get("event") or ""
        matched_paths: List[str] = []
        for name in specimen_names:
            matched_paths.extend(find_matching_images(image_dir, name))
        seen_paths: set[str] = set()
        paths: List[str] = []
        for p in matched_paths:
            if p in seen_paths:
                continue
            seen_paths.add(p)
            paths.append(p)
        log.info("[%s] Found %d image(s) for %s", event_key, len(paths), specimen)
        for p in paths:
            cache_key = (event_key, p)
            if cache_key in upload_cache:
                url = upload_cache[cache_key]
            else:
                url = upload_to_cloudinary(p, os.path.basename(p), cloudinary_folder)
                if url:
                    upload_cache[cache_key] = url
            if url:
                sp_id = str(
                    uuid.uuid5(
                        NS,
                        f"bugbo:specimen_pictures:{event_name}:{specimen}:{os.path.basename(p)}",
                    )
                )
                log.info("[%s] Upserting image for %s -> %s", event_key, specimen, url)
                insert_specimen_picture(conn, sp_id, specimen_names, url, event_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Bugbo ID questions into CockroachDB.")
    parser.add_argument("--rocks", action="store_true", help="Ingest rocks only")
    parser.add_argument("--water", action="store_true", help="Ingest water only")
    parser.add_argument("--entomology", action="store_true", help="Ingest entomology only")
    args = parser.parse_args()

    selected = {k for k, v in {"rocks": args.rocks, "water": args.water, "entomology": args.entomology}.items() if v}
    if not selected:
        selected = {"rocks", "water", "entomology"}

    bugbo = Path(__file__).resolve().parent
    rocks_json = bugbo / "rocks_id_questions.json"
    water_json = bugbo / "water_id_questions.json"
    ento_json = bugbo / "entomology_id_questions.json"
    rocks_dir = str(bugbo / "rocks")
    water_dir = str(bugbo / "water")
    inat_dir = str(bugbo / "inat_images")
    for p in (rocks_json, water_json, ento_json):
        if not p.exists():
            log.error("Missing %s", p)
            return 1
    rocks_cloudinary = {
        "cloud_name": "dw7vhwjv4",
        "api_key": "383621762414734",
        "api_secret": "-ym3tVAjU6xnynOS_xg-jf-n3-s",
    }
    water_cloudinary = {
        "cloud_name": "dzwazcehy",
        "api_key": "812999824174966",
        "api_secret": "lW0dYSPjJQhrBEc3G_iPFXcufEQ",
    }
    ento_cloudinary = water_cloudinary
    conn = connect_db()
    try:
        ensure_id_questions_table(conn)
        ensure_specimen_pictures_table(conn)
        upload_cache: dict = {}
        if "rocks" in selected:
            run_event(
                conn,
                "rocks",
                str(rocks_json),
                rocks_dir,
                "bugbo/rocks",
                rocks_cloudinary,
                upload_cache,
            )
        if "water" in selected:
            run_event(
                conn,
                "water",
                str(water_json),
                water_dir,
                "bugbo/water",
                water_cloudinary,
                upload_cache,
            )
        if "entomology" in selected:
            run_event(
                conn,
                "entomology",
                str(ento_json),
                inat_dir,
                "bugbo/entomology",
                ento_cloudinary,
                upload_cache,
            )
        log.info("Ingest done. Upload cache size: %d", len(upload_cache))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
