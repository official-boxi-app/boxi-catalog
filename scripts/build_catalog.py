#!/usr/bin/env python3
"""
Builds catalog.json from bol.com's Marketing Catalog API.

Reads BOL_CLIENT_ID and BOL_CLIENT_SECRET from environment.
Runs via GitHub Action (daily cron) or locally.

Tagt elk product met:
  - subcategory : afgeleid van de bol-(sub)categorie waar het uit komt
  - minAge/maxAge : leeftijdsgrens van de ontvanger (regelgebaseerd)
en ontdubbelt variant-producten per categorie+budget-cel.
"""
import base64
import json
import os
import random
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

CID = os.environ.get("BOL_CLIENT_ID")
CS = os.environ.get("BOL_CLIENT_SECRET")
if not CID or not CS:
    print("❌ Missing BOL_CLIENT_ID or BOL_CLIENT_SECRET environment variables", file=sys.stderr)
    sys.exit(1)

# Per interesse: lijst van (bol-categorie-id, label, subcategorie).
#   subcategorie = None  → interesse zonder verfijning (geen sub-filter in de app)
#   subcategorie = "..." → elk product uit die bol-categorie krijgt dit subcategorie-label
INTEREST_CATEGORIES = {
    "Baby & Peuter": [],
    "Speelgoed":     [("4", "Speelgoed", None)],
    "Koken":         [("11764", "Koken & Tafelen", None)],
    "Sport":         [("14648", "Sport", None)],
    "Reizen":        [("16799", "Reisbagage", None), ("15270", "Kamperen", None)],
    "Muziek":        [("3132", "Muziek", None)],
    "Tech":          [("3136", "Elektronica", None), ("3134", "Computer", None)],
    "Beauty":        [("43228", "Beauty", None)],
    "Fashion":       [("16715", "Mode", None), ("16716", "Sieraden & Horloges", None)],
    "Gaming":        [("3135", "Gaming", None)],
    "Lezen": [
        ("24410", "Literatuur & Romans",       "Romans & thrillers"),
        ("2551",  "Thrillers & Spanning",      "Romans & thrillers"),
        ("24421", "Kinderboeken",              "Kinderboeken"),
        ("52814", "Strips & Manga",            "Strips & manga"),
        ("24421", "Young Adult",               "Young Adult"),
        ("40342", "Biografieën",               "Non-fictie"),
        ("24054", "Persoonlijke ontwikkeling", "Non-fictie"),
    ],
    "Creatief":      [("19237", "Tekenen & Kleuren", "Tekenen & Kleuren"), ("19215", "Schilderen", "Schilderen"), ("19398", "Kleien", "Kleien")],
    "Natuur":        [("12974", "Tuin", None)],
    "Film & Series": [("3133", "Films & Series", None)],
    "Wonen":         [("14035", "Wonen", None)],
    "Wijn & Drank":  [],
    "Huisdieren": [
        ("12749", "Honden",      "Honden"),
        ("12835", "Katten",      "Katten"),
        ("12888", "Knaagdieren", "Knaagdieren"),
        ("12885", "Vissen",      "Vissen"),
        ("12887", "Vogels",      "Vogels"),
        ("12889", "Reptielen",   "Reptielen"),
    ],
}

