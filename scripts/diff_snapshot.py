"""Jämför en tidigare sortiments-snapshot mot den nya (efter merge_online_backfill.py)
och loggar förändringar till data/history.csv. Skriver också ut en lista över
Varunummer som är nya sedan förra körningen (för Vivino-matchning) till
pending_match.json — förutom de som redan har en matchning i
vivino_matches.csv (t.ex. ett vin som cyklat ut och kommit tillbaka; se
merge_master.py:s Aktiv-hantering, som är anledningen till att det inte
behöver matchas om).

Användning: diff_snapshot.py <previous_all_stores_wines.csv> <new_all_stores_wines.csv>
"""
import csv
import json
import sys
from datetime import date, timezone, datetime

if len(sys.argv) != 3:
    print("Usage: diff_snapshot.py <previous.csv> <new.csv>")
    sys.exit(1)

prev_path, new_path = sys.argv[1], sys.argv[2]
today = date.today().isoformat()

with open(prev_path, encoding="utf-8-sig") as f:
    prev = {r["Varunummer"]: r for r in csv.DictReader(f)}

with open(new_path, encoding="utf-8-sig") as f:
    new = {r["Varunummer"]: r for r in csv.DictReader(f)}

changes = []

for vn, r in new.items():
    if vn not in prev:
        changes.append({
            "datum": today, "varunummer": vn, "namn": r["Namn"], "typ": "ny",
            "fran": "", "till": r.get("AssortmentText", ""),
        })
        continue
    old_r = prev[vn]
    if old_r.get("AssortmentText") != r.get("AssortmentText"):
        changes.append({
            "datum": today, "varunummer": vn, "namn": r["Namn"], "typ": "sortiment_andrat",
            "fran": old_r.get("AssortmentText", ""), "till": r.get("AssortmentText", ""),
        })
    try:
        old_price, new_price = float(old_r.get("Pris") or 0), float(r.get("Pris") or 0)
        if old_price and new_price and abs(old_price - new_price) > 0.01:
            changes.append({
                "datum": today, "varunummer": vn, "namn": r["Namn"], "typ": "pris_andrat",
                "fran": old_r.get("Pris", ""), "till": r.get("Pris", ""),
            })
    except ValueError:
        pass

for vn, r in prev.items():
    if vn not in new:
        changes.append({
            "datum": today, "varunummer": vn, "namn": r["Namn"], "typ": "borttagen",
            "fran": r.get("AssortmentText", ""), "till": "",
        })

fieldnames = ["datum", "varunummer", "namn", "typ", "fran", "till"]
try:
    with open("history.csv", "r", encoding="utf-8-sig") as f:
        pass
    write_header = False
except FileNotFoundError:
    write_header = True

with open("history.csv", "a", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if write_header:
        w.writeheader()
    w.writerows(changes)

try:
    with open("vivino_matches.csv", encoding="utf-8-sig") as f:
        already_matched = {r["Varunummer"] for r in csv.DictReader(f)}
except FileNotFoundError:
    already_matched = set()

new_varunummer_all = [vn for vn in new if vn not in prev]
new_varunummer = [vn for vn in new_varunummer_all if vn not in already_matched]
skipped_already_matched = len(new_varunummer_all) - len(new_varunummer)

with open("pending_match.json", "w", encoding="utf-8") as f:
    json.dump(new_varunummer, f, ensure_ascii=False, indent=2)

from collections import Counter
counts = Counter(c["typ"] for c in changes)
print(f"Diff klar: {len(changes)} förändringar ({dict(counts)}). "
      f"{len(new_varunummer)} nya viner skrivna till pending_match.json för matchning "
      f"({skipped_already_matched} redan matchade sedan tidigare, t.ex. återkomna viner, hoppas över).")
