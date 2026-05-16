$ cat /tmp/boxi-catalog-update/scripts/build_catalog.py

#!/usr/bin/env python3
"""
Builds catalog.json from bol.com's Marketing Catalog API.

Reads BOL_CLIENT_ID and BOL_CLIENT_SECRET from environment.
Runs via GitHub Action (daily cron) or locally.
"""
import base64
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

CID = os.environ.get("BOL_CLIENT_ID")
CS = os.environ.get("BOL_CLIENT_SECRET")
if not CID or not CS:
    print("❌ Missing BOL_CLIENT_ID or BOL_CLIENT_SECRET environment variables", file=sys.stderr)
    sys.exit(1)

INTEREST_CATEGORIES = {
    "Koken":         [("11764", "Koken & Tafelen")],
    "Sport":         [("14648", "Sport")],
    "Reizen":        [("16799", "Reisbagage"), ("15270", "Kamperen")],
    "Muziek":        [("3132", "Muziek")],
    "Tech":          [("3136", "Elektronica"), ("3134", "Computer")],
    "Beauty":        [("43228", "Beauty")],
    "Gaming":        [("3135", "Gaming")],
    "Lezen":         [("8299", "Boeken")],
    "Natuur":        [("12974", "Tuin")],
    "Film & Series": [("3133", "Films & Series")],
    "Wijn & Drank":  [("36080", "Eten & Drinken")],
    "Huisdieren":    [("12748", "Dieren")],
}

# Search fallbacks for cells that don't have enough items via popular endpoint
GAP_SEARCHES = {
    ("Muziek", "€100+"):       [("koptelefoon", "3132"), ("Sonos", None), ("Bose speaker", None), ("Marshall speaker", None)],
    ("Lezen", "€100+"):        [("boekenkast", None), ("Kindle", None), ("Kobo", None)],
    ("Film & Series", "€50-100"): [("boxset", "3133"), ("blu-ray", "3133"), ("complete serie", "3133")],
    ("Film & Series", "€100+"): [("blu-ray collector", "3133"), ("complete saga", "3133")],
    ("Wijn & Drank", "€100+"): [("Macallan whisky", None), ("Glenfiddich 18", None), ("Hennessy", None), ("Veuve Clicquot", None)],
    ("Beauty", "€100+"):       [("Dior parfum", None), ("Chanel parfum", None), ("Tom Ford parfum", None)],
}

COLORS = {
    "Koken":         ["E6A35F", "C9874E", "8B5A2B", "D4626A", "9CB36A"],
    "Sport":         ["5B7DC9", "3D6BBF", "84C4AC", "2E9B7B", "27AE60"],
    "Reizen":        ["7DB5D8", "9FB8E8", "E8A0B0", "D4956D", "2980B9"],
    "Muziek":        ["8B6FD4", "564A63", "2A2330", "B07ABF", "A0522D"],
    "Tech":          ["3F4756", "1F2A37", "5C6470", "8DA5C4", "2C3E50"],
    "Beauty":        ["E8A0B0", "F0BFC4", "D6809B", "C49FB5", "F1948A"],
    "Gaming":        ["E4000F", "564A63", "9C5DD1", "2A2330", "E67E22"],
    "Lezen":         ["7A6049", "8B6F4D", "BFA68A", "5C4631", "A0522D"],
    "Natuur":        ["84C4AC", "9CB36A", "5E8050", "B5CC8F", "27AE60"],
    "Film & Series": ["D4263A", "6B2A35", "1F1A1F", "B0394D", "E67E22"],
    "Wijn & Drank":  ["9B5C7E", "6F4E37", "C49FB5", "5A2638", "C9A84C"],
    "Huisdieren":    ["C9B89A", "B89976", "8B6F4D", "D6BFA0", "A0522D"],
}

OFFTOPIC_BLOCKLIST = ["kerstboom", "philosophy", "palgrave", "perspectives"]
PER_CELL = 12

def bucket(price):
    if price < 25: return "Onder €25"
    if price < 50: return "€25-50"
    if price < 100: return "€50-100"
    return "€100+"

