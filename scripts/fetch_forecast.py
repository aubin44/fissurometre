#!/usr/bin/env python3
"""Extract the MeteoSwiss local temperature forecast for one point into a small JSON file.

MeteoSwiss publishes its local forecasts as Open Government Data (STAC collection
``ch.meteoschweiz.ogd-local-forecasting``). Each hourly run ships one CSV per
parameter covering *every* forecast point in Switzerland (tens of MB), which is
far too heavy for a web page. This script, run by a scheduled GitHub Action,
downloads the latest hourly air temperature files (``tre200h0`` = mean,
``treq10h0`` / ``treq90h0`` = 10 % and 90 % quantiles, i.e. the official
uncertainty range), keeps only the requested point over the whole available
horizon, and writes ``data/forecast-gra.json`` that the page loads in a few kB.

Source: MeteoSwiss (CC BY 4.0) — https://opendatadocs.meteoswiss.ch/e-forecast-data/e4-local-forecast-data
Only the Python standard library is used.
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request

STAC_ITEMS = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-local-forecasting/items"
PARAMS = ["tre200h0", "treq10h0", "treq90h0"]   # mean, 10 % quantile, 90 % quantile
UA = {"User-Agent": "crackmeter-forecast/1.0 (+https://github.com/aubin44/fissurometre)"}


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def asset_re(param):
    return re.compile(r"\.(\d{12})\." + param + r"\.csv$")


def latest_asset(now, param):
    """Return (run_time, href) of the most recent file of one parameter, looking at today's and earlier items."""
    ASSET_RE = asset_re(param)
    candidates = []
    for back in range(0, 3):
        day = (now - dt.timedelta(days=back)).strftime("%Y%m%d")
        try:
            item = get_json(STAC_ITEMS + "/" + day + "-ch")
        except Exception:
            continue
        for key, asset in (item.get("assets") or {}).items():
            href = asset.get("href", "")
            m = ASSET_RE.search(href) or ASSET_RE.search(key)
            if m:
                candidates.append((m.group(1), href))
        if candidates:
            break
    if not candidates:
        # fallback: walk the item listing
        url = STAC_ITEMS + "?limit=100"
        for _ in range(20):
            page = get_json(url)
            for item in page.get("features", []):
                for key, asset in (item.get("assets") or {}).items():
                    href = asset.get("href", "")
                    m = ASSET_RE.search(href) or ASSET_RE.search(key)
                    if m:
                        candidates.append((m.group(1), href))
            nxt = [l["href"] for l in page.get("links", []) if l.get("rel") == "next"]
            if not nxt:
                break
            url = nxt[0]
    if not candidates:
        raise SystemExit("no %s asset found in the STAC collection" % param)
    run, href = max(candidates)
    return dt.datetime.strptime(run, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc), href


def parse_time(s):
    s = s.strip()
    for fmt in ("%Y%m%d%H%M", "%Y%m%d%H", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


def extract(stream, point_id, point_type_id, start, end, param):
    """Read the semicolon-separated CSV and keep the rows of one point between start and end (UTC)."""
    text = io.TextIOWrapper(stream, encoding="latin-1", newline="")
    header = None
    out = []
    for line in text:
        line = line.strip()
        if not line:
            continue
        cols = [c.strip().strip('"') for c in line.split(";")]
        if header is None:
            header = [c.lower() for c in cols]
            try:
                i_pid = header.index("point_id")
                i_ptype = header.index("point_type_id")
                i_val = header.index(param)
            except ValueError:
                raise SystemExit("unexpected CSV header: %s" % line)
            i_time = next((header.index(c) for c in ("date", "reference_timestamp", "time", "datetime") if c in header), None)
            continue
        if len(cols) < len(header):
            continue
        if cols[i_pid] != str(point_id) or cols[i_ptype] != str(point_type_id):
            continue
        if i_time is None:
            i_time = next(i for i, c in enumerate(cols) if re.fullmatch(r"\d{12}", c))
        t = parse_time(cols[i_time])
        try:
            v = float(cols[i_val].replace(",", "."))
        except ValueError:
            continue
        if t is None or not (start <= t <= end):
            continue
        out.append((t, v))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--point-id", default="14", help="MeteoSwiss point_id (14 = Fribourg / Grangeneuve, GRA)")
    ap.add_argument("--point-type-id", default="1", help="1 = weather station")
    ap.add_argument("--station", default="GRA")
    ap.add_argument("--hours", type=int, default=0, help="forecast horizon kept in the JSON (0 = everything available)")
    ap.add_argument("--out", default="data/forecast-gra.json")
    ap.add_argument("--csv", nargs="+", help="local CSV files (mean, q10, q90) instead of downloading (tests)")
    ap.add_argument("--run", help="run time YYYYMMDDHHMM for --csv")
    ap.add_argument("--now", help="override 'now' as YYYYMMDDHHMM (tests)")
    a = ap.parse_args()

    now = parse_time(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(hours=a.hours) if a.hours > 0 else start + dt.timedelta(days=60)
    series = {}
    run = None
    for k, param in enumerate(PARAMS):
        try:
            if a.csv:
                if k >= len(a.csv):
                    continue
                prun = parse_time(a.run) if a.run else now
                stream = open(a.csv[k], "rb")
            else:
                prun, href = latest_asset(now, param)
                stream = urllib.request.urlopen(urllib.request.Request(href, headers=UA), timeout=300)
            with stream:
                series[param] = dict(extract(stream, a.point_id, a.point_type_id, start, end, param))
            if param == PARAMS[0]:
                run = prun
        except SystemExit:
            if param == PARAMS[0]:
                raise
            print("warning: %s unavailable, continuing without it" % param)
    rows = sorted(series.get(PARAMS[0], {}).items())
    if len(rows) < 6:
        print("only %d rows found for point %s/%s, keeping the previous file" % (len(rows), a.point_type_id, a.point_id))
        return 0
    q10 = series.get("treq10h0", {})
    q90 = series.get("treq90h0", {})

    def row(t, v):
        r = [t.strftime("%Y-%m-%dT%H:%MZ"), round(v, 1)]
        if t in q10 and t in q90:
            r += [round(min(q10[t], q90[t]), 1), round(max(q10[t], q90[t]), 1)]
        return r

    doc = {
        "source": "MeteoSwiss, local forecast (Open Government Data, CC BY 4.0)",
        "parameters": PARAMS,
        "columns": ["time_utc", "mean", "q10", "q90"],
        "unit": "degC",
        "station": a.station,
        "point_id": int(a.point_id),
        "run": run.strftime("%Y-%m-%dT%H:%MZ"),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "data": [row(t, v) for t, v in rows],
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print("wrote %s: %d values (%s to %s), run %s" % (a.out, len(rows), doc["data"][0][0], doc["data"][-1][0], doc["run"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

