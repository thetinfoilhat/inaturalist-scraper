#!/usr/bin/env python3
"""Generate specimen_questions rows from specimen JSONs using Gemini (google-genai)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from google import genai
    from google.genai import types
except Exception as exc:
    raise SystemExit("Please install: pip install google-genai") from exc


LOGGER = logging.getLogger("specimen-gen")


EVENT_SOURCES = [
    {
        "event": "Rocks and Minerals",
        "path": "data/rocks_and_minerals/rocks.json",
        "specimen_key": "specimen",
    },
    {
        "event": "Entomology",
        "path": "data/entomology/entomology.json",
        "specimen_key": "specimen",
    },
    {
        "event": "Water Quality - Freshwater",
        "path": "data/water_quality/water.json",
        "specimen_key": "common_name",
    },
]


NAMESPACE = uuid.UUID("f4a6fd4e-3f5e-4c2b-8a28-7d2b1b0c5a9b")


def _sorted_specimens(items: Sequence[Dict[str, Any]], specimen_key: str) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda x: str(x.get(specimen_key, "")).casefold())


def _make_id(*parts: str) -> str:
    key = "|".join(parts)
    return str(uuid.uuid5(NAMESPACE, key))


def _append_json_item(path: Path, item: Dict[str, Any]) -> None:
    payload = json.dumps(item, ensure_ascii=True)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[{payload}]\n", encoding="utf-8")
        return
    with path.open("r+b") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            f.write(f"[{payload}]\n".encode("utf-8"))
            return
        f.seek(0, os.SEEK_SET)
        start = f.read(2)
        if start == b"[]":
            f.seek(0, os.SEEK_SET)
            f.write(f"[{payload}]\n".encode("utf-8"))
            f.truncate()
            return
        f.seek(-2, os.SEEK_END)
        tail = f.read(2)
        if tail != b"]\n":
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"]":
                raise RuntimeError(f"Output file {path} is not a JSON array.")
            f.seek(-1, os.SEEK_END)
            f.truncate()
        else:
            f.seek(-2, os.SEEK_END)
            f.truncate()
        f.write(b",\n")
        f.write(payload.encode("utf-8"))
        f.write(b"]\n")


def _write_json_array(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("[\n")
        for i, item in enumerate(items):
            if i:
                f.write(",\n")
            f.write(json.dumps(item, ensure_ascii=False))
        f.write("\n]\n")


def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False))
            f.write("\n")


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip().lower())
    return safe or "specimen"


def _schema_spec() -> Dict[str, str]:
    return {
        "id": "uuid",
        "question": "string",
        "tournament": "string",
        "division": "string",
        "options": "array (empty for FRQ, 3-6 for MCQ)",
        "answers": "array (MCQ: [0-based index]; FRQ: [accepted strings])",
        "subtopics": "array of strings",
        "difficulty": "number 0..1",
        "event": "string",
        "pure_id": "boolean",
        "rm_type": "string or null",
        "specimen": "string",
        "statesNationals": "boolean",
    }

def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
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


def _rocks_prompt(specimen: str) -> str:
    subtopics = ["Igneous", "Metamorphic", "Sedimentary", "Mineral Properties", "Crystal Systems"]
    rules = (
        "Task: Create exactly 35 Science Olympiad–style MCQ questions, and create up to another 35 "
        "derivative FRQ questions for a single rock specimen for a station-based Rocks & Minerals C competition.\n"
        "Stimulus Requirement (MANDATORY): Every question must be phrased as if it refers to an image or "
        "physical specimen shown above the question. Use anchoring language such as: \n"
        "\"Based on this rock...\", \"In the specimen shown...\", \"The bands visible in this sample...\", "
        "\"The dark/red layers in this rock...\"\n"
        "DO NOT mention the rock’s name or ask for identification in non-pure questions.\n\n"
        "Hard No-Go Rules (Strict):\n"
        "- Do not ask for identification of the rock or minerals by name in non-pure questions.\n"
        "- Do not start each option with A), B), C) or 1) 2) 3). It should get straight to the text.\n"
        "- Do not repeat the same observable or inferred property more than once.\n"
        "- Do not require external maps, stratigraphic columns, or unlabeled diagrams.\n"
        "- Do not ask purely definitional textbook questions.\n\n"
        "Required Topic Distribution (Must Follow) for the 35 MCQ:\n"
        "- 8 Physical & optical properties\n"
        "- 7 Formation & geochemical process questions\n"
        "- 6 Earth history & environmental interpretation questions\n"
        "- 5 Economic/use questions\n"
        "- 4 High-difficulty synthesis questions (Eh–pH reasoning, facies comparison, metamorphic overprint, proxy reasoning)\n"
        "- 5 Paired comparison questions (e.g., compared to limestone / shale / quartzite...)\n\n"
        "Difficulty Calibration:\n"
        "0.1–0.2 basic observable inference; 0.2–0.5 applied reasoning; 0.5–0.8 synthesis; 0.8-1.0 nationals.\n"
        "At least 6 questions must be >= 0.7.\n\n"
        "Style Guidance: Questions should resemble station tasks. Favor 'what does this imply', 'why would this occur', "
        "and 'what property enables'. Each question must test a distinct concept.\n"
    )

    return (
        "You are an expert Science Olympiad question author for Rocks & Minerals C.\n"
        f"Specimen name (for pure ID questions only): {specimen}\n\n"
        "Generate JSON ONLY as an array of question objects.\n"
        "- Include EXACTLY 35 non-pure MCQ questions.\n"
        "- Include EXACTLY 35 non-pure FRQ questions that are direct derivatives of those MCQs (same concept).\n"
        "- Include EXACTLY 2 pure ID questions total: 1 MCQ and 1 FRQ.\n"
        "- The question text for BOTH pure ID questions MUST be exactly: \"Identify this rock or mineral specimen.\".\n"
        "- No other questions may have pure_id=true.\n"
        "- TOTAL output must be EXACTLY 72 questions.\n"
        "- For non-pure questions, do NOT mention the specimen name.\n"
        "- Every question must be anchored to a shown specimen image (use anchoring language).\n\n"
        f"Rules: {rules}\n\n"
        "Output JSON ONLY in this schema (fields must exist):\n"
        f"{json.dumps(_schema_spec(), ensure_ascii=False)}\n\n"
        "Additional requirements:\n"
        "- tournament: 'ID Event'\n"
        "- division: 'B/C'\n"
        "- event: 'Rocks and Minerals'\n"
        "- subtopics: you must selectselect 1-2 relevant values from this list only: "
        + ", ".join(subtopics)
        + "\n"
        "- pure_id: true only for the 2 identification questions\n"
        "- answers must be arrays.\n"
        "- For MCQ: options must be 4 choices, answer is [0-based index].\n"
        "- For FRQ: options is [], answer is [string]. Use proper capitalization.\n"
        "- snOnly=true only for states/nationals content (thin sections, Eh–pH, facies).\n"
        "- rm_type should be 'rock' for this specimen.\n"
        "- specimen field must equal the provided specimen name exactly.\n"
        "- statesNationals should be false unless a question is SN-only.\n"
        "- pure_id questions must use difficulty 0.5.\n"
        "- Do NOT mention the specimen name in any question text (including pure ID).\n"
        "If you are unsure, return an empty array []."
    )


def _entomology_prompt(specimen: str) -> str:
    subtopics = ["Behavior", "Classification", "Ecology", "Insect Anatomy", "Life Cycles"]
    rules = (
        "Task: Create exactly 20 MCQ questions and up to 20 FRQ questions for a single entomology specimen. "
        "For this run, scale to 75 total questions while preserving topic balance.\n"
        "Stimulus: Every question must explicitly reference the specimen shown using anchoring language.\n"
        "Do NOT mention the insect name unless the question explicitly asks for it.\n"
        "Do not repeat the same observable trait or inference twice.\n"
        "No dissections, destructive testing, maps, or unlabeled diagrams.\n"
        "No purely definitional textbook questions.\n"
        "Required Topic Distribution (for 35 non-pure MCQ):\n"
        "- Morphology & Anatomy: 8\n"
        "- Life History & Development: 7\n"
        "- Ecology & Behavior: 7\n"
        "- Economic / Human Interaction: 5\n"
        "- High-Difficulty Synthesis: 4\n"
        "- Paired Comparison: 4\n"
        "Difficulty calibration: 0.1–0.3 observable, 0.4–0.6 applied, 0.7–0.9 synthesis, 1.0 nationals.\n"
        "At least 6 questions must be >= 0.7.\n"
        "snOnly=true only for IPM, invasive species, conservation, climate change, urban/ag strategies.\n"
    )
    return (
        "You are an expert Science Olympiad Entomology question author.\n"
        f"Specimen name (for pure ID questions only): {specimen}\n\n"
        "Generate JSON ONLY as an array of question objects.\n"
        "- Include EXACTLY 35 non-pure MCQ questions.\n"
        "- Include EXACTLY 35 non-pure FRQ questions that are direct derivatives of those MCQs.\n"
        "- Include EXACTLY 2 pure ID questions total: 1 MCQ and 1 FRQ.\n"
        "- The question text for BOTH pure ID questions MUST be exactly: \"Identify this specimen.\".\n"
        "- No other questions may have pure_id=true.\n"
        "- TOTAL output must be EXACTLY 72 questions.\n"
        "- For non-pure questions, do NOT mention the specimen name.\n"
        "- Every question must be anchored to the specimen shown (use anchoring language).\n\n"
        f"Rules: {rules}\n\n"
        "Output JSON ONLY in this schema (fields must exist):\n"
        f"{json.dumps(_schema_spec(), ensure_ascii=False)}\n\n"
        "Additional requirements:\n"
        "- tournament: 'ID Event'\n"
        "- division: 'B/C'\n"
        "- subtopics: select 1-2 relevant values from this list only: "
        + ", ".join(subtopics)
        + "\n"
        "- answers must be arrays.\n"
        "- For MCQ: options must be 4 choices, answer is [0-based index].\n"
        "- For FRQ: options is [], answer is [string]. Use proper capitalization.\n"
        "- snOnly=true only for IPM/invasive/conservation/climate/urban/ag strategies.\n"
        "- pure_id questions must use difficulty 0.5.\n"
        "- Do NOT mention the specimen name in any question text (including pure ID).\n"
        "If you are unsure, return an empty array []."
    )


def _water_prompt(specimen: str) -> str:
    subtopics = [
        "Dissolved Oxygen",
        "Ecology",
        "Identification",
        "Nutrients",
        "Pollutants",
        "Pollution",
        "Testing",
        "pH",
    ]
    rules = (
        "Task: Create exactly 20 MCQ questions and 6–10 FRQ questions for a single water quality specimen. "
        "For this run, scale to 72 total questions while preserving topic balance.\n"
        "Stimulus: Every question must explicitly reference the specimen shown using anchoring language.\n"
        "Do NOT ask for identification in non-pure questions.\n"
        "Do not repeat the same observable trait or ecological inference twice.\n"
        "No graphs/tables/charts/chemical readouts or numeric test results.\n"
        "No physical testing (pH/DO/turbidity/BOD) questions.\n"
        "No purely definitional textbook questions.\n"
        "Required Topic Distribution (for 35 non-pure MCQ):\n"
        "- Specimen Morphology & Function: 8\n"
        "- Feeding Ecology & Trophic Role: 7\n"
        "- Water Quality Indicator Value: 8\n"
        "- Environmental & Habitat Interpretation: 6\n"
        "- High-Difficulty Synthesis: 3\n"
        "- Paired Comparison: 3\n"
        "Difficulty calibration: 0.1–0.3 observable, 0.4–0.6 applied, 0.7–0.9 synthesis, 1.0 nationals.\n"
        "At least 4 questions must be >= 0.7.\n"
        "snOnly=true only for invasive species impacts, watershed-scale management, "
        "long-term ecosystem effects, conservation/control strategies.\n"
    )
    return (
        "You are an expert Science Olympiad Water Quality question author.\n"
        f"Specimen name (for pure ID questions only): {specimen}\n\n"
        "Generate JSON ONLY as an array of question objects.\n"
        "- Include EXACTLY 35 non-pure MCQ questions.\n"
        "- Include EXACTLY 35 non-pure FRQ questions that are direct derivatives of those MCQs.\n"
        "- Include EXACTLY 2 pure ID questions total: 1 MCQ and 1 FRQ.\n"
        "- The question text for BOTH pure ID questions MUST be exactly: \"Identify this specimen.\".\n"
        "- No other questions may have pure_id=true.\n"
        "- TOTAL output must be EXACTLY 72 questions.\n"
        "- For non-pure questions, do NOT mention the specimen name.\n"
        "- Every question must be anchored to the specimen shown (use anchoring language).\n\n"
        f"Rules: {rules}\n\n"
        "Output JSON ONLY in this schema (fields must exist):\n"
        f"{json.dumps(_schema_spec(), ensure_ascii=False)}\n\n"
        "Additional requirements:\n"
        "- tournament: 'ID Event'\n"
        "- division: 'B/C'\n"
        "- subtopics: select 1-2 relevant values from this list only: "
        + ", ".join(subtopics)
        + "\n"
        "- answers must be arrays.\n"
        "- For MCQ: options must be 4 choices, answer is [0-based index].\n"
        "- For FRQ: options is [], answer is [string]. Use proper capitalization.\n"
        "- snOnly=true only for invasive/watershed/long-term/conservation/control strategies.\n"
        "- pure_id questions must use difficulty 0.5.\n"
        "- Do NOT mention the specimen name in any question text (including pure ID).\n"
        "If you are unsure, return an empty array []."
    )


def _generic_prompt(event: str, specimen: str) -> str:
    subtopics = ["Identification", "Characteristics", "Ecology", "Behavior", "Taxonomy"]

    return (
        f"You are writing station-based Science Olympiad {event} questions.\n"
        f"Specimen name (for pure ID questions only): {specimen}\n\n"
        "Generate JSON ONLY as an array of question objects.\n"
        "- Include EXACTLY 35 non-pure MCQ questions.\n"
        "- Include EXACTLY 35 non-pure FRQ questions that are direct derivatives of those MCQs.\n"
        "- Include EXACTLY 2 pure ID questions total: 1 MCQ and 1 FRQ.\n"
        "- The question text for BOTH pure ID questions MUST be exactly: \"Identify this specimen.\".\n"
        "- No other questions may have pure_id=true.\n"
        "- TOTAL output must be EXACTLY 72 questions.\n"
        "- For non-pure questions, do NOT mention the specimen name.\n"
        "- Every question must be anchored to a shown specimen image (use anchoring language).\n\n"
        "Output JSON ONLY in this schema (fields must exist):\n"
        f"{json.dumps(_schema_spec(), ensure_ascii=False)}\n\n"
        "Additional requirements:\n"
        "- tournament: 'ID Event'\n"
        "- division: 'B/C'\n"
        "- subtopics: select 1-2 relevant values from this list only: "
        + ", ".join(subtopics)
        + "\n"
        "- answers must be arrays.\n"
        "- For MCQ: options must be 4 choices, answer is [0-based index].\n"
        "- For FRQ: options is [], answer is [string]. Use proper capitalization.\n"
        "- snOnly=true only for states/nationals content (thin sections, Eh–pH, facies).\n"
        "- pure_id questions must use difficulty 0.5.\n"
        "- Do NOT mention the specimen name in the question text for any non-pure question.\n"
        "If you are unsure, return an empty array []."
    )


def _call_gemini(client: genai.Client, model: str, prompt: str) -> str:
    LOGGER.debug("Gemini request | model=%s | prompt_chars=%d", model, len(prompt))
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(
            temperature=0.6,
            top_p=0.9,
            max_output_tokens=65536,
            response_mime_type="application/json",
        ),
    )
    if hasattr(resp, "text") and resp.text:
        text = resp.text.strip()
        LOGGER.debug("Gemini response | chars=%d", len(text))
        return text
    return ""


def _call_gemini_with_timeout(client: genai.Client, model: str, prompt: str, timeout_s: int) -> str:
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_call_gemini, client, model, prompt)
        return future.result(timeout=timeout_s)


def _parse_json(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    raw = text.strip()
    if raw.startswith("```"):
        parts = raw.split("```", 2)
        raw = parts[1] if len(parts) > 1 else raw
    def _sanitize(s: str) -> str:
        # Remove control characters and escape stray backslashes.
        s = re.sub(r"[\x00-\x1F]", " ", s)
        return re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", s)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\[(?:.|\n)*\]", raw)
        if not m:
            return []
        snippet = m.group(0)
        try:
            return json.loads(snippet)
        except Exception:
            return json.loads(_sanitize(snippet))


def _repair_json(client: genai.Client, model: str, raw: str) -> str:
    prompt = (
        "You are a strict JSON fixer. Given malformed JSON, return ONLY a valid JSON array. "
        "Do not add commentary or code fences. Preserve the original content as much as possible.\n\n"
        "Malformed JSON:\n"
        f"{raw}"
    )
    try:
        resp = client.models.generate_content(
            model=model,
            contents=[types.Part.from_text(text=prompt)],
            config=types.GenerateContentConfig(
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=65536,
                response_mime_type="application/json",
            ),
        )
        if hasattr(resp, "text") and resp.text:
            return resp.text.strip()
    except Exception:
        return ""
    return ""


def _normalize_items(specimen: str, event: str, rm_type: Optional[str], states_nationals: bool, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(items or []):
        question = str(q.get("question", "")).strip()
        if not question:
            continue
        options = q.get("options", [])
        answers = q.get("answers") or q.get("answer")
        if isinstance(answers, list):
            answers_out = answers
        else:
            answers_out = [answers] if answers is not None else []
        try:
            difficulty = float(q.get("difficulty", 0.5))
        except Exception:
            difficulty = 0.5
        difficulty = max(0.0, min(1.0, difficulty))
        sn_only = bool(q.get("snOnly", False))
        row = {
            "id": _make_id(event, specimen, f"q-{i}"),
            "question": question,
            "tournament": q.get("tournament", "ID Event"),
            "division": q.get("division", "B/C"),
            "options": options if isinstance(options, list) else [],
            "answers": answers_out,
            "subtopics": q.get("subtopics", []),
            "difficulty": difficulty,
            "event": q.get("event", event),
            "pure_id": bool(q.get("pure_id", False)),
            "rm_type": rm_type,
            "specimen": specimen,
            "statesNationals": bool(states_nationals or sn_only),
        }
        out.append(row)
    return out


def _count_pure_id_issues(items: List[Dict[str, Any]], expected_text: str) -> int:
    pure = [q for q in items if q.get("pure_id") is True]
    bad = [q for q in pure if str(q.get("question", "")).strip() != expected_text]
    if len(pure) != 2:
        return 1
    if bad:
        return 1
    # ensure one MCQ and one FRQ
    mcq = [q for q in pure if isinstance(q.get("options"), list) and len(q.get("options")) > 0]
    frq = [q for q in pure if not (isinstance(q.get("options"), list) and len(q.get("options")) > 0)]
    if len(mcq) != 1 or len(frq) != 1:
        return 1
    return 0


def generate_questions(
    *,
    include_events: Sequence[str],
    resume_specimen: Optional[str],
    model: str,
    output_path: Path,
    sleep_ms: int,
    workers: int,
) -> int:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("Missing GOOGLE_API_KEY or GEMINI_API_KEY in environment.")

    tasks: List[Dict[str, Any]] = []
    for event_idx, src in enumerate(EVENT_SOURCES):
        event = src["event"]
        if include_events and event not in include_events:
            continue
        path = (Path(__file__).resolve().parents[1] / src["path"]).resolve()
        specimen_key = src["specimen_key"]
        items = json.loads(path.read_text())
        items = _sorted_specimens(items, specimen_key)

        resume_seen = resume_specimen is None
        resume_key = resume_specimen.casefold() if resume_specimen else None

        LOGGER.info("Event: %s | specimens=%d", event, len(items))
        for specimen_idx, item in enumerate(items, start=1):
            specimen = str(item[specimen_key])
            if not resume_seen:
                if specimen.casefold() < resume_key:
                    continue
                resume_seen = True
            tasks.append(
                {
                    "event_idx": event_idx,
                    "specimen_idx": specimen_idx,
                    "event": event,
                    "specimen": specimen,
                    "rm_type": item.get("rm_type"),
                    "states_nationals": bool(item.get("statesNationals", False)),
                }
            )

    if not tasks:
        LOGGER.warning("No specimens to process.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    def _worker(task: Dict[str, Any]) -> Dict[str, Any]:
        event = task["event"]
        specimen = task["specimen"]
        rm_type = task["rm_type"]
        states_nationals = task["states_nationals"]
        event_idx = task["event_idx"]
        specimen_idx = task["specimen_idx"]

        LOGGER.info("Processing specimen %d/%d: %s", specimen_idx, len(tasks), specimen)
        if event == "Rocks and Minerals":
            prompt = _rocks_prompt(specimen)
            if rm_type is None:
                rm_type = "rock"
            expected_pure_text = "Identify this rock or mineral specimen."
            expected_count = 72
        elif event == "Entomology":
            prompt = _entomology_prompt(specimen)
            expected_pure_text = "Identify this specimen."
            expected_count = 72
        elif event == "Water Quality - Freshwater":
            prompt = _water_prompt(specimen)
            expected_pure_text = "Identify this specimen."
            expected_count = 72
        else:
            prompt = _generic_prompt(event, specimen)
            expected_pure_text = "Identify this specimen."
            expected_count = 72

        client = genai.Client(api_key=api_key)
        items_out: List[Dict[str, Any]] = []
        attempts = 0
        while attempts < 3:
            attempts += 1
            try:
                raw = _call_gemini_with_timeout(client, model, prompt, timeout_s=300)
            except Exception as exc:
                LOGGER.warning("Gemini timeout or error; retrying specimen | specimen=%s | attempt=%d | err=%s", specimen, attempts, exc)
                continue
            LOGGER.info("Gemini raw chars: %d | specimen=%s", len(raw), specimen)
            items_out = _parse_json(raw)
            if not items_out and raw:
                LOGGER.warning("Primary JSON parse failed; attempting repair | specimen=%s", specimen)
                repaired = _repair_json(client, model, raw)
                if repaired:
                    items_out = _parse_json(repaired)
                    LOGGER.info("Repair parse items: %d | specimen=%s", len(items_out), specimen)
            LOGGER.info("Parsed items: %d | specimen=%s", len(items_out), specimen)
            if len(items_out) != expected_count:
                LOGGER.warning(
                    "Unexpected count (%d), retrying | specimen=%s | attempt=%d",
                    len(items_out),
                    specimen,
                    attempts,
                )
                continue
            if _count_pure_id_issues(items_out, expected_pure_text):
                LOGGER.warning("Pure ID validation failed, retrying | specimen=%s | attempt=%d", specimen, attempts)
                continue
            break
        normalized = _normalize_items(specimen, event, rm_type, states_nationals, items_out)
        LOGGER.info("Normalized items: %d | specimen=%s", len(normalized), specimen)

        if sleep_ms:
            time.sleep(max(0, sleep_ms) / 1000.0)

        return {
            "order": (event_idx, specimen_idx),
            "rows": normalized,
        }

    count = 0
    if output_path.exists():
        output_path.unlink()

    def _append_rows(rows: List[Dict[str, Any]]) -> int:
        nonlocal count
        with output_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False))
                f.write("\n")
        count += len(rows)
        return len(rows)

    if workers <= 1:
        for task in tasks:
            res = _worker(task)
            _append_rows(res["rows"])
            LOGGER.info("Total written so far: %d", count)
    else:
        LOGGER.info("Running with %d workers", workers)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_worker, task) for task in tasks]
            for fut in as_completed(futures):
                res = fut.result()
                _append_rows(res["rows"])
                LOGGER.info("Total written so far: %d", count)

    LOGGER.info("Wrote streaming JSONL output: %s | rows=%d", output_path, count)
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate specimen_questions JSON using Gemini.")
    ap.add_argument("--output", default=None, help="Output JSONL path (single event only)")
    ap.add_argument("--rocks", action="store_true", help="Generate only Rocks and Minerals")
    ap.add_argument("--entomology", action="store_true", help="Generate only Entomology")
    ap.add_argument("--water", action="store_true", help="Generate only Water Quality - Freshwater")
    ap.add_argument("--resume", default=None, help="Resume from specimen name (inclusive)")
    ap.add_argument("--model", default="gemini-3-flash-preview")
    ap.add_argument("--sleep-ms", type=int, default=0)
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--workers", type=int, default=1, help="Number of concurrent workers")
    args = ap.parse_args()

    _setup_logging(args.log_level)
    if args.workers > 8:
        LOGGER.warning("High worker count (%d). You may hit rate limits; 4-8 is usually safer.", args.workers)
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    include_events: List[str] = []
    if args.rocks:
        include_events.append("Rocks and Minerals")
    if args.entomology:
        include_events.append("Entomology")
    if args.water:
        include_events.append("Water Quality - Freshwater")

    output_map = {
        "Rocks and Minerals": Path("data/rocks_questions.jsonl"),
        "Entomology": Path("data/entomology_questions.jsonl"),
        "Water Quality - Freshwater": Path("data/water_questions.jsonl"),
    }

    if not include_events:
        include_events = list(output_map.keys())

    total = 0
    if args.output and len(include_events) == 1:
        out_path = Path(args.output)
        total = generate_questions(
            include_events=include_events,
            resume_specimen=args.resume,
            model=args.model,
            output_path=out_path,
            sleep_ms=args.sleep_ms,
            workers=args.workers,
        )
        print(f"wrote {total} rows to {out_path}")
    else:
        if args.output and len(include_events) > 1:
            LOGGER.warning("Ignoring --output for multi-event run; using per-event defaults.")
        for event in include_events:
            out_path = output_map[event]
            total += generate_questions(
                include_events=[event],
                resume_specimen=args.resume,
                model=args.model,
                output_path=out_path,
                sleep_ms=args.sleep_ms,
                workers=args.workers,
            )
            print(f"wrote {total} rows so far (latest: {out_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