def get_token():
    auth = base64.b64encode(f"{CID}:{CS}".encode()).decode()
    req = urllib.request.Request(
        "https://login.bol.com/token?grant_type=client_credentials",
        method="POST", headers={"Authorization": f"Basic {auth}"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]

def api_get(token, path, **params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"https://api.bol.com/marketing/catalog/v1{path}?{qs}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Accept-Language": "nl"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (404, 400):
            return {"results": []}
        raise

def collect(token, items, dest, seen_pids, want_budget=None):
    added = 0
    for p in items:
        pid = p.get("bolProductId")
        if not pid or pid in seen_pids: continue
        offer = p.get("offer") or {}
        price = offer.get("price")
        if not price or price < 1: continue
        b = bucket(price)
        if want_budget and b != want_budget: continue
        img = (p.get("image") or {}).get("url")
        if not img: continue
        title = (p.get("title") or "").strip()
        if not title: continue
        if any(w in title.lower() for w in OFFTOPIC_BLOCKLIST): continue
        seen_pids.add(pid)
        dest.append({
            "ean": p.get("ean"), "bolProductId": pid, "title": title,
            "price": price, "image": img,
        })
        added += 1
    return added

def main():
    print(f"🚀 Refreshing catalog at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    token = get_token()
    print("✅ Token obtained")

    buckets = defaultdict(lambda: defaultdict(list))
    seen_pids = set()

    # Phase 1: popular per category
    for interest, cats in INTEREST_CATEGORIES.items():
        print(f"📦 {interest}", end="", flush=True)
        for cat_id, cat_label in cats:
            collected_for_interest = sum(len(buckets[interest][b]) for b in ["Onder €25","€25-50","€50-100","€100+"])
            if collected_for_interest >= 120: break
            for page in range(1, 7):
                data = api_get(token, "/products/lists/popular",
                    **{"category-id": cat_id, "country-code": "NL", "page": page,
                       "page-size": 50, "include-image": "true", "include-offer": "true"})
                items = data.get("results", [])
                if not items: break
                flat = []
                collect(token, items, flat, seen_pids)
                for it in flat:
                    b = bucket(it["price"])
                    buckets[interest][b].append(it)
                time.sleep(0.25)
        print(f" → {sum(len(buckets[interest][b]) for b in ['Onder €25','€25-50','€50-100','€100+'])} items")

    # Phase 2: fill gaps via search
    print("\n🔍 Filling gap cells via search")
    for (interest, budget), queries in GAP_SEARCHES.items():
        have = len(buckets[interest][budget])
        if have >= PER_CELL: continue
        for term, cat_id in queries:
            if len(buckets[interest][budget]) >= PER_CELL: break
            params = {"search-term": term, "country-code": "NL", "page": 1,
                      "page-size": 50, "sort": "POPULARITY",
                      "include-image": "true", "include-offer": "true"}
            if cat_id: params["category-id"] = cat_id
            data = api_get(token, "/products/search", **params)
            flat = []
            collect(token, data.get("results", []), flat, seen_pids, want_budget=budget)
            for it in flat:
                if len(buckets[interest][budget]) >= PER_CELL: break
                buckets[interest][budget].append(it)
            time.sleep(0.25)
        print(f"  {interest} × {budget}: {have} → {len(buckets[interest][budget])}")

    # Phase 3: assemble catalog (deterministic IDs based on EAN for stability)
    random.seed(99)
    budgets_order = ["Onder €25", "€25-50", "€50-100", "€100+"]
    products = []
    pid_counter = 1
    for interest in COLORS:
        for budget in budgets_order:
            for item in buckets[interest][budget][:PER_CELL]:
                price = item["price"]
                price_str = f"€{int(price)}" if price == int(price) else f"€{price:.2f}".replace(".", ",")
                tag = ""
                if price >= 100 and random.random() < 0.3:
                    tag = "Premium"
                elif price < 100:
                    tag = random.choice(["", "", "", "Bestseller", "Tip", "Geliefd"])
                products.append({
                    "id": f"b{pid_counter}",
                    "name": item["title"][:120],
                    "price": price_str,
                    "tag": tag,
                    "colorHex": random.choice(COLORS[interest]),
                    "interests": [interest],
                    "budget": budget,
                    "productId": item["bolProductId"],
                    "imageURL": item["image"],
                })
                pid_counter += 1

    # Version: use yyyymmdd for traceability
    version = int(time.strftime("%Y%m%d"))
    catalog = {"version": version, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "products": products}

    out_path = sys.argv[1] if len(sys.argv) > 1 else "catalog.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    # Summary
    grid = defaultdict(lambda: defaultdict(int))
    for p in products:
        for i in p["interests"]:
            grid[i][p["budget"]] += 1
    print(f"\n✅ Catalog v{version} written to {out_path}: {len(products)} products")
    issues = sum(1 for i in COLORS for b in budgets_order if grid[i][b] < 10)
    print(f"   Coverage: {'all 10+' if issues == 0 else f'{issues} cells under 10'}")

if __name__ == "__main__":
    main()
