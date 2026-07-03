#!/usr/bin/env python3
"""
Step 1 of the data pipeline: fetch Pakistani(-family) restaurants in Greater
London from the OpenStreetMap Overpass API and save the raw result to
raw_osm.json.

Runs OFFLINE on your machine only — it is never deployed. Standard library only
(no pip installs). See pipeline/README.md for the full workflow.

Data © OpenStreetMap contributors, licensed under the ODbL.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_PATH = os.path.join(HERE, "raw_osm.json")

# Public Overpass endpoints. We try them in order and fall back on failure.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Greater London administrative boundary is OSM relation 175342.
# Overpass area ids are 3600000000 + relation id.
GREATER_LONDON_AREA = 3600175342

# High-precision cuisine tags that indicate a Pakistani(-family) restaurant.
CUISINE_RE = "pakistani|punjabi|kashmiri|balti|lahori|mughlai"

# Lower-precision name heuristic: catches places mis-tagged cuisine=indian or
# with no cuisine tag at all. These are flagged needs_review for manual curation.
NAME_RE = "lahore|karachi|kashmir|balti|tayyab|mughlai|desi|halal|karahi|peshawar|lahori|punjab"

# We query both sit-down restaurants and fast_food (many curry/kebab houses are
# tagged as fast_food in OSM).
AMENITIES = "restaurant|fast_food"

# Build the Overpass QL. `out center;` gives ways/relations a representative
# lat/lng. The area filter scopes everything to Greater London.
QUERY = f"""
[out:json][timeout:120];
area({GREATER_LONDON_AREA})->.london;
(
  nwr["amenity"~"{AMENITIES}"]["cuisine"~"{CUISINE_RE}",i](area.london);
  nwr["amenity"~"{AMENITIES}"]["name"~"{NAME_RE}",i](area.london);
);
out center tags;
""".strip()

USER_AGENT = (
    "South-Asia-London-Eats/1.0 (static-site data pipeline; "
    "contact via github.com/Dannyizlam97/South_Asia_London_Eats)"
)


def make_ssl_context() -> ssl.SSLContext:
    """
    Build a verifying SSL context that works even when the python.org macOS
    build can't find its CA bundle. We try, in order: an already-working default
    context, the SSL_CERT_FILE env var, the certifi package (if installed), and
    the system bundle at /etc/ssl/cert.pem. Verification stays ON throughout.
    """
    ctx = ssl.create_default_context()
    default_cafile = ssl.get_default_verify_paths().openssl_cafile
    if default_cafile and os.path.exists(default_cafile):
        return ctx  # the default already works

    candidates = [os.environ.get("SSL_CERT_FILE")]
    try:
        import certifi  # optional; not required
        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates.append("/etc/ssl/cert.pem")

    for cafile in candidates:
        if cafile and os.path.exists(cafile):
            ctx.load_verify_locations(cafile=cafile)
            return ctx
    # Nothing found — return the default and let it raise a clear SSL error.
    return ctx


def fetch(query: str) -> dict:
    """POST the query to Overpass, trying each endpoint with retry/backoff."""
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    ctx = make_ssl_context()
    last_err = None
    for endpoint in ENDPOINTS:
        for attempt in range(1, 4):
            try:
                print(f"→ Querying {endpoint} (attempt {attempt})...")
                req = urllib.request.Request(
                    endpoint,
                    data=body,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
                    return json.load(resp)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                code = getattr(e, "code", None)
                last_err = e
                # 429 (rate limited) and 504 (gateway timeout) are worth retrying.
                wait = 5 * attempt
                print(f"  ! failed ({code or e}); backing off {wait}s", file=sys.stderr)
                time.sleep(wait)
        print(f"  ! giving up on {endpoint}, trying next mirror", file=sys.stderr)
    raise SystemExit(f"All Overpass endpoints failed. Last error: {last_err}")


def main() -> None:
    print("Fetching Pakistani(-family) restaurants in Greater London from OSM...")
    result = fetch(QUERY)
    elements = result.get("elements", [])

    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Quick provenance summary — this answers "how many are there?".
    named = [e for e in elements if e.get("tags", {}).get("name")]
    print(f"✓ Saved {len(elements)} elements ({len(named)} with a name) to {RAW_PATH}")
    print("  Next: python3 pipeline/load_db.py")


if __name__ == "__main__":
    main()
