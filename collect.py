"""Fetch DATEX weather + traffic once, parse and append to daily CSV files (run by GitHub Actions)."""
import gzip
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from parse_datex import parse_sites, parse_traffic, parse_weather

BASE = "https://tarktee.transpordiamet.ee/api/v1/datex"
FEEDS = {"weather": ("/weatherData", parse_weather), "traffic": ("/trafficData", parse_traffic)}
OUT = Path("data")
RADIUS_KM = 1000.0


def fetch_gz(endpoint, key):
    r = requests.get(BASE + endpoint, headers={"X-DATEX-API-KEY": key}, timeout=120)
    r.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(suffix=".xml.gz", delete=False)
    with gzip.open(tmp.name, "wb") as f:
        f.write(r.content)
    return tmp.name


def append_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        df = pd.concat([pd.read_csv(path), df], ignore_index=True)
    df.drop_duplicates(["site_id", "time"]).sort_values(["site_id", "time"]).to_csv(path, index=False)


def main():
    key = os.environ["DATEX_API_KEY"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    OUT.mkdir(exist_ok=True)

    sites_path = OUT / "sites.csv"
    if not sites_path.exists() or datetime.now(timezone.utc).hour == 0:
        sites = parse_sites(fetch_gz("/measurementSites", key))
        sites[sites.dist_tartu_km <= RADIUS_KM].to_csv(sites_path, index=False)
    keep = set(pd.read_csv(sites_path).site_id)

    for feed, (endpoint, parser) in FEEDS.items():
        df = pd.DataFrame(parser(fetch_gz(endpoint, key)))
        df = df[df.site_id.isin(keep)]
        df["time"] = pd.to_datetime(df["time"], utc=True).astype(str)
        append_csv(df, OUT / feed / f"{today}.csv")
        print(f"{feed}: {len(df)} rows")


if __name__ == "__main__":
    main()
