import argparse
import asyncio
import logging
import os
import re
from io import BytesIO
from typing import Iterable, List

import aiohttp
try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Pillow is required to save images as .webp files.") from exc

logger = logging.getLogger("inat_scrape")

TAXON_ID_URL = "https://api.inaturalist.org/v1/taxa?q={taxon}&per_page=50"
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
        return (0, tuple(), tuple())

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
                data = await resp.read()
                with Image.open(BytesIO(data)) as img:
                    path = next_filename(outdir, base, "webp")
                    img.save(path, format="WEBP")
                saved += 1
        except aiohttp.ClientError:
            logger.warning("download error: %s", url)
            continue
    return saved


async def run(args: argparse.Namespace) -> None:
    taxa = list(iter_nats_list(args.nats_list))
    if args.limit_taxa:
        taxa = taxa[: args.limit_taxa]

    os.makedirs(args.outdir, exist_ok=True)

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for taxon in taxa:
            logger.info("taxon: %s", taxon)
            urls = await get_urls_for_taxon(session, taxon, args.per_taxon)
            if len(urls) > args.per_taxon:
                urls = urls[: args.per_taxon]
            logger.info("urls returned: %s", len(urls))
            if not urls:
                print(f"skip: no photos for {taxon}")
                continue

            saved = await download_images(session, urls, args.outdir, taxon)
            print(f"{taxon}: saved {saved} image(s)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
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
