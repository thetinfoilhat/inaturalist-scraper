import argparse
import asyncio
import json
import logging
import os
import re
from typing import Iterable, List, TypedDict

import aiohttp

logger = logging.getLogger("inat_scrape")

TAXON_ID_URL = "https://api.inaturalist.org/v1/taxa?q={taxon}&rank=genus&per_page=50"
OBSERVATIONS_URL = (
    "https://api.inaturalist.org/v1/observations?photos=true&photo_licensed=true"
    + "&place_id=46,10,50,22,14,16,15,52,34,40,9,13,44,3,25,12,18,38,24,28,36,27,32,29,35,20,31,33,26,45,37,19,17,41,47,2,8,49,48,51,42,4,39,5,7,30,43,23,21"
    + "&taxon_id={taxon_id}&quality_grade=research&per_page={count}"
    + "&order_by=id&order=asc&id_above={last_id}"
)
IMAGE_URL = "https://inaturalist-open-data.s3.amazonaws.com/photos/{id}/medium.{ext}"


def slugify(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip().lower())
    return cleaned.strip("_") or "specimen"


class WaterEntry(TypedDict):
    common_name: str
    scientific_genera: List[str]


def load_water_entries(path: str) -> List[WaterEntry]:
    with open(path, "r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("water.json must be a list of entries")
    entries: List[WaterEntry] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("water.json entries must be objects")
        common = item.get("common_name")
        genera = item.get("scientific_genera")
        if not isinstance(common, str) or not isinstance(genera, list):
            raise ValueError(
                "water.json entries must have common_name and scientific_genera"
            )
        entries.append(
            {
                "common_name": common,
                "scientific_genera": [str(g) for g in genera if str(g).strip()],
            }
        )
    return entries


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


async def get_urls_for_taxon(
    session: aiohttp.ClientSession,
    taxon: str,
    count: int,
    retries: int = 2,
) -> List[str]:
    for attempt in range(retries + 1):
        try:
            _, urls, _ = await get_urls(session, taxon, 0, count)
            return list(urls)
        except aiohttp.ClientError as exc:
            logger.warning("request failed (%s): %s", taxon, exc)
            if attempt >= retries:
                raise
            await asyncio.sleep(1.0 + attempt)


async def get_taxon_id(taxon: str, session: aiohttp.ClientSession) -> int | None:
    async with session.get(TAXON_ID_URL.format(taxon=taxon)) as resp:
        data = await resp.json()
    results = data.get("results", [])
    if not results:
        return None
    exact = [
        item
        for item in results
        if item.get("rank") == "genus"
        and str(item.get("name", "")).lower() == taxon.lower()
    ]
    if exact:
        if len(exact) > 1:
            logger.info(
                "multiple genus matches for %s: %s",
                taxon,
                ", ".join(str(item.get("id")) for item in exact),
            )
        exact.sort(key=lambda x: x.get("observations_count", 0), reverse=True)
        return exact[0]["id"]
    results = sorted(results, key=lambda x: x["rank_level"], reverse=True)
    return results[0]["id"]


async def get_urls(
    session: aiohttp.ClientSession,
    item: str,
    index: int,
    count: int,
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    taxon_id = await get_taxon_id(item, session)
    if not taxon_id:
        logger.info("no taxon id found for %s", item)

    urls = []
    ids = []
    async with session.get(
        OBSERVATIONS_URL.format(taxon_id=taxon_id, count=count, last_id=index)
    ) as resp:
        observations = (await resp.json())["results"]

    if not observations:
        async with session.get(
            OBSERVATIONS_URL.format(taxon_id=taxon_id, count=count, last_id="")
        ) as resp:
            observations = (await resp.json())["results"]

    if not observations:
        return (0, tuple(), tuple())

    logger.info(
        "observation ids: %s", ",".join([str(o["id"]) for o in observations])
    )
    for observation in observations:
        logger.info("observation at: %s", observation.get("observed_on"))
        for photo in observation["photos"]:
            urls.append(
                IMAGE_URL.format(id=photo["id"], ext=photo["url"].split(".")[-1])
            )
            ids.append(observation["id"])
    return (observations[-1]["id"], tuple(urls), tuple(ids))


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
                    logger.warning("download failed (%s): %s", url, resp.status)
                    continue
                ext = url.split(".")[-1].split("?")[0].lower() or "bin"
                if ext == "jpeg":
                    ext = "jpg"
                path = next_filename(outdir, base, ext)
                with open(path, "wb") as f:
                    while True:
                        block = await resp.content.read(1024 * 8)
                        if not block:
                            break
                        f.write(block)
                saved += 1
        except aiohttp.ClientError:
            logger.warning("download error: %s", url)
            continue
    return saved


async def run(args: argparse.Namespace) -> None:
    entries = load_water_entries(args.water_json)
    if args.limit_taxa:
        entries = entries[: args.limit_taxa]

    os.makedirs(args.outdir, exist_ok=True)

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for entry in entries:
            common_name = entry["common_name"]
            genera = entry["scientific_genera"]
            if not genera:
                print(f"skip: no genera for {common_name}")
                continue
            for genus in genera:
                logger.info("taxon: %s (%s)", genus, common_name)
                urls = await get_urls_for_taxon(session, genus, args.per_taxon)
                if len(urls) > args.per_taxon:
                    urls = urls[: args.per_taxon]
                logger.info("urls returned: %s", len(urls))
                if not urls:
                    print(f"skip: no photos for {genus} ({common_name})")
                    continue

                saved = await download_images(session, urls, args.outdir, common_name)
                print(f"{common_name} ({genus}): saved {saved} image(s)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Download iNaturalist images for entries in water.json.",
    )
    parser.add_argument(
        "--water-json",
        default="water.json",
        help="Path to water.json file.",
    )
    parser.add_argument(
        "--outdir",
        default="water",
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
