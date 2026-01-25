#!/usr/bin/env python3
"""
Generate ID-only questions (1 MCQ + 1 FRQ per specimen) for Rocks, Water Quality, and
Entomology. Reads specimen names from rocks.txt, water.json (common_name), and
data/state/NATS/list.txt. Writes 3 JSON files: rocks_id_questions.json,
water_id_questions.json, entomology_id_questions.json.

Gemini receives only the specimen name (no images). Generates pure_id=true MCQ and FRQ.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import List

try:
    from google import genai
    from google.genai import types
except Exception:
    print("Please install: pip install google-genai", file=sys.stderr)
    sys.exit(1)


LOGGER = logging.getLogger("bugbo-id-gen")


HARDCODED_KEYS: List[str] = [
    "AIzaSyBPilBHjt5XuvSaxU4wZIVw_rYDF5X6Si4"
]

SYSTEM_INSTRUCTION = (
    "You are an expert Science Olympiad question author. "
    "Think step-by-step internally. Use hidden scratchpad reasoning but NEVER include "
    "reasoning, steps, or commentary in outputs. Only emit the final strict JSON."
)


def setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stderr, level=lvl, format="[%(levelname)s] %(message)s")


def canonical_answer(name: str) -> str:
    cleaned = re.sub(r"[_\s]+", " ", name.strip())
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.title()

def normalize_specimen_names(specimen_name: str, answers: object) -> list[str]:
    names: list[str] = []
    if isinstance(answers, list):
        for item in answers:
            if isinstance(item, str):
                names.append(canonical_answer(item))
    if not names:
        names = [canonical_answer(specimen_name)]
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out

def load_keys(path: str, max_keys: int | None) -> List[str]:
    keys: List[str] = []
    source = "file"
    if path == "HARDCODED" or not os.path.isfile(path):
        keys = [k for k in HARDCODED_KEYS if k]
        source = "hardcoded"
    else:
        with Path(path).open("r", encoding="utf-8") as f:
            keys = [ln.strip() for ln in f if ln.strip()]
    seen: set[str] = set()
    filtered: List[str] = []
    for k in keys:
        if not k.startswith("AIza") or k in seen:
            continue
        seen.add(k)
        filtered.append(k)
    if max_keys is not None:
        filtered = filtered[:max_keys]
    if not filtered:
        raise RuntimeError("No valid API keys found")
    LOGGER.info("Loaded %d API keys (%s)", len(filtered), source)
    return filtered


# --- Load specimen lists ---


def load_rocks_specimens(path: str) -> List[str]:
    with Path(path).open("r", encoding="utf-8", errors="ignore") as f:
        return [ln.strip() for ln in f if ln.strip()]


def load_water_common_names(path: str) -> List[str]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    out: List[str] = []
    for entry in data:
        if isinstance(entry, dict) and "common_name" in entry:
            out.append(entry["common_name"])
    return out


def load_entomology_specimens(path: str) -> List[str]:
    with Path(path).open("r", encoding="utf-8", errors="ignore") as f:
        return [ln.strip().lower() for ln in f if ln.strip()]


# --- ID-only prompts (1 MCQ + 1 FRQ, no stimulus-based) ---


# Subtopics from id_events/scripts/generate_rocks.py
ROCKS_SUBTOPICS = [
    "Igneous", "Metamorphic", "Sedimentary", "Mineral Properties", "Crystal Systems"
]


def build_rocks_id_prompt(common_name: str) -> str:
    schema = {
        "id": "uuid string (v5 deterministic if possible)",
        "question": "string",
        "tournament": "string",
        "division": "string (e.g., 'B/C')",
        "options": "array (empty for FRQ, 3-6 options for MCQ)",
        "answers": "array: for MCQ [single 0-based index]; for FRQ [accepted strings]",
        "subtopics": "array of strings",
        "difficulty": "number between 0 and 1",
        "event": "string (must be 'Rocks and Minerals')",
        "images": "array (must be [])",
        "pure_id": "boolean (must be true)",
        "rm_type": "string: 'rock' or 'mineral'",
    }
    return (
        "You are an expert Science Olympiad question author for Rocks & Minerals.\n\n"
        f"Specimen name: {common_name}\n\n"
        "Generate EXACTLY 2 identification-only questions (pure_id=true) for this specimen:\n"
        "1. One MCQ: 'What is the common name of this rock/mineral?' with 4 options. The correct answer must be the specimen name.\n"
        "2. One FRQ: 'What is the common name of this rock/mineral?' (free response). Accepted answers must include the specimen name.\n\n"
        "MCQ distractors: Use VARIED wrong answers—pick distractors from different rock/mineral types, not all similar names. "
        "e.g. for a mineral like Albite use distractors such as Bauxite, Granite, Shale (mix minerals, igneous, sedimentary) rather than Albite, Actinolite, Agate, Amethyst (all mineral names that look alike).\n\n"
        "Clean the specimen name: remove numbers/parentheses, replace underscores with spaces, capitalize (e.g. 'rose_quartz' -> 'Rose quartz').\n\n"
        "Requirements:\n"
        "- OUTPUT STRICT JSON ONLY: an array of exactly 2 objects. No prose/markdown.\n"
        f"- Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        "- answers: MCQ = [0-based index of correct option]; FRQ = [accepted strings, case-insensitive].\n"
        "- tournament: 'ID Event', division: 'B/C', event: 'Rocks and Minerals'.\n"
        "- images: [] for both (no image provided). pure_id: true for both.\n"
        f"- subtopics: MUST use ONLY from: {', '.join(ROCKS_SUBTOPICS)}. Include the specimen's rock type (Igneous, Metamorphic, or Sedimentary) plus others as applicable (1-3 subtopics).\n"
        "- rm_type: 'rock' or 'mineral' for each question.\n"
    )


# Subtopics from id_events/scripts/generate_water_quality.py
WATER_SUBTOPICS = [
    "pH", "Dissolved Oxygen", "Nutrients", "Pollutants", "Testing", "Identification"
]


def build_water_id_prompt(common_name: str) -> str:
    schema = {
        "id": "uuid string",
        "question": "string",
        "tournament": "string",
        "division": "string",
        "options": "array (empty for FRQ, 3-6 for MCQ)",
        "answers": "array: MCQ [index]; FRQ [strings]",
        "subtopics": "array of strings",
        "difficulty": "number 0-1",
        "event": "string (must be 'Water Quality' or 'Water Quality - Freshwater')",
        "images": "array (must be [])",
        "pure_id": "boolean (must be true)",
    }
    return (
        "You are an expert Science Olympiad question author for Water Quality.\n\n"
        f"Organism common name: {common_name}\n\n"
        "Generate EXACTLY 2 identification-only questions (pure_id=true):\n"
        "1. One MCQ: 'What is the common name of this organism?' with 4 options. Correct answer = the organism name.\n"
        "2. One FRQ: 'What is the common name of this organism?' (free response). Accepted answers must include the organism name.\n\n"
        "MCQ distractors: Use VARIED wrong answers—pick organisms from different groups (e.g. mix insects, mollusks, worms, plants, fish) rather than all similar-sounding or same-class names.\n\n"
        "Requirements:\n"
        "- OUTPUT STRICT JSON ONLY: an array of exactly 2 objects. No prose/markdown.\n"
        f"- Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        "- answers: MCQ = [index]; FRQ = [accepted strings]. tournament: 'ID Event - Water Quality', division: 'B/C', event: 'Water Quality' or 'Water Quality - Freshwater'.\n"
        "- images: [] for both (no image provided). pure_id: true.\n"
        f"- subtopics: MUST use ONLY from: {', '.join(WATER_SUBTOPICS)}. Include 'Identification' (1-3 subtopics per question).\n"
    )


# Subtopics from id_events/scripts/generate_entomology.py
ENTOMOLOGY_SUBTOPICS = [
    "Insect Anatomy", "Life Cycle", "Behavior", "Classification", "Ecology"
]


def build_entomology_id_prompt(common_name: str) -> str:
    schema = {
        "id": "uuid string",
        "question": "string",
        "tournament": "string",
        "division": "string",
        "options": "array (empty for FRQ, 3-6 for MCQ)",
        "answers": "array: MCQ [index]; FRQ [strings]",
        "subtopics": "array of strings",
        "difficulty": "number 0-1",
        "event": "string (must be 'Entomology')",
        "images": "array (must be [])",
        "pure_id": "boolean (must be true)",
    }
    return (
        "You are an expert Science Olympiad question author for Entomology.\n\n"
        f"Specimen (family/order): {common_name}\n\n"
        "Generate EXACTLY 2 identification-only questions (pure_id=true):\n"
        "1. One MCQ: 'What is the family/order of this insect?' with 4 options. Correct answer = the specimen name (e.g. Acrididae).\n"
        "2. One FRQ: 'What is the family/order of this insect?' (free response). Accepted answers must include the specimen name.\n\n"
        "MCQ distractors: Use VARIED wrong answers—pick families/orders from different groups (e.g. mix Orthoptera, Coleoptera, Lepidoptera, Diptera, Hymenoptera) rather than all similar -idae endings (e.g. Acrididae, Tettigoniidae, Gryllidae, Rhaphidophoridae).\n\n"
        "Use the taxonomic name as given; capitalize appropriately (e.g. acrididae -> Acrididae).\n\n"
        "Requirements:\n"
        "- OUTPUT STRICT JSON ONLY: an array of exactly 2 objects. No prose/markdown.\n"
        f"- Schema: {json.dumps(schema, ensure_ascii=False)}\n"
        "- answers: MCQ = [index]; FRQ = [strings]. tournament: 'ID Event', division: 'B/C', event: 'Entomology'.\n"
        "- images: [] for both (no image provided). pure_id: true.\n"
        f"- subtopics: MUST use ONLY from: {', '.join(ENTOMOLOGY_SUBTOPICS)}. Include 'Classification' (1-2 subtopics per question).\n"
    )


def call_gemini(model_name: str, key: str, prompt: str) -> str:
    client = genai.Client(api_key=key)
    contents = [types.Part.from_text(text=prompt)]
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.6,
                top_p=0.9,
                max_output_tokens=65535,
                system_instruction=SYSTEM_INSTRUCTION,
            ),
        )
        txt = (getattr(resp, "text", None) or "").strip()
        if txt:
            return txt
    except Exception as e:
        LOGGER.debug("Gemini call failed: %s", e)
    return ""


def parse_json_array(raw: str) -> List[dict]:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            text = parts[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
    try:
        if not text:
            return []
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            return json.loads(m.group(0))
        return []


def normalize_rocks(specimen_name: str, items: List[dict]) -> List[dict]:
    out: List[dict] = []
    base = specimen_name
    expected = canonical_answer(specimen_name)
    for i, q in enumerate(items or []):
        if not q.get("pure_id", False):
            continue
        q.setdefault("tournament", "ID Event")
        q.setdefault("division", "B/C")
        q.setdefault("event", "Rocks and Minerals")
        q.setdefault("subtopics", [])
        q.setdefault("options", [])
        q["images"] = []
        q["pure_id"] = True
        q["specimen_names"] = normalize_specimen_names(specimen_name, q.get("answers"))
        rm = (q.get("rm_type") or "").lower().strip()
        q["rm_type"] = "rock" if rm == "rock" else "mineral"
        ns = uuid.NAMESPACE_URL
        q["id"] = str(uuid.uuid5(ns, f"bugbo:rocks:{base}:{i}:{q.get('question','')[:64]}"))
        ans = q.get("answers")
        if isinstance(ans, (int, float)):
            q["answers"] = [int(ans)]
        elif isinstance(ans, list):
            q["answers"] = [expected] if ans and isinstance(ans[0], str) else ans
        else:
            q["answers"] = []
        q["difficulty"] = 0.4 if q.get("options") else 0.6
        out.append(q)
    return out


def normalize_water(specimen_name: str, items: List[dict]) -> List[dict]:
    out: List[dict] = []
    base = specimen_name
    expected = canonical_answer(specimen_name)
    for i, q in enumerate(items or []):
        if not q.get("pure_id", False):
            continue
        q.setdefault("tournament", "ID Event - Water Quality")
        q.setdefault("division", "B/C")
        q.setdefault("event", "Water Quality")
        q.setdefault("subtopics", [])
        q.setdefault("options", [])
        q["images"] = []
        q["pure_id"] = True
        q["specimen_names"] = normalize_specimen_names(specimen_name, q.get("answers"))
        ns = uuid.NAMESPACE_URL
        q["id"] = str(uuid.uuid5(ns, f"bugbo:water:{base}:{i}:{q.get('question','')[:64]}"))
        ans = q.get("answers")
        if isinstance(ans, (int, float)):
            q["answers"] = [int(ans)]
        elif isinstance(ans, list):
            q["answers"] = [expected] if ans and isinstance(ans[0], str) else ans
        else:
            q["answers"] = []
        q["difficulty"] = 0.4 if q.get("options") else 0.6
        out.append(q)
    return out


def normalize_entomology(specimen_name: str, items: List[dict]) -> List[dict]:
    out: List[dict] = []
    base = specimen_name
    expected = canonical_answer(specimen_name)
    for i, q in enumerate(items or []):
        if not q.get("pure_id", False):
            continue
        q.setdefault("tournament", "ID Event")
        q.setdefault("division", "B/C")
        q.setdefault("event", "Entomology")
        q.setdefault("subtopics", [])
        q.setdefault("options", [])
        q["images"] = []
        q["pure_id"] = True
        q["specimen_names"] = normalize_specimen_names(specimen_name, q.get("answers"))
        ns = uuid.NAMESPACE_URL
        q["id"] = str(uuid.uuid5(ns, f"bugbo:ento:{base}:{i}:{q.get('question','')[:64]}"))
        ans = q.get("answers")
        if isinstance(ans, (int, float)):
            q["answers"] = [int(ans)]
        elif isinstance(ans, list):
            q["answers"] = [expected] if ans and isinstance(ans[0], str) else ans
        else:
            q["answers"] = []
        q["difficulty"] = 0.4 if q.get("options") else 0.6
        out.append(q)
    return out


def process_event(
    event_name: str,
    specimens: List[str],
    build_prompt_fn: object,
    normalize_fn: object,
    model_name: str,
    keys: List[str],
    output_path: str,
    sleep_ms: int,
    dump_responses_dir: str | None,
) -> List[dict]:
    out: List[dict] = []
    key_idx = 0

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with Path(output_path).open("w", encoding="utf-8") as f:
        f.write("[")
        wrote_any = False

        for idx, specimen_name in enumerate(specimens):
            prompt = build_prompt_fn(specimen_name)
            LOGGER.info(
                "[%s] [%d/%d] %s",
                event_name,
                idx + 1,
                len(specimens),
                specimen_name,
            )

            retries = 0
            while True:
                key = keys[(key_idx + retries) % len(keys)]
                try:
                    if sleep_ms > 0:
                        time.sleep(sleep_ms / 1000.0)
                    raw = call_gemini(model_name, key, prompt)
                    if not raw:
                        raise ValueError("Empty Gemini response")
                    items = parse_json_array(raw)
                    # Keep only pure_id; take up to 1 MCQ and 1 FRQ
                    normalized = normalize_fn(specimen_name, items)
                    mcq = [q for q in normalized if q.get("options")]
                    frq = [q for q in normalized if not q.get("options")]
                    chosen: List[dict] = []
                    if mcq:
                        chosen.append(mcq[0])
                    if frq:
                        chosen.append(frq[0])
                    out.extend(chosen)
                    key_idx = (key_idx + 1) % len(keys)

                    for item in chosen:
                        if wrote_any:
                            f.write(",\n")
                        json.dump(item, f, ensure_ascii=False)
                        f.flush()
                        wrote_any = True

                    if dump_responses_dir:
                        os.makedirs(dump_responses_dir, exist_ok=True)
                        safe = re.sub(r"[^\w\-]", "_", specimen_name)[:60]
                        dump_path = os.path.join(dump_responses_dir, f"{event_name}_{safe}.txt")
                        with Path(dump_path).open("w", encoding="utf-8") as df:
                            df.write(raw)
                    break
                except Exception as e:
                    LOGGER.warning("[%s] Gemini failed (attempt %d): %s", event_name, retries + 1, e)
                    retries += 1
                    time.sleep(0.5)

        f.write("]\n")
    LOGGER.info("[%s] Wrote %d questions → %s", event_name, len(out), output_path)
    return out


def parse_args() -> argparse.Namespace:
    bugbo = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="Generate ID-only questions (1 MCQ + 1 FRQ per specimen) for Rocks, Water, Entomology in Bugbo."
    )
    ap.add_argument("--keys", default="HARDCODED", help="Path to gemini_keys.txt or HARDCODED")
    ap.add_argument("--model", default="gemini-2.0-flash", help="Gemini model")
    ap.add_argument("--max-keys", type=int, default=None)
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    ap.add_argument("--sleep-ms", type=int, default=0)
    ap.add_argument("--dump-responses-dir", default=None)
    ap.add_argument("--output-dir", default=str(bugbo), help="Directory for output JSON files")
    ap.add_argument("--rocks-txt", default=str(bugbo / "rocks.txt"))
    ap.add_argument("--water-json", default=str(bugbo / "water.json"))
    ap.add_argument("--nats-list", default=str(bugbo / "data" / "state" / "NATS" / "list.txt"))
    ap.add_argument(
        "--events",
        choices=["rocks", "water", "entomology", "all"],
        default="all",
        help="Which event(s) to generate",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    keys = load_keys(args.keys, args.max_keys)

    rocks_specimens = load_rocks_specimens(args.rocks_txt)
    water_common = load_water_common_names(args.water_json)
    ento_specimens = load_entomology_specimens(args.nats_list)

    LOGGER.info("Rocks: %d specimens", len(rocks_specimens))
    LOGGER.info("Water: %d specimens", len(water_common))
    LOGGER.info("Entomology: %d specimens", len(ento_specimens))

    if args.events in ("rocks", "all") and rocks_specimens:
        process_event(
            "rocks",
            rocks_specimens,
            build_rocks_id_prompt,
            normalize_rocks,
            args.model,
            keys,
            os.path.join(args.output_dir, "rocks_id_questions.json"),
            args.sleep_ms,
            args.dump_responses_dir,
        )
    if args.events in ("water", "all") and water_common:
        process_event(
            "water",
            water_common,
            build_water_id_prompt,
            normalize_water,
            args.model,
            keys,
            os.path.join(args.output_dir, "water_id_questions.json"),
            args.sleep_ms,
            args.dump_responses_dir,
        )
    if args.events in ("entomology", "all") and ento_specimens:
        process_event(
            "entomology",
            ento_specimens,
            build_entomology_id_prompt,
            normalize_entomology,
            args.model,
            keys,
            os.path.join(args.output_dir, "entomology_id_questions.json"),
            args.sleep_ms,
            args.dump_responses_dir,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
