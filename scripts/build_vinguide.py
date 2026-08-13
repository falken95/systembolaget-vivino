import csv, json, sys
from datetime import date, timedelta

csv_path = sys.argv[1]
stores_path = sys.argv[2] if len(sys.argv) > 2 else None
html_template_path = r"C:\Users\carlp\AppData\Local\Temp\claude\C--Users-carlp\c454c059-8531-49d4-ad42-b4c02d333c24\scratchpad\vinguide_template.html"
out_path = r"C:\Users\carlp\AppData\Local\Temp\claude\C--Users-carlp\c454c059-8531-49d4-ad42-b4c02d333c24\scratchpad\vinguide.html"

with open(csv_path, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

def to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None

def to_int(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None

def to_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes")

NEW_WINDOW_DAYS = 30
_today = date.today()
_new_cutoff = _today - timedelta(days=NEW_WINDOW_DAYS)

def is_new(launch_date_str):
    # Nyhetstaggen styrs av ProductLaunchDate (finns för de flesta viner),
    # inte Systembolagets egen IsNewInAssortment-flagga — den senare täcker
    # bara en bråkdel (25 av 10 686 vid senaste crawl) och har en okänd,
    # icke-kontrollerbar definition. Med lanseringsdatum kan vi garantera
    # exakt "ny i 1 månad"-fönstret användaren bad om.
    d = (launch_date_str or "").strip()
    if not d:
        return False
    try:
        y, m, day = map(int, d.split("-")[:3])
        return date(y, m, day) >= _new_cutoff
    except (ValueError, IndexError):
        return False

EXCLUDED_PACKAGING = {"Box", "Påse", "Pappförpackning", "PET-flaska"}

wines = []
seen_site_ids = set()
skipped_box = 0
for r in rows:
    if r.get("Forpackning") in EXCLUDED_PACKAGING or r.get("Kategori3") == "Smaksatt":
        skipped_box += 1
        continue
    grapes_raw = r.get("Druvor") or ""
    butiker_raw = (r.get("Butiker") or "").strip()
    if butiker_raw == "ONLINE":
        butiker = ["ONLINE"]
    elif butiker_raw:
        butiker = butiker_raw.split(",")
        seen_site_ids.update(butiker)
    else:
        butiker = []
    wines.append({
        "varunummer": r.get("Varunummer") or None,
        "namn": r.get("Namn", ""),
        "producent": r.get("Producent", ""),
        "pris": to_float(r.get("Pris")),
        "ursprung": r.get("Ursprung", ""),
        "druvor": [g.strip() for g in grapes_raw.split(",") if g.strip()],
        "betyg": to_float(r.get("Vivino_betyg")),
        "recensioner": to_int(r.get("Vivino_antal_recensioner")),
        "url": r.get("Vivino_url") or None,
        "butiker": butiker,
        "ny": is_new(r.get("ProductLaunchDate")),
        "tillfalligt": r.get("AssortmentText") == "Tillfälligt sortiment",
        "lanserad": (r.get("ProductLaunchDate") or "").strip() or None,
    })

stores = []
if stores_path:
    with open(stores_path, encoding="utf-8") as f:
        all_sites = json.load(f)
    by_id = {s["siteId"]: s for s in all_sites}
    for site_id in seen_site_ids:
        s = by_id.get(site_id)
        if s:
            stores.append({"id": site_id, "city": s["city"].title(), "name": s["displayName"]})
    stores.sort(key=lambda s: (s["city"], s["name"]))

data_json = json.dumps(wines, ensure_ascii=False)
stores_json = json.dumps(stores, ensure_ascii=False)

with open(html_template_path, encoding="utf-8") as f:
    html = f.read()

html = html.replace("const WINES = __WINE_DATA__;", f"const WINES = {data_json};")
html = html.replace("const STORES = __STORE_DATA__;", f"const STORES = {stores_json};")

with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

matched = sum(1 for w in wines if w["betyg"] is not None)
print(f"Embedded {len(wines)} wines ({matched} matched, {len(stores)} stores) into {out_path} (skipped {skipped_box} box wines)")
