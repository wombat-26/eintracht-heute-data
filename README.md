# eintracht-heute-data

Zentral gepflegte Spieldaten für die App **EintrachtHeute**. Eine GitHub
Action fragt zweimal täglich OpenLigaDB und ESPN ab, merged die Ergebnisse in
`data/seed_matches.json` und veröffentlicht sie als Release-Assets.

Zweck: Neuinstallationen bringen alle vergangenen Spiele mit, und nicht jedes
Gerät fragt die Quell-APIs einzeln ab.

## Download-URLs (stabil)

| Datei | Zweck | Größe |
| --- | --- | --- |
| [`manifest.json`](../../releases/latest/download/manifest.json) | Versionsprüfung, täglich | ~500 B |
| [`window.json.gz`](../../releases/latest/download/window.json.gz) | letzte 120 Tage + alle Zukunftsspiele | ~2,8 KB |
| [`seed_matches.json.gz`](../../releases/latest/download/seed_matches.json.gz) | Vollstand, nur als Notnagel | ~209 KB |

Die Assets hängen an einem Release mit festem Tag `data` und werden bei jedem
Lauf ersetzt — die URLs bleiben dadurch stabil.

## Aufbau

```
data/       seed_matches.json (Wahrheitsquelle) + generierte Artefakte
tools/      seedkit.py · providers.py · update_seed.py · validate_seed.py
tests/      Fixtures aus echten API-Antworten
```

## Sechs Regeln, an die sich die Pipeline hält

1. **Nie neu generieren, nur ergänzen.** Der Seed enthält Daten aus
   eintracht-archiv.de (3.484 Quell-Links, Torschützennamen, Notizen), die
   keine API liefert.
2. **Upsert-Semantik der App exakt spiegeln** (`seedkit.upsert` ↔
   `MatchStore.applyUpsert`). Ändert sich das Swift, muss das Python nachziehen.
3. **Bestehende IDs niemals umschreiben.** 215 Seed-IDs sind nicht aus den
   heutigen Teamnamen reproduzierbar; eine Kanonisierung hinterließe in jeder
   Installation Dubletten.
4. **Abbruch statt Notfallreparatur.** Validierungsfehler oder ein Rückgang um
   mehr als 5 Spiele → kein Commit, Job rot.
5. **Kein Commit ohne inhaltliche Änderung.**
6. **`gender` aus dem Liga-Kürzel**, nie aus der API geraten.

## Quellen

| Kürzel | Wettbewerb | gender |
| --- | --- | --- |
| `bl1` | Bundesliga Männer | men |
| `ffb1` | Frauen-Bundesliga | women |
| `wsc` | DFB Frauen Supercup | women |
| ESPN `uefa.wchampions[_qual]` | UEFA Women's Champions League | women |

Nicht abgedeckt und weiterhin nur im Seed: DFB-Pokal (Männer und Frauen),
Europapokal der Männer, Champions League der Männer. Für `dfb`, `ucl` und
`BLSupercup` gäbe es OpenLigaDB-Kürzel — bewusst noch nicht aktiviert.

## Datumsdreher

OpenLigaDB datiert bei `ffb1/2026` fünf Spieltage falsch (9, 17, 23, 24, 25) —
Tag und Monat sind vertauscht. `seedkit.spieltag_plausibilitaet()` erkennt das
über Anker: Spieltage mit Tag > 12 können nicht gedreht sein und dienen als
Stützstellen. Ein kippbarer Spieltag gilt als gedreht, wenn sein Datum nicht
zwischen die Nachbaranker passt, das gedrehte aber schon.

**Ein reiner Monotonie-Test genügt nicht** — springt ein Dreher nach vorn
(07.02. → 02.07.), erscheinen alle folgenden, korrekten Spieltage als Verstoß.
Gegen die echten Daten hätte das sechs echte Termine verworfen und zwei
Geister durchgelassen.

## Lokal ausführen

```bash
cd tools
python3 update_seed.py --seed ../data/seed_matches.json --out-dir ../data --dry-run
python3 validate_seed.py ../data/seed_matches.json --min-count 3500
python3 update_seed.py --offline-fixture ../tests/fixtures/fetched_ffb1.json \
                       --seed ../data/seed_matches.json --out-dir /tmp/out
```

Einmalig nach dem Anlegen: Action manuell über *Actions → Seed aktualisieren →
Run workflow* starten und den ersten Diff von Hand prüfen, bevor der Cron läuft.
