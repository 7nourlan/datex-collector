"""Parse raw DATEX II snapshots into tidy CSV tables (sites, weather, traffic)."""
import argparse
import gzip
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

NS = "{http://datex2.eu/schema/2/2_0}"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
RAW = Path("data/raw_datex")
OUT = Path("data")
TARTU = (58.3780, 26.7290)

WEATHER_FIELDS = {
    "air_temp_c": "TemperatureInformation/temperature/airTemperature/temperature",
    "dew_point_c": "TemperatureInformation/temperature/dewPointTemperature/temperature",
    "road_temp_c": "RoadSurfaceConditionInformation/roadSurfaceConditionMeasurements/roadSurfaceTemperature/temperature",
    "humidity_pct": "HumidityInformation/humidity/relativeHumidity/percentage",
    "precip_mm_h": "PrecipitationInformation/precipitationDetail/precipitationIntensity/millimetresPerHourIntensity",
    "precip_type": "PrecipitationInformation/precipitationDetail/precipitationType",
    "visibility_m": "VisibilityInformation/visibility/minimumVisibilityDistance/integerMetreDistance",
    "wind_speed": "WindInformation/wind/windSpeed/speed",
    "wind_gust": "WindInformation/wind/maximumWindSpeed/speed",
    "wind_dir_deg": "WindInformation/wind/windDirectionBearing/directionBearing",
}
TEXT_FIELDS = {"precip_type"}


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def read_xml(path):
    with gzip.open(path, "rb") as f:
        return ET.parse(f).getroot()


def leaves(elem, prefix):
    kids = list(elem)
    if not kids:
        yield prefix, elem.text
    for k in kids:
        yield from leaves(k, f"{prefix}/{k.tag.replace(NS, '')}")


def parse_sites(path):
    rows = []
    for table in read_xml(path).iter(NS + "measurementSiteTable"):
        kind = "weather" if table.get("id") == "WEATHER_STATION_SITES" else "traffic"
        for rec in table.iter(NS + "measurementSiteRecord"):
            lat, lon = rec.find(f".//{NS}latitude"), rec.find(f".//{NS}longitude")
            name = rec.find(f".//{NS}value")
            if lat is None or lon is None:
                continue
            lat, lon = float(lat.text), float(lon.text)
            rows.append({
                "site_id": rec.get("id"),
                "kind": kind,
                "name": name.text if name is not None else None,
                "lat": lat,
                "lon": lon,
                "dist_tartu_km": round(haversine_km(*TARTU, lat, lon), 2),
            })
    return pd.DataFrame(rows)


def site_measurements(root):
    for sm in root.iter(NS + "siteMeasurements"):
        ref = sm.find(NS + "measurementSiteReference").get("id")
        time = sm.find(NS + "measurementTimeDefault").text
        yield ref, time, sm


def parse_weather(path):
    rows = []
    for ref, time, sm in site_measurements(read_xml(path)):
        values = {}
        conditions = []
        for b in sm.iter(NS + "basicData"):
            for p, text in leaves(b, b.get(XSI_TYPE)):
                if p.endswith("weatherRelatedRoadConditionType"):
                    conditions.append(text)
                elif p.endswith("noPrecipitation") and text == "true":
                    values["precip_mm_h"] = 0.0
                else:
                    values[p] = text
        row = {"site_id": ref, "time": time}
        for col, p in WEATHER_FIELDS.items():
            v = values.get(p, values.get(col))
            row[col] = v if col in TEXT_FIELDS or v is None else float(v)
        row["road_condition"] = "|".join(sorted(set(conditions))) or None
        rows.append(row)
    return rows


def parse_traffic(path):
    rows = []
    for ref, time, sm in site_measurements(read_xml(path)):
        row = {"site_id": ref, "time": time, "speed_kmh": None, "flow_veh_h": None}
        for b in sm.iter(NS + "basicData"):
            kind = b.get(XSI_TYPE)
            if kind == "TrafficSpeed":
                row["speed_kmh"] = float(b.find(f".//{NS}speed").text)
            elif kind == "TrafficFlow":
                row["flow_veh_h"] = float(b.find(f".//{NS}vehicleFlowRate").text)
        rows.append(row)
    return rows


def collect(feed, parser):
    rows = []
    for path in sorted((RAW / feed).rglob("*.xml.gz")):
        try:
            rows.extend(parser(path))
        except ET.ParseError as e:
            print(f"skip {path}: {e}")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.drop_duplicates(["site_id", "time"]).sort_values(["site_id", "time"])


def nearest_weather(traffic_sites, weather_sites):
    out = []
    for _, t in traffic_sites.iterrows():
        d = weather_sites.apply(lambda w: haversine_km(t.lat, t.lon, w.lat, w.lon), axis=1)
        i = d.idxmin()
        out.append({
            "traffic_site_id": t.site_id,
            "traffic_name": t["name"],
            "weather_site_id": weather_sites.at[i, "site_id"],
            "weather_name": weather_sites.at[i, "name"],
            "distance_km": round(d[i], 2),
        })
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=float, default=40.0, help="km around Tartu (0 = all of Estonia)")
    args = ap.parse_args()

    site_files = sorted((RAW / "sites").rglob("*.xml.gz"))
    if not site_files:
        raise SystemExit("no sites file: run  python datex_collect.py --once --feeds sites")
    sites = parse_sites(site_files[-1])
    if args.radius:
        sites = sites[sites.dist_tartu_km <= args.radius]
    keep = set(sites.site_id)

    weather = collect("weather", parse_weather)
    traffic = collect("traffic", parse_traffic)
    weather = weather[weather.site_id.isin(keep)] if not weather.empty else weather
    traffic = traffic[traffic.site_id.isin(keep)] if not traffic.empty else traffic

    w_sites, t_sites = sites[sites.kind == "weather"], sites[sites.kind == "traffic"]
    match = nearest_weather(t_sites, w_sites.reset_index(drop=True)) if len(w_sites) else pd.DataFrame()

    OUT.mkdir(exist_ok=True)
    sites.to_csv(OUT / "datex_sites.csv", index=False)
    weather.to_csv(OUT / "datex_weather.csv", index=False)
    traffic.to_csv(OUT / "datex_traffic.csv", index=False)
    match.to_csv(OUT / "traffic_to_weather.csv", index=False)

    print(f"sites: {len(w_sites)} weather stations, {len(t_sites)} traffic counters (radius {args.radius} km)")
    for name, df in (("weather", weather), ("traffic", traffic)):
        if df.empty:
            print(f"{name}: no rows")
        else:
            print(f"{name}: {len(df)} rows, {df.site_id.nunique()} sites, {df.time.min()} .. {df.time.max()}")
    if not weather.empty:
        print(weather.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
