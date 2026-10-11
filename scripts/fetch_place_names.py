"""Download the official list of Dutch place names (woonplaatsen) from CBS open data.

    uv run python scripts/fetch_place_names.py

The list is committed (app/privacy/data/woonplaatsen.txt); run this once a year when CBS
publishes a new table and update TABLE below.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx

TABLE = "86312NED"  # "Woonplaatsen in Nederland 2026"
URL = f"https://opendata.cbs.nl/ODataApi/odata/{TABLE}/Woonplaatsen"
TARGET = Path("app/privacy/data/woonplaatsen.txt")


def main() -> None:
    names: set[str] = set()
    url: str | None = URL
    with httpx.Client(timeout=60) as http:
        while url:
            page = http.get(url, params={"$format": "json"}).raise_for_status().json()
            names.update(row["Title"].strip() for row in page["value"])
            url = page.get("odata.nextLink")
    header = (
        f"# Dutch place names (woonplaatsen). Source: CBS open data, table {TABLE},\n"
        f"# licence CC BY 4.0. Fetched {datetime.now(UTC).date().isoformat()} by "
        "scripts/fetch_place_names.py.\n"
    )
    TARGET.write_text(header + "\n".join(sorted(names)) + "\n", encoding="utf-8")
    print(f"Saved {len(names)} place names to {TARGET}")


if __name__ == "__main__":
    main()