# Searches to enrich bol-menu-like subcategories and fill sparse cells.
# Tuple: (interest, subcategory, search term, optional bol category-id)
CATEGORY_SEARCHES = [
    ("Baby & Peuter", "Baby speelgoed", "baby speelgoed", "4"),
    ("Baby & Peuter", "Knuffels", "knuffeldoekje baby", "4"),
    ("Baby & Peuter", "Boekjes", "baby boekje", "24421"),
    ("Baby & Peuter", "Verzorging", "baby verzorging cadeau", "4"),

    ("Speelgoed", "Bouwen & Constructie", "bouwspeelgoed", "4"),
    ("Speelgoed", "Spellen", "bordspel kinderen", "4"),
    ("Speelgoed", "Educatief speelgoed", "educatief speelgoed", "4"),
    ("Speelgoed", "Speelfiguren", "speelfiguren", "4"),
    ("Speelgoed", "Poppen", "poppen speelgoed", "4"),
    ("Speelgoed", "Voertuigen", "speelgoed voertuigen", "4"),
    ("Speelgoed", "Puzzels", "puzzels kinderen", "4"),
    ("Speelgoed", "Buitenspeelgoed", "buitenspeelgoed", "4"),

    ("Koken", "Keukengadgets", "keukengadget cadeau", "11764"),
    ("Koken", "Bakken", "bakspullen", "11764"),
    ("Koken", "Kookboeken", "kookboek", "24410"),
    ("Koken", "Pannen", "pannenset", "11764"),
    ("Koken", "Koffie & Thee", "koffie thee cadeau", "11764"),
    ("Koken", "BBQ", "barbecue accessoires", "11764"),

    ("Sport", "Fitness", "fitness cadeau", "14648"),
    ("Sport", "Voetbal", "voetbal cadeau", "14648"),
    ("Sport", "Hardlopen", "hardlopen accessoires", "14648"),
    ("Sport", "Fietsen", "fiets accessoires", "14648"),
    ("Sport", "Yoga", "yoga cadeau", "14648"),
    ("Sport", "Outdoor", "outdoor sport cadeau", "14648"),

    ("Reizen", "Koffers & Tassen", "koffer reistas", "16799"),
    ("Reizen", "Reisaccessoires", "reisaccessoires", "16799"),
    ("Reizen", "Outdoor", "kamperen accessoires", "15270"),
    ("Reizen", "Camera-accessoires", "camera accessoire reizen", "3136"),

    ("Muziek", "Koptelefoons", "koptelefoon", "3132"),
    ("Muziek", "Speakers", "bluetooth speaker", "3132"),
    ("Muziek", "Instrumenten", "muziekinstrument", "3132"),
    ("Muziek", "Vinyl & CD", "vinyl plaat", "3132"),
    ("Muziek", "Muziekaccessoires", "muziek accessoires", "3132"),

    ("Tech", "Telefoonaccessoires", "telefoonaccessoires", "3136"),
    ("Tech", "Audio", "audio gadget", "3136"),
    ("Tech", "Smart home", "smart home", "3136"),
    ("Tech", "Computeraccessoires", "computeraccessoires", "3134"),
    ("Tech", "Camera", "camera gadget", "3136"),
    ("Tech", "Wearables", "smartwatch activity tracker", "3136"),

    ("Beauty", "Make-up", "make-up cadeau", "43228"),
    ("Beauty", "Skincare", "skincare cadeau", "43228"),
    ("Beauty", "Parfum", "parfum cadeau", "43228"),
    ("Beauty", "Haarverzorging", "haarverzorging cadeau", "43228"),
    ("Beauty", "Lichaamsverzorging", "lichaamsverzorging cadeau", "43228"),
    ("Beauty", "Wellness", "wellness cadeau", "43228"),

    ("Fashion", "Tassen", "tas cadeau", "16715"),
    ("Fashion", "Sieraden", "sieraden cadeau", "16716"),
    ("Fashion", "Horloges", "horloge cadeau", "16716"),
    ("Fashion", "Accessoires", "mode accessoires cadeau", "16715"),

    ("Gaming", "Nintendo", "Nintendo Switch game", "3135"),
    ("Gaming", "PlayStation", "PlayStation game", "3135"),
    ("Gaming", "Xbox", "Xbox game", "3135"),
    ("Gaming", "PC", "PC game accessoires", "3135"),
    ("Gaming", "Game-accessoires", "gaming accessoires", "3135"),
    ("Gaming", "LEGO & bouwsets", "LEGO gaming", "4"),

    ("Lezen", "Young Adult", "young adult boek", "24421"),
    ("Lezen", "Biografie", "biografie boek", "40342"),

    ("Creatief", "Knutselen", "knutselen cadeau", "4"),
    ("Creatief", "Tekenen & Kleuren", "tekenspullen", "19237"),
    ("Creatief", "Schilderen", "schilderen set", "19215"),
    ("Creatief", "Kleien", "kleien boetseren", "19398"),
    ("Creatief", "Fotografie", "fotografie cadeau", "3136"),
    ("Creatief", "DIY", "DIY pakket volwassenen", None),

    ("Natuur", "Tuinieren", "tuinieren cadeau", "12974"),
    ("Natuur", "Vogels", "vogelhuisje", "12974"),
    ("Natuur", "Planten", "plant cadeau", "12974"),
    ("Natuur", "Outdoor", "outdoor natuur cadeau", "15270"),

    ("Film & Series", "Films", "film blu-ray", "3133"),
    ("Film & Series", "Series", "complete serie dvd", "3133"),
    ("Film & Series", "Blu-ray & DVD", "blu-ray dvd", "3133"),
    ("Film & Series", "Merchandise", "film merchandise", "3133"),

    ("Wonen", "Woondecoratie", "woondecoratie cadeau", "14035"),
    ("Wonen", "Verlichting", "sfeerverlichting", "14035"),
    ("Wonen", "Kaarsen & Geur", "geurkaars diffuser", "14035"),
    ("Wonen", "Textiel", "woontextiel cadeau", "14035"),
    ("Wonen", "Opbergen", "opbergen wonen", "14035"),

    ("Wijn & Drank", "Wijn", "wijn cadeau", "36080"),
    ("Wijn & Drank", "Bier", "bier cadeaupakket", "36080"),
    ("Wijn & Drank", "Whisky", "whisky cadeau", "36080"),
    ("Wijn & Drank", "Cocktails", "cocktail set", "36080"),
    ("Wijn & Drank", "Glazen & Accessoires", "wijnglazen drankaccessoires", "36080"),
]

