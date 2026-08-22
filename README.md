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
| `national_backfill.csv` | Rå nationell katalog (AssortmentText, IsNewInAssortment, ProductLaunchDate, Argång, nationella lagerstatusflaggor, hela Ordervaror) | Skrivs över varje körning |
| `vivino_matches.csv` | Kanonisk lista över Vivino-matchade viner (Varunummer, url, betyg, recensioner, matched_date) | Nya rader läggs till varje körning |
| `vivino_prices.csv` | Vivinos marknadspris (kr/750ml, normaliserat för flaskstorlek) för matchade viner — saknas för de flesta vardagsviner utan aktiv återförsäljarmarknad på Vivino | Skrivs över varje körning |
| `master_wines.csv` | Färdig sammanslagning av ovanstående — det webbappen bygger från. Kumulativ: viner som cyklar ut ur sortimentet TAS INTE BORT, de behålls med `Aktiv=False` (döljs i appen av `build_vinguide.py`, men finns kvar med sin Vivino-matchning intakt om de kommer tillbaka) | Byggs om varje körning, men bevarar utgångna rader från förra körningen |
| `history.csv` | Append-only förändringslogg (nya/borttagna viner, sortiments- och prisändringar) | Nya rader läggs till varje körning |
| `stores.json` | Butikslista (namn, stad, id) från AlexGustafsson/systembolaget-api-data | Uppdateras vid behov |
| `wines_full_store.csv`, `national_match_results.csv` | Historiska matchningskörningar (Brommaplan resp. riksomfattande batch) — ersatta av `vivino_matches.csv`, sparade för spårbarhet | Statiska |

## Veckovis körning (pipeline)

Körs av ett schemalagt Claude-jobb (se `.claude`-rutinen "Systembolaget weekly refresh"). Ordning:

1. **`git pull`**, sedan **spara föregående snapshot**: `cp data/all_stores_wines.csv /tmp/prev.csv`
2. **Crawl**: kör `scripts/crawl_all_stores.py` (alla 451 butiker) och `scripts/crawl_national_backfill.py` i `data/`-mappen. `crawl_all_stores.py` kan ta längre än molnsessionens tidsgräns och bli avbruten mitt i (sett 2026-08-14: en körning dödades efter 100+ minuter) — därför är den återupptagningsbar: den hoppar över butiker som redan finns i `all_stores_progress.json`, och committar+pushar framsteg var 25:e butik, så nästa körning (schemalagd eller manuell) kan fortsätta istället för att börja om. Om crawlen inte hinner klart under en körning, avbryts pipelinen efter crawl-steget (se rutinens prompt) — diff/matchning/publicering körs bara mot en komplett butikscrawl.
3. **Slå ihop**: `scripts/merge_online_backfill.py` — bygger den fullständiga `all_stores_wines.csv` (butikskopplat + online-katalog + AssortmentText/IsNewInAssortment/ProductLaunchDate)
4. **Diffa**: `scripts/diff_snapshot.py /tmp/prev.csv data/all_stores_wines.csv` — loggar förändringar till `data/history.csv`, skriver Varunummer som är nya sedan förra körningen till `data/pending_match.json` — MINUS de som redan har en rad i `vivino_matches.csv` (t.ex. ett vin som cyklat ut och kommit tillbaka; se `Aktiv`-hanteringen nedan, det är därför de inte behöver matchas om)
5. **Matcha nya viner mot Vivino**: för varje Varunummer i `pending_match.json`, använd WebSearch enligt samma metod som den ursprungliga matchningen (se "Matchningsmetod" nedan) och lägg till resultatet i `data/vivino_matches.csv`
6. **Hämta Vivino-marknadspris**: `scripts/fetch_vivino_prices.py` (kört i `data/`-mappen) — skriver om `data/vivino_prices.csv` för alla matchade viner. Tar ~45 min för hela matchningslistan (artighetsfördröjning mot Vivino); de flesta viner får ingen rad (ingen aktiv återförsäljarmarknad på Vivino) och det är förväntat.
7. **Bygg om master**: `scripts/merge_master.py` — normaliserar Systembolagets pris till kr/750ml (hanterar flerflaskeförpackningar), räknar ut `Rabatt_procent` mot Vivino-marknadspriset, och bevarar utgångna viner från föregående `master_wines.csv` som `Aktiv=False` istället för att droppa dem
8. **Bygg om webbappen**: `scripts/build_vinguide.py data/master_wines.csv data/stores.json` → `vinguide.html` (viner med `Aktiv=False` exkluderas från webbappens data helt)
9. **Publicera**: uppdatera Artifact-sidan (samma URL som tidigare, se rutinens prompt)
10. **Committa och pusha**: `git add data/ && git commit -m "Veckovis uppdatering YYYY-MM-DD" && git push`

