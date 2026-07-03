#!/usr/bin/env python3
"""
Step 2 of the data pipeline: normalize raw_osm.json into a local SQLite staging
database (restaurants.db), de-duplicating along the way.

This DB is the offline "working store" — it is gitignored and never deployed.
It exists so de-duping, curating, and incremental re-runs are clean. Standard
library only.

Re-running is safe: existing rows are upserted by (osm_type, osm_id), and the
curation columns (include, needs_review, price, note, image, area, website) are
preserved for rows you've already touched — see UPSERT logic below.
"""

import json
import math
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_PATH = os.path.join(HERE, "raw_osm.json")
DB_PATH = os.path.join(HERE, "restaurants.db")

# Cuisine tags we treat as a confident Pakistani(-family) match (auto-include).
CUISINE_TAGS = {"pakistani", "punjabi", "kashmiri", "balti", "lahori", "mughlai"}


def load_raw() -> list:
    if not os.path.exists(RAW_PATH):
        raise SystemExit(f"{RAW_PATH} not found — run pipeline/fetch_osm.py first.")
    with open(RAW_PATH, encoding="utf-8") as f:
        return json.load(f).get("elements", [])


def latlng(el: dict):
    """Return (lat, lng) for a node, or the 'center' of a way/relation."""
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    if c:
        return c.get("lat"), c.get("lon")
    return None, None


def pick(tags: dict, *keys: str) -> str:
    for k in keys:
        v = tags.get(k)
        if v:
            return v.strip()
    return ""


def derive_area(tags: dict) -> str:
    """Best-effort neighbourhood/area from OSM address tags."""
    area = pick(tags, "addr:suburb", "addr:district", "addr:city", "addr:town")
    # OSM often stores "London" as the city; a suburb is more useful. If all we
    # have is the literal "London", keep it — better than blank.
    return area


def matched_cuisine(tags: dict) -> str:
    """Return the first recognised Pakistani-family cuisine tag, else ''."""
    raw = (tags.get("cuisine") or "").lower()
    for part in re.split(r"[;,]", raw):
        part = part.strip()
        if part in CUISINE_TAGS:
            return part
    return ""


def norm_name(name: str) -> str:
    """Loose key for near-duplicate detection."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def haversine_m(a_lat, a_lng, b_lat, b_lng) -> float:
    if None in (a_lat, a_lng, b_lat, b_lng):
        return float("inf")
    R = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlmb = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS places (
            osm_type     TEXT NOT NULL,
            osm_id       INTEGER NOT NULL,
            name         TEXT NOT NULL,
            cuisine_raw  TEXT DEFAULT '',
            cuisine      TEXT DEFAULT 'Pakistani',
            area         TEXT DEFAULT '',
            lat          REAL,
            lng          REAL,
            website      TEXT DEFAULT '',
            phone        TEXT DEFAULT '',
            price        TEXT DEFAULT '',
            note         TEXT DEFAULT '',
            image        TEXT DEFAULT '',
            needs_review INTEGER DEFAULT 0,
            include      INTEGER DEFAULT 1,
            merged_into  TEXT DEFAULT '',
            PRIMARY KEY (osm_type, osm_id)
        )
        """
    )
    con.commit()


def upsert(con: sqlite3.Connection, row: dict) -> None:
    """
    Insert a fresh OSM element, or refresh only the OSM-sourced fields on an
    existing one — preserving any manual curation (include, price, note, image,
    and an area/website you may have hand-filled).
    """
    con.execute(
        """
        INSERT INTO places (osm_type, osm_id, name, cuisine_raw, cuisine, area,
                            lat, lng, website, phone, needs_review, include)
        VALUES (:osm_type, :osm_id, :name, :cuisine_raw, :cuisine, :area,
                :lat, :lng, :website, :phone, :needs_review, :include)
        ON CONFLICT(osm_type, osm_id) DO UPDATE SET
            name        = excluded.name,
            cuisine_raw = excluded.cuisine_raw,
            lat         = excluded.lat,
            lng         = excluded.lng,
            phone       = excluded.phone,
            -- only backfill area/website if we didn't already have one
            area        = CASE WHEN places.area    = '' THEN excluded.area    ELSE places.area END,
            website     = CASE WHEN places.website = '' THEN excluded.website ELSE places.website END
        """,
        row,
    )


def dedup(con: sqlite3.Connection) -> int:
    """
    Merge near-duplicates (same normalized name within ~60 m) by keeping the
    richest row and setting include=0 + merged_into on the others.
    """
    rows = con.execute(
        "SELECT osm_type, osm_id, name, lat, lng, website FROM places WHERE include=1"
    ).fetchall()
    buckets: dict = {}
    for r in rows:
        buckets.setdefault(norm_name(r[2]), []).append(r)

    merged = 0
    for _key, group in buckets.items():
        if len(group) < 2:
            continue
        # keep the row with a website (richer), else the first
        group.sort(key=lambda r: (r[5] == "",))  # rows with website first
        keeper = group[0]
        for other in group[1:]:
            if haversine_m(keeper[3], keeper[4], other[3], other[4]) <= 60:
                con.execute(
                    "UPDATE places SET include=0, merged_into=? "
                    "WHERE osm_type=? AND osm_id=?",
                    (f"{keeper[0]}/{keeper[1]}", other[0], other[1]),
                )
                merged += 1
    return merged


def main() -> None:
    elements = load_raw()
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    kept = 0
    for el in elements:
        tags = el.get("tags", {})
        name = pick(tags, "name")
        if not name:
            continue  # unnamed POIs are useless in a directory
        lat, lng = latlng(el)
        cuisine_tag = matched_cuisine(tags)
        is_cuisine_match = bool(cuisine_tag)
        upsert(
            con,
            {
                "osm_type": el["type"],
                "osm_id": el["id"],
                "name": name,
                "cuisine_raw": tags.get("cuisine", ""),
                "cuisine": "Pakistani",
                "area": derive_area(tags),
                "lat": lat,
                "lng": lng,
                "website": pick(tags, "website", "contact:website", "url"),
                "phone": pick(tags, "phone", "contact:phone"),
                # cuisine matches auto-include; name-only catches await review
                "needs_review": 0 if is_cuisine_match else 1,
                "include": 1 if is_cuisine_match else 0,
            },
        )
        kept += 1
    con.commit()

    merged = dedup(con)
    con.commit()

    total = con.execute("SELECT count(*) FROM places").fetchone()[0]
    included = con.execute("SELECT count(*) FROM places WHERE include=1").fetchone()[0]
    review = con.execute(
        "SELECT count(*) FROM places WHERE needs_review=1 AND include=0"
    ).fetchone()[0]
    con.close()

    print(f"✓ Loaded {kept} named elements into {DB_PATH}")
    print(f"  {total} unique places | {included} auto-included | "
          f"{review} awaiting review | {merged} near-duplicates merged")
    print("  Curate:  sqlite3 pipeline/restaurants.db "
          "\"SELECT name, area, cuisine_raw FROM places WHERE needs_review=1;\"")
    print("  Then:    python3 pipeline/build_data.py")


if __name__ == "__main__":
    main()
