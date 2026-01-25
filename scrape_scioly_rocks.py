import os
import re
import ssl
import sys
import time
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from PIL import Image, UnidentifiedImageError
except ImportError as exc:
    raise SystemExit("Pillow is required to save images as .webp files.") from exc

BASE_ROOT = "https://www.scioly.rocks"
OUTPUT_DIR = "rocks"
INPUT_FILE = "rocks.txt"
SSL_CONTEXT = ssl._create_unverified_context()


def to_pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name.strip())
    return "".join(p.capitalize() for p in parts if p)


def to_filename_base(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9'._-]+", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-") or "specimen"


def next_filename(outdir: str, base: str) -> str:
    base_path = os.path.join(outdir, f"{base}.webp")
    if not os.path.exists(base_path):
        return base_path
    index = 1
    while True:
        candidate = os.path.join(outdir, f"{base}({index}).webp")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def read_specimens(path: str) -> list[str]:
    specimens = []
    with open(path, "r") as f:
        for line in f:
            name = line.strip()
            if name:
                specimens.append(name)
    return specimens


def fetch_image(url: str) -> bytes | None:
    req = Request(url, headers={"User-Agent": "scioly-scraper"})
    try:
        with urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            return resp.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        print(f"warning: {url} -> HTTP {exc.code}")
        return None
    except URLError as exc:
        print(f"warning: {url} -> {exc.reason}")
        return None


def download_sequence(base_url: str, filename_base: str) -> int:
    count = 0
    index = 1
    while True:
        url = f"{base_url}/{index}.jpg"
        content = fetch_image(url)
        if content is None:
            if index == 1:
                print(f"no images at {base_url}")
            break
        path = next_filename(OUTPUT_DIR, filename_base)
        try:
            with Image.open(BytesIO(content)) as img:
                img.save(path, format="WEBP")
        except UnidentifiedImageError:
            print(f"warning: skipping non-image response at {url}")
            break
        count += 1
        index += 1
        time.sleep(0.1)
    return count


def main() -> int:
    start_from = None
    if len(sys.argv) > 1:
        if sys.argv[1] == "--start" and len(sys.argv) > 2:
            start_from = sys.argv[2].strip()
        elif sys.argv[1].startswith("--"):
            start_from = sys.argv[1][2:].strip()

    if not os.path.exists(INPUT_FILE):
        print(f"missing input file: {INPUT_FILE}")
        return 1

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    specimens = read_specimens(INPUT_FILE)
    if start_from:
        start_lower = start_from.lower()
        specimens = [s for s in specimens if s.lower() >= start_lower]

    for specimen in specimens:
        pascal = to_pascal(specimen)
        filename_base = to_filename_base(specimen)

        minerals_url = f"{BASE_ROOT}/{pascal}"
        print(f"{specimen} -> {minerals_url}")
        download_sequence(minerals_url, filename_base)

        rocks_url = f"{BASE_ROOT}/rocks/{pascal}"
        print(f"{specimen} -> {rocks_url}")
        download_sequence(rocks_url, filename_base)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