## Fyndpris-taggen

Viner där Systembolagets pris (normaliserat till kr/750ml) ligger ≥30% under Vivinos marknadspris — och där marknadspriset bygger på minst 2 aktiva återförsäljarpriser — taggas "Fyndpris" i webbappen (se `RABATT_THRESHOLD_PCT`/`RABATT_MIN_PRISPUNKTER` i `build_vinguide.py`). Vivinos prisdata täcker bara en bråkdel av sortimentet — mest viner med en aktiv andrahands-/samlarmarknad, inte vardagsviner.

## Utgångna viner (Aktiv-flaggan)

`scripts/crawl_all_stores.py` skriver bara över `all_stores_wines.csv` med det som faktiskt hittas i den aktuella crawlen — ett vin som inte längre säljs någonstans (butik eller online) finns helt enkelt inte med. Utan vidare hantering skulle `merge_master.py` (som bygger `master_wines.csv` direkt från `all_stores_wines.csv`) tappa bort sådana viner helt, och om de dyker upp i sortimentet igen senare skulle de se ut som helt nya viner — trots att de redan har en giltig Vivino-matchning liggande i `vivino_matches.csv`, vilket skulle slösa bort en ny (och möjligen misslyckad) WebSearch-matchning i onödan.

Istället bevarar `merge_master.py` viner som fanns i föregående körnings `master_wines.csv` men saknas i den nya `all_stores_wines.csv`: de skrivs med sin senast kända Systembolagets-data och `Aktiv=False`. `build_vinguide.py` exkluderar sedan `Aktiv=False`-rader helt från webbappens data — de är alltså kvar i `master_wines.csv` (bevarade, matchningen intakt) men syns inte i appen förrän/om de dyker upp i en crawl igen, då de skrivs om med `Aktiv=True` och färsk data.

**Känd bieffekt att hålla koll på**: eftersom `all_stores_wines.csv`s online-del (Ordervaror) byggs om från grunden varje körning (crawl_all_stores.py fångar bara butiksdata; merge_online_backfill.py lägger till ALLA aktuella Ordervaror-viner som inte redan täcks), och den nationella crawlens exakta täckning av Ordervaror-katalogen kan variera lite mellan körningar, dyker en betydande andel av samma Ordervaror-viner upp som "ny"/"borttagen" i `history.csv` varje vecka utan att sortimentet egentligen svänger så mycket. De faktiskt intressanta förändringarna (butiksnivå: Tillfälligt/Fast sortiment/Lokalt & Småskaligt) är typiskt en mycket mindre delmängd av totalen — filtrera på `AssortmentText` i `history.csv` om du vill se den verkliga bilden.

## Matchningsmetod

Två-variant WebSearch-sökning per vin:
1. `{Producent} {Namn} vivino`
2. Om inget bra resultat: `{Namn} vivino` (Systembolagets Producent-fält är ofta en importör, inte den faktiska vinerien som Vivino indexerar under)

Kandidat-ID:n extraheras från `vivino.com/.../w/(\d+)`-länkar i sökresultaten, hämtas via `get_vivino_wine_by_id` i `scripts/systembolaget_vivino.py`, och valideras med `candidate_accepted` (som i sin tur använder `producer_match`, `distinctive_words_subset_match`, `country_match`, `_dominant_grape_present`). **Ändra aldrig valideringslogiken för att tvinga fram fler matchningar** — en obekräftad matchning ska lämnas tom, inte gissas.

Given att bara ett fåtal nya viner tillkommer per vecka (senast sett: ~25 st i `IsNewInAssortment`-fönstret) är detta en liten, billig körning jämfört med den ursprungliga engångsmatchningen av hela sortimentet.
