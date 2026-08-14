"""Slår ihop crawl_all_stores.py:s butikskopplade sortiment med
crawl_national_backfill.py:s nationella katalog (AssortmentText,
IsNewInAssortment, ProductLaunchDate, samt hela Ordervaror-katalogen som
inte är meningsfullt butikskopplad).

Körs efter varje färsk crawl, innan matchning. Skriver över all_stores_wines.csv
med den fullständiga, backfillade versionen."""
import csv

with open("all_stores_wines.csv", encoding="utf-8-sig") as f:
    store_rows = list(csv.DictReader(f))

with open("national_backfill.csv", encoding="utf-8-sig") as f:
    national_rows = {r["Varunummer"]: r for r in csv.DictReader(f)}

FIELDNAMES = ["Varunummer", "Namn", "Producent", "Pris", "Volym", "Forpackning", "Kategori3",
              "Ursprung", "Druvor", "AssortmentText", "IsNewInAssortment", "ProductLaunchDate", "Argang",
              "Antal_butiker", "Butiker"]

merged = {}
for r in store_rows:
    vn = r["Varunummer"]
    nat = national_rows.get(vn, {})
    row = {
        "Varunummer": vn,
        "Namn": r["Namn"],
        "Producent": r["Producent"],
        "Pris": r["Pris"],
        "Volym": r["Volym"],
        "Forpackning": r["Forpackning"],
        "Kategori3": r["Kategori3"],
        "Ursprung": r["Ursprung"],
        "Druvor": r["Druvor"],
        "AssortmentText": nat.get("AssortmentText", r.get("AssortmentText", "")),
        "IsNewInAssortment": nat.get("IsNewInAssortment", ""),
        "ProductLaunchDate": nat.get("ProductLaunchDate", ""),
        "Argang": nat.get("Argang", ""),
        "Antal_butiker": r["Antal_butiker"],
        "Butiker": r["Butiker"],
    }
    # Ordervaror är inte meningsfullt butikskopplat (median 1 butik av
    # tillfällig sökexponering, mot median 21 för genuint lagerförda
    # kategorier) — markera som online istället för de skenbara butikerna.
    if row["AssortmentText"] == "Ordervaror":
        row["Antal_butiker"] = "Online"
        row["Butiker"] = "ONLINE"
    merged[vn] = row

added_online = 0
for vn, nat in national_rows.items():
    if vn in merged:
        continue
    if nat.get("AssortmentText") != "Ordervaror":
        continue
    merged[vn] = {
        "Varunummer": vn,
        "Namn": nat["Namn"],
        "Producent": nat["Producent"],
        "Pris": nat["Pris"],
        "Volym": nat["Volym"],
        "Forpackning": nat["Forpackning"],
        "Kategori3": nat["Kategori3"],
        "Ursprung": nat["Ursprung"],
        "Druvor": nat["Druvor"],
        "AssortmentText": nat["AssortmentText"],
        "IsNewInAssortment": nat.get("IsNewInAssortment", ""),
        "ProductLaunchDate": nat.get("ProductLaunchDate", ""),
        "Argang": nat.get("Argang", ""),
        "Antal_butiker": "Online",
        "Butiker": "ONLINE",
    }
    added_online += 1

with open("all_stores_wines.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=FIELDNAMES)
    w.writeheader()
    w.writerows(merged.values())

print(f"Klart. {len(merged)} viner totalt ({added_online} nya online-only rader tillagda).")
