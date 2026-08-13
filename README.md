# systembolaget-vivino

Korskör hela Systembolagets vinsortiment mot Vivino-betyg, per butik eller online.
Publiceras som en interaktiv webbsida (Claude Artifact).

## Struktur

- `scripts/` — crawl, matchning och byggscript
- `templates/vinguide_template.html` — HTML/JS-mall för webbappen
- `data/` — sortimentsdata, matchningar och förändringshistorik

## Filer i `data/`

| Fil | Innehåll | Uppdateras |
|---|---|---|
| `all_stores_wines.csv` | Hela sortimentet, butikskopplat (Varunummer → Butiker-lista/"ONLINE") | Skrivs över varje körning |
| `national_backfill.csv` | Rå nationell katalog (AssortmentText, IsNewInAssortment, ProductLaunchDate, hela Ordervaror) | Skrivs över varje körning |
| `vivino_matches.csv` | Kanonisk lista över Vivino-matchade viner (Varunummer, url, betyg, recensioner, matched_date) | Nya rader läggs till varje körning |
| `master_wines.csv` | Färdig sammanslagning av ovanstående — det webbappen bygger från | Byggs om varje körning |
| `history.csv` | Append-only förändringslogg (nya/borttagna viner, sortiments- och prisändringar) | Nya rader läggs till varje körning |
| `stores.json` | Butikslista (namn, stad, id) från AlexGustafsson/systembolaget-api-data | Uppdateras vid behov |
| `wines_full_store.csv`, `national_match_results.csv` | Historiska matchningskörningar (Brommaplan resp. riksomfattande batch) — ersatta av `vivino_matches.csv`, sparade för spårbarhet | Statiska |

## Veckovis körning (pipeline)

Körs av ett schemalagt Claude-jobb (se `.claude`-rutinen "Systembolaget weekly refresh"). Ordning:

1. **Spara föregående snapshot**: `cp data/all_stores_wines.csv /tmp/prev.csv`
2. **Färsk crawl**: kör `scripts/crawl_all_stores.py` och `scripts/crawl_national_backfill.py` i `data/`-mappen (skriv över `all_stores_progress.json` — börja alltid om från noll, inte resume, vid en schemalagd körning)
3. **Slå ihop**: `scripts/merge_online_backfill.py` — bygger den fullständiga `all_stores_wines.csv` (butikskopplat + online-katalog + AssortmentText/IsNewInAssortment/ProductLaunchDate)
4. **Diffa**: `scripts/diff_snapshot.py /tmp/prev.csv data/all_stores_wines.csv` — loggar förändringar till `data/history.csv`, skriver nya Varunummer till `data/pending_match.json`
5. **Matcha nya viner mot Vivino**: för varje Varunummer i `pending_match.json`, använd WebSearch enligt samma metod som den ursprungliga matchningen (se "Matchningsmetod" nedan) och lägg till resultatet i `data/vivino_matches.csv`
6. **Bygg om master**: `scripts/merge_master.py`
7. **Bygg om webbappen**: `scripts/build_vinguide.py data/master_wines.csv data/stores.json` → `vinguide.html`
8. **Publicera**: uppdatera Artifact-sidan (samma URL som tidigare, se rutinens prompt)
9. **Committa och pusha**: `git add data/ && git commit -m "Veckovis uppdatering YYYY-MM-DD" && git push`

## Matchningsmetod

Två-variant WebSearch-sökning per vin:
1. `{Producent} {Namn} vivino`
2. Om inget bra resultat: `{Namn} vivino` (Systembolagets Producent-fält är ofta en importör, inte den faktiska vinerien som Vivino indexerar under)

Kandidat-ID:n extraheras från `vivino.com/.../w/(\d+)`-länkar i sökresultaten, hämtas via `get_vivino_wine_by_id` i `scripts/systembolaget_vivino.py`, och valideras med `candidate_accepted` (som i sin tur använder `producer_match`, `distinctive_words_subset_match`, `country_match`, `_dominant_grape_present`). **Ändra aldrig valideringslogiken för att tvinga fram fler matchningar** — en obekräftad matchning ska lämnas tom, inte gissas.

Given att bara ett fåtal nya viner tillkommer per vecka (senast sett: ~25 st i `IsNewInAssortment`-fönstret) är detta en liten, billig körning jämfört med den ursprungliga engångsmatchningen av hela sortimentet.