# Search fallbacks for cells that don't have enough items via popular endpoint.
GAP_SEARCHES = {
    ("Baby & Peuter", "Onder €25"): [("baby speelgoed", "4"), ("knuffeldoekje", "4"), ("babyboekje", "24421")],
    ("Baby & Peuter", "€25-50"):    [("loopwagen baby", "4"), ("speelkleed baby", "4"), ("baby cadeau", "4")],
    ("Baby & Peuter", "€50-100"):   [("baby gym", "4"), ("houten baby speelgoed", "4"), ("babyfoon", "3136")],
    ("Baby & Peuter", "€100+"):     [("loopfiets baby", "4"), ("babyfoon camera", "3136"), ("speelgoedkist baby", "14035")],
    ("Speelgoed", "Onder €25"):     [("spel kinderen", "4"), ("puzzel kinderen", "4"), ("LEGO kinderen", "4")],
    ("Speelgoed", "€25-50"):        [("bouwspeelgoed", "4"), ("educatief speelgoed", "4"), ("buitenspeelgoed", "4")],
    ("Speelgoed", "€50-100"):       [("LEGO set kinderen", "4"), ("bordspel kinderen", "4"), ("houten speelgoed", "4")],
    ("Speelgoed", "€100+"):         [("grote LEGO set kinderen", "4"), ("speelkeuken", "4"), ("trampoline", "4")],
    ("Muziek", "€100+"):       [("koptelefoon", "3132"), ("Sonos", None), ("Bose speaker", None), ("Marshall speaker", None)],
    ("Lezen", "€100+"):        [("boekenkast", None), ("Kindle", None), ("Kobo", None)],
    ("Film & Series", "€50-100"): [("boxset", "3133"), ("blu-ray", "3133"), ("complete serie", "3133")],
    ("Film & Series", "€100+"): [("blu-ray collector", "3133"), ("complete saga", "3133")],
    ("Wijn & Drank", "€100+"): [("Macallan whisky", None), ("Glenfiddich 18", None), ("Hennessy", None), ("Veuve Clicquot", None)],
    ("Beauty", "€100+"):       [("Dior parfum", None), ("Chanel parfum", None), ("Tom Ford parfum", None)],
}

