# Data pipeline (offline)

This folder builds the site's data from **OpenStreetMap**. It runs on your
machine only — **nothing here is deployed.** GitHub Pages serves static files
(`index.html` + `data.js`); there is no server or database in production. This
pipeline just regenerates `../data.js`, which you then commit.

```
Overpass API → fetch_osm.py → raw_osm.json → load_db.py → restaurants.db → build_data.py → ../data.js
                              (gitignored)                 (gitignored, SQLite)            (committed)
```

Python 3 standard library only — no `pip install` needed.

## Run it (3 steps)

```bash
python3 pipeline/fetch_osm.py     # 1. download Pakistani(-family) restaurants in Greater London
python3 pipeline/load_db.py       # 2. normalize + de-dupe into restaurants.db (SQLite)
python3 pipeline/build_data.py    # 3. export ../data.js
```

Then preview and deploy:

```bash
python3 -m http.server 8000       # open http://localhost:8000
git add data.js && git commit -m "Refresh restaurant data from OSM" && git push
```

## Curating (step 2.5, optional but recommended)

`load_db.py` **auto-includes** places with a clear Pakistani-family `cuisine`
tag, and marks *name-only* matches (e.g. things called "…Lahore…" but tagged
`cuisine=indian`) as `needs_review` and **excluded** until you approve them.

Review and edit with any SQLite client:

```bash
# See what's awaiting review:
sqlite3 pipeline/restaurants.db \
  "SELECT osm_type, osm_id, name, area, cuisine_raw FROM places WHERE needs_review=1;"

# Approve a real Pakistani spot:
sqlite3 pipeline/restaurants.db \
  "UPDATE places SET include=1 WHERE osm_type='node' AND osm_id=123456;"

# Hand-fill editorial fields OSM doesn't have:
sqlite3 pipeline/restaurants.db \
  "UPDATE places SET price='££', note='Famous for karahi', image='https://...' \
   WHERE name='Some Restaurant';"
```

Re-running `fetch_osm.py` + `load_db.py` later is **incremental**: it refreshes
OSM facts (name, coords, phone) but **preserves your curation** (`include`,
`price`, `note`, `image`, and any `area`/`website` you filled in). Then re-run
`build_data.py` to regenerate `data.js`.

## Notes

- **Coverage** is only as good as OSM's tagging. The name heuristic + your
  curation catch most Pakistani places, but not 100%. You can improve OSM
  directly at https://www.openstreetmap.org — fixes flow back on the next run.
- **Attribution is required** by the ODbL: `data.js` carries a provenance
  header and `index.html` shows an OpenStreetMap credit in the footer. Keep both.
- **Photos/prices/notes** are not in OSM — they're editorial, added via the DB.
