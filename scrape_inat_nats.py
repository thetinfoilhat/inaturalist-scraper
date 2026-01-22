import argparse
import asyncio
import os
import re
from typing import Iterable, List, Set

import aiohttp

import inat

def slugify(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip().lower())
    return cleaned.strip("_") or "specimen"


def iter_nats_list(path: str) -> Iterable[str]:
    with open(path, "r") as f:
        for line in f:
            name = line.strip()
            if name:
                yield name


def next_filename(outdir: str, base: str, ext: str) -> str:
    base_path = os.path.join(outdir, f"{base}.{ext}")
    if not os.path.exists(base_path):
        return base_path
    index = 1
    while True:
        candidate = os.path.join(outdir, f"{base}({index}).{ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json()


async def get_observations(
    session: aiohttp.ClientSession,
    taxon_id: int,
    count: int,
    last_id: str,
) -> List[dict]:
    url = inat.OBSERVATIONS_URL.format(
        taxon_id=taxon_id,
        count=count,
        last_id=last_id,
    )
    data = await fetch_json(session, url)
    return data.get("results", [])


async def collect_photo_urls(
    session: aiohttp.ClientSession,
    taxon_id: int,
    target_count: int,
) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()
    last_id = ""

    while len(urls) < target_count:
        per_page = min(200, max(20, target_count - len(urls)))
        observations = await get_observations(session, taxon_id, per_page, last_id)
        if not observations and last_id:
            observations = await get_observations(session, taxon_id, per_page, "")

        if not observations:
            break

        for observation in observations:
            for photo in observation.get("photos", []):
                raw_url = photo.get("url", "")
                ext = raw_url.split(".")[-1].split("?")[0].lower() or "jpg"
                url = inat.IMAGE_URL.format(id=photo["id"], ext=ext)
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                if len(urls) >= target_count:
                    break
            if len(urls) >= target_count:
                break

        last_id = str(observations[-1].get("id", ""))
        if not last_id:
            break

    return urls


async def download_images(
    session: aiohttp.ClientSession,
    urls: Iterable[str],
    outdir: str,
    specimen_name: str,
) -> int:
    saved = 0
    base = slugify(specimen_name)
    for url in urls:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    continue
                ext = url.split(".")[-1].split("?")[0].lower() or "bin"
                path = next_filename(outdir, base, ext)
                with open(path, "wb") as f:
                    while True:
                        block = await resp.content.read(1024 * 8)
                        if not block:
                            break
                        f.write(block)
                saved += 1
        except aiohttp.ClientError:
            continue
    return saved


async def run(args: argparse.Namespace) -> None:
    taxa = list(iter_nats_list(args.nats_list))
    if args.limit_taxa:
        taxa = taxa[: args.limit_taxa]

    os.makedirs(args.outdir, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        for taxon in taxa:
            taxon_id = await inat.get_taxon_id(taxon, session)
            if not taxon_id:
                print(f"skip: no taxon id for {taxon}")
                continue

            urls = await collect_photo_urls(session, taxon_id, args.per_taxon)
            if not urls:
                print(f"skip: no photos for {taxon}")
                continue

            saved = await download_images(session, urls, args.outdir, taxon)
            print(f"{taxon}: saved {saved} image(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download iNaturalist images for taxa in NATS list.",
    )
    parser.add_argument(
        "--nats-list",
        default=os.path.join("data", "state", "NATS", "list.txt"),
        help="Path to NATS list.txt file.",
    )
    parser.add_argument(
        "--outdir",
        default="inat_images",
        help="Output directory for images.",
    )
    parser.add_argument(
        "--per-taxon",
        type=int,
        default=10,
        help="Number of images to download per taxon.",
    )
    parser.add_argument(
        "--limit-taxa",
        type=int,
        default=0,
        help="Limit number of taxa for a smaller run (0 = no limit).",
    )
    args = parser.parse_args()

    if args.per_taxon <= 0:
        raise SystemExit("--per-taxon must be > 0")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