COLORS = {
    "Baby & Peuter": ["F0BFC4", "E8A0B0", "B5CC8F", "9FB8E8", "D6BFA0"],
    "Speelgoed":     ["E4000F", "E6A35F", "9C5DD1", "84C4AC", "5B7DC9"],
    "Koken":         ["E6A35F", "C9874E", "8B5A2B", "D4626A", "9CB36A"],
    "Sport":         ["5B7DC9", "3D6BBF", "84C4AC", "2E9B7B", "27AE60"],
    "Reizen":        ["7DB5D8", "9FB8E8", "E8A0B0", "D4956D", "2980B9"],
    "Muziek":        ["8B6FD4", "564A63", "2A2330", "B07ABF", "A0522D"],
    "Tech":          ["3F4756", "1F2A37", "5C6470", "8DA5C4", "2C3E50"],
    "Beauty":        ["E8A0B0", "F0BFC4", "D6809B", "C49FB5", "F1948A"],
    "Fashion":       ["D6809B", "C49FB5", "8B6FD4", "BFA68A", "2A2330"],
    "Gaming":        ["E4000F", "564A63", "9C5DD1", "2A2330", "E67E22"],
    "Lezen":         ["7A6049", "8B6F4D", "BFA68A", "5C4631", "A0522D"],
    "Creatief":      ["E6A35F", "E4000F", "9C5DD1", "84C4AC", "D6809B"],
    "Natuur":        ["84C4AC", "9CB36A", "5E8050", "B5CC8F", "27AE60"],
    "Film & Series": ["D4263A", "6B2A35", "1F1A1F", "B0394D", "E67E22"],
    "Wonen":         ["BFA68A", "7A6049", "D6BFA0", "8B6F4D", "C9874E"],
    "Wijn & Drank":  ["9B5C7E", "6F4E37", "C49FB5", "5A2638", "C9A84C"],
    "Huisdieren":    ["C9B89A", "B89976", "8B6F4D", "D6BFA0", "A0522D"],
}

OFFTOPIC_BLOCKLIST = ["kerstboom", "philosophy", "palgrave", "perspectives"]
PER_CELL = 48

# Per bol-categorie maximaal zoveel items meenemen, zodat subcategorieën
# binnen een interesse in balans blijven (anders vult één subcategorie alles).
# Schalen mee met PER_CELL: bij 48 per cel × 4 budgets = 192 items per
# (sub)categorie nodig, plus marge voor budget-spreiding en dedup.
CAP_PER_SUBCAT = 350
CAP_PER_PLAIN_CAT = 800
SUBCAT_SEARCH_PAGES = 5

# --- Leeftijd-tagging -------------------------------------------------------
# bol levert geen leeftijdsdata; deze regels leiden het af uit titel + subcategorie.
PEGI18_KEYWORDS = [
    "call of duty", "grand theft auto", "assassin's creed", "cyberpunk",
    "mortal kombat", "resident evil", "the last of us", "red dead",
    "far cry", "hitman", "sniper elite", "doom eternal", "mafia",
]
SPIRITS_KEYWORDS = ["distilleer", "destilleer", "moonshine"]
DRINK_OFFTOPIC_KEYWORDS = [
    "koffie", "thee", "espresso", "cappuccino", "nespresso", "koffiebonen",
    "waterfles", "drinkfles", "thermosfles", "sodastream", "limonade",
]

INTEREST_AGE_DEFAULTS = {
    "Baby & Peuter": (0, 3),
    "Speelgoed": (4, 12),
    "Koken": (14, None),
    "Sport": (7, None),
    "Reizen": (14, None),
    "Muziek": (10, None),
    "Tech": (10, None),
    "Beauty": (12, None),
    "Fashion": (12, None),
    "Gaming": (7, None),
    "Lezen": (0, None),
    "Creatief": (4, None),
    "Natuur": (4, None),
    "Film & Series": (6, None),
    "Wonen": (16, None),
    "Wijn & Drank": (18, None),
    "Huisdieren": (4, None),
}


def compute_age(interest, subcategory, title):
    """Geeft (minAge|None, maxAge|None) voor een product."""
    t = title.lower()
    min_age, max_age = INTEREST_AGE_DEFAULTS.get(interest, (None, None))

    if interest == "Baby & Peuter":
        max_age = 3
    if subcategory in ("Kinderboeken", "Baby speelgoed", "Knuffels", "Boekjes"):
        max_age = min(max_age, 12) if max_age is not None else 12
    if subcategory == "Kinderboeken":
        max_age = 12
    if subcategory == "Young Adult":
        min_age = max(min_age or 0, 12)
    if "voor volwassenen" in t:
        min_age = 16
        max_age = None
    if interest == "Gaming" and any(k in t for k in PEGI18_KEYWORDS):
        min_age = 16
    if any(k in t for k in SPIRITS_KEYWORDS):
        min_age = 18
        max_age = None
    return min_age, max_age


