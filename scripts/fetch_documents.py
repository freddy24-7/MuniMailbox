"""Download the public pages listed in data/sources.toml into data/documents/.

The pages are not part of this repository. Run this once, and again when you want fresh
copies; every document records the date it was fetched.

    uv run python scripts/fetch_documents.py
"""

import argparse
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.rag.chunking import Document
from app.rag.html import extract
from app.rag.ingestion import save_document

USER_AGENT = (
    "MuniMailbox-demo/0.1 (non-commercial student project; github.com/freddy24-7/MuniMailbox)"
)
MIN_CHARS = 200  # less than this and the page is navigation, not content


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--sources", type=Path, default=Path("data/sources.toml"))
    parser.add_argument("--out", type=Path, default=Path("data/documents"))
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args()

    sources = tomllib.loads(args.sources.read_text(encoding="utf-8"))["source"]
    today = datetime.now(UTC).date().isoformat()
    saved = 0
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as http:
        for group in sources:
            for url in group["urls"]:
                try:
                    response = http.get(url)
                    response.raise_for_status()
                except httpx.HTTPError as error:
                    print(f"FAILED  {url}: {error}")
                    continue
                title, text = extract(response.text)
                if len(text) < MIN_CHARS:
                    print(f"SKIPPED {url}: only {len(text)} characters of content")
                    continue
                save_document(Document(url, title, group["topic"], today, text), args.out)
                saved += 1
                print(f"ok      {len(text):>6} chars  {title}")
                time.sleep(args.delay)  # be a polite guest
    print(f"Saved {saved} documents to {args.out}")


if __name__ == "__main__":
    main()
