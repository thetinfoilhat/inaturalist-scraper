import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_ROOT = "https://www.scioly.rocks"
INPUT_FILE = "rocks.txt"
SSL_CONTEXT = ssl._create_unverified_context()


def to_pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name.strip())
    return "".join(p.capitalize() for p in parts if p)


def read_specimens(path: str) -> list[str]:
    specimens = []
    with open(path, "r") as f:
        for line in f:
            name = line.strip()
            if name:
                specimens.append(name)
    return specimens


def url_exists(url: str) -> bool:
    req = Request(url, headers={"User-Agent": "scioly-validator"}, method="HEAD")
    try:
        with urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            print(f"ok: {url}")
            return 200 <= resp.status < 300
    except HTTPError as exc:
        if exc.code == 404:
            print(f"missing: {url}")
            return False
        print(f"error {exc.code}: {url}")
        return False
    except URLError:
        print(f"error: {url}")
        return False


def main() -> int:
    try:
        specimens = read_specimens(INPUT_FILE)
    except FileNotFoundError:
        print(f"missing input file: {INPUT_FILE}")
        return 1

    missing = []
    for specimen in specimens:
        print(f"checking: {specimen}")
        pascal = to_pascal(specimen)
        minerals_url = f"{BASE_ROOT}/{pascal}/1.jpg"
        rocks_url = f"{BASE_ROOT}/rocks/{pascal}/1.jpg"

        has_mineral = url_exists(minerals_url)
        has_rock = url_exists(rocks_url)

        if not (has_mineral or has_rock):
            missing.append(specimen)

    if missing:
        print("missing specimens:")
        for specimen in missing:
            print(specimen)
    else:
        print("all specimens found in scioly.rocks")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