def family_key(name):
    """Sleutel om variant-producten (zelfde product, andere kleur/maat) te herkennen.
    Identiek aan GiftItem.familyKey in de app."""
    s = unicodedata.normalize("NFD", name.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.split(" - ")[0].strip()


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


def collect(token, items, dest, seen_pids, want_budget=None, subcategory=None, blocklist_extra=None):
    added = 0
    blocklist_extra = blocklist_extra or []
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
        lowered = title.lower()
        if any(w in lowered for w in OFFTOPIC_BLOCKLIST): continue
        if any(w in lowered for w in blocklist_extra): continue
        seen_pids.add(pid)
        dest.append({
            "ean": p.get("ean"), "bolProductId": pid, "title": title,
            "price": price, "image": img, "subcategory": subcategory,
        })
        added += 1
    return added


def select_cell(items):
    """Selecteert tot PER_CELL producten voor één interesse+budget-cel:
    ontdubbelt op productfamilie en mengt subcategorieën via round-robin,
    zodat elke subcategorie kans maakt in de cel."""
    seen = set()
    by_sub = defaultdict(list)
    for it in items:
        fk = family_key(it["title"])
        if fk in seen:
            continue
        seen.add(fk)
        by_sub[it.get("subcategory")].append(it)

    subs = list(by_sub.keys())
    chosen, i = [], 0
    while len(chosen) < PER_CELL and any(by_sub.values()):
        lst = by_sub[subs[i % len(subs)]]
        if lst:
            chosen.append(lst.pop(0))
        i += 1
    return chosen


def main():
    print(f"🚀 Refreshing catalog at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    token = get_token()
    print("✅ Token obtained")

    buckets = defaultdict(lambda: defaultdict(list))
    seen_pids = set()
    budgets_order = ["Onder €25", "€25-50", "€50-100", "€100+"]

    # Phase 1: fill subcategories first, so broad popular categories cannot
    # consume those products before they receive a subcategory label.
    print("\n🏷️  Filling subcategories via search")
    for interest, subcat, term, cat_id in CATEGORY_SEARCHES:
        before = sum(len(buckets[interest][b]) for b in budgets_order)
        for page in range(1, SUBCAT_SEARCH_PAGES + 1):
            params = {"search-term": term, "country-code": "NL", "page": page,
                      "page-size": 50, "sort": "POPULARITY",
                      "include-image": "true", "include-offer": "true"}
            if cat_id: params["category-id"] = cat_id
            data = api_get(token, "/products/search", **params)
            flat = []
            extra_blocklist = DRINK_OFFTOPIC_KEYWORDS if interest == "Wijn & Drank" else None
            collect(token, data.get("results", []), flat, seen_pids, subcategory=subcat, blocklist_extra=extra_blocklist)
            for it in flat:
                buckets[interest][bucket(it["price"])].append(it)
            time.sleep(0.25)

        for budget in budgets_order:
            if any(it.get("subcategory") == subcat for it in buckets[interest][budget]):
                continue
            for page in range(SUBCAT_SEARCH_PAGES + 1, SUBCAT_SEARCH_PAGES + 4):
                params = {"search-term": term, "country-code": "NL", "page": page,
                          "page-size": 50, "sort": "POPULARITY",
                          "include-image": "true", "include-offer": "true"}
                if cat_id: params["category-id"] = cat_id
                data = api_get(token, "/products/search", **params)
                flat = []
                extra_blocklist = DRINK_OFFTOPIC_KEYWORDS if interest == "Wijn & Drank" else None
                collect(token, data.get("results", []), flat, seen_pids, want_budget=budget, subcategory=subcat, blocklist_extra=extra_blocklist)
                for it in flat:
                    buckets[interest][budget].append(it)
                time.sleep(0.25)
                if any(it.get("subcategory") == subcat for it in buckets[interest][budget]):
                    break

        after = sum(len(buckets[interest][b]) for b in budgets_order)
        print(f"  {interest} / {subcat}: +{after - before}")

    # Phase 2: popular per (sub)categorie
    for interest, cats in INTEREST_CATEGORIES.items():
        print(f"📦 {interest}", end="", flush=True)
        for cat_id, cat_label, subcat in cats:
            cat_cap = CAP_PER_SUBCAT if subcat else CAP_PER_PLAIN_CAT
            cat_count = 0
            for page in range(1, 17):
                if cat_count >= cat_cap: break
                data = api_get(token, "/products/lists/popular",
                    **{"category-id": cat_id, "country-code": "NL", "page": page,
                       "page-size": 50, "include-image": "true", "include-offer": "true"})
                items = data.get("results", [])
                if not items: break
                flat = []
                extra_blocklist = DRINK_OFFTOPIC_KEYWORDS if interest == "Wijn & Drank" else None
                collect(token, items, flat, seen_pids, subcategory=subcat, blocklist_extra=extra_blocklist)
                for it in flat:
                    buckets[interest][bucket(it["price"])].append(it)
                    cat_count += 1
                time.sleep(0.25)
        total = sum(len(buckets[interest][b]) for b in budgets_order)
        print(f" → {total} items")

    # Phase 3: fill gaps via search
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
            extra_blocklist = DRINK_OFFTOPIC_KEYWORDS if interest == "Wijn & Drank" else None
            collect(token, data.get("results", []), flat, seen_pids, want_budget=budget, blocklist_extra=extra_blocklist)
            for it in flat:
                if len(buckets[interest][budget]) >= PER_CELL: break
                buckets[interest][budget].append(it)
            time.sleep(0.25)
        print(f"  {interest} × {budget}: {have} → {len(buckets[interest][budget])}")

    # Phase 4: assemble catalog
    random.seed(99)
    products = []
    pid_counter = 1
    for interest in COLORS:
        for budget in budgets_order:
            for item in select_cell(buckets[interest][budget]):
                price = item["price"]
                price_str = f"€{int(price)}" if price == int(price) else f"€{price:.2f}".replace(".", ",")
                tag = ""
                if price >= 100 and random.random() < 0.3:
                    tag = "Premium"
                elif price < 100:
                    tag = random.choice(["", "", "", "Bestseller", "Tip", "Geliefd"])

                sub = item.get("subcategory")
                product = {
                    "id": f"b{pid_counter}",
                    "name": item["title"][:120],
                    "price": price_str,
                    "tag": tag,
                    "colorHex": random.choice(COLORS[interest]),
                    "interests": [interest],
                    "budget": budget,
                    "productId": item["bolProductId"],
                    "imageURL": item["image"],
                }
                if sub:
                    product["subcategory"] = sub
                min_age, max_age = compute_age(interest, sub, item["title"])
                if min_age is not None:
                    product["minAge"] = min_age
                if max_age is not None:
                    product["maxAge"] = max_age

                products.append(product)
                pid_counter += 1

    # Version: use yyyymmdd for traceability
    version = int(time.strftime("%Y%m%d"))
    catalog = {"version": version, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "products": products}

    out_path = sys.argv[1] if len(sys.argv) > 1 else "catalog.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    # Summary
    grid = defaultdict(lambda: defaultdict(int))
    subgrid = defaultdict(int)
    for p in products:
        for i in p["interests"]:
            grid[i][p["budget"]] += 1
        if p.get("subcategory"):
            subgrid[(p["interests"][0], p["subcategory"])] += 1
    print(f"\n✅ Catalog v{version} written to {out_path}: {len(products)} products")
    issues = sum(1 for i in COLORS for b in budgets_order if grid[i][b] < 10)
    print(f"   Coverage: {'all 10+' if issues == 0 else f'{issues} cells under 10'}")
    if subgrid:
        print("   Subcategorieën:")
        for (interest, sub), n in sorted(subgrid.items()):
            print(f"     {interest} / {sub}: {n}")


if __name__ == "__main__":
    main()
