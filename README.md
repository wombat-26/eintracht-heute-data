# eintracht-heute-data

Zentral gepflegte Spieldaten für die App **EintrachtHeute**. Eine GitHub
Action fragt dreimal täglich OpenLigaDB, ESPN und das DFB-Datencenter ab,
merged die Ergebnisse in `data/seed_matches.json` und veröffentlicht sie als
Release-Assets.

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
            check_abbreviations.py · canonicalize_ids.py · test_dfb_parser.py
tests/      Fixtures aus echten API-Antworten und HTML-Abzügen
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
| DFB-Datencenter | Torschützen: Bundesliga (Frauen und Männer), DFB-Pokal | beide |

Nicht abgedeckt und weiterhin nur im Seed: DFB-Pokal (Männer und Frauen),
Europapokal der Männer, Champions League der Männer. Für `dfb`, `ucl` und
`BLSupercup` gäbe es OpenLigaDB-Kürzel — bewusst noch nicht aktiviert.

## DFB-Datencenter als Zweitquelle

Das [DFB-Datencenter](https://datencenter.dfb.de) liefert Torschützen mit
ausgeschriebenen Namen, ohne Key und ohne JavaScript. Es schließt zwei
verschiedene Lücken:

- **Frauen:** OpenLigaDB führt für die Frauen-Bundesliga überhaupt keine
  Torschützinnen. Was dort steht, hat jemand von Hand eingetragen.
- **Männer:** OpenLigaDB kürzt Vornamen von Spielern ab, die nicht im
  gepflegten Bestand stehen („T. Skarke", „R. Fellhauer").
  `loese_abkuerzung()` kommt da nicht weiter, weil es nur auflösen kann, was
  der Seed schon kennt.

Abgedeckt sind laut `providers.DFB_QUELLEN` die Bundesliga (Frauen und
Männer) und der DFB-Pokal der Männer. Der Europapokal liegt nicht beim DFB,
die UWCL kommt von ESPN.

Nachteil gegenüber der Handarbeit: Das Datencenter füllt die Ereignisliste
mit Verzug. In einer Stichprobe über die ersten vier Spieltage 2026/27 lagen
Partien ab acht Tagen vollständig vor, eine zwei Tage alte noch gar nicht.
Bei drei Läufen täglich holt die Pipeline das von selbst nach.

Drei Eigenschaften, die den Zuschnitt bestimmen:

- **Legt nie ein Spiel an.** Der Provider bekommt nur Partien, die nach dem
  Merge dieses Laufs ohnehin im Seed stünden, und gibt sie unverändert mit
  gefüllter Torliste zurück. Eine abweichende Vereinsschreibweise im
  Datencenter kann damit keine Dublette erzeugen; der schlimmste Fall ist ein
  Spiel ohne Namen.
- **Fällt weich aus.** HTML-Scraping bricht, wenn der DFB sein Layout ändert.
  Fehlende Torschützinnen sind keine kaputten Daten — der Lauf wird deshalb
  nicht rot (Regel 4 zielt auf Schäden, nicht auf ausgebliebene Ergänzungen).
- **Gedeckelt auf 10 Detailseiten je Lauf** (`DFB_MAX_DETAIL`). Der
  Vereinsspielplan kostet eine Anfrage und liefert die ganze Saison mit Datum
  und Ergebnis; nur die Torliste steht auf der jeweiligen Schema-Seite.

Der Wettbewerbs-Slug der Frauen trägt den Sponsornamen
(`google-pixel-frauen-bundesliga`, davor Flyeralarm). Neuer Vertrag → in
`providers.DFB_QUELLEN` vorne ergänzen. Der Saison-Slug wird nicht gebaut,
sondern aus dem Auswahlfeld der Seite nachgezogen, denn der DFB führt drei
Formate nebeneinander: `google-pixel-frauen-bundesliga-2026-2027` bei den
Frauen, `2026-2027` bei den Männern, `2026-27` im Pokal.

Geprüft und verworfen: ESPN (kennt keine deutsche Frauenliga — nur `eng.w.1`,
`esp.w.1`, `fra.w.1`, `ned.w.1`, `aus.w.1` und die UWCL), TheSportsDB (für
Deutschland nur Bundesliga, 2. Bundesliga und die beiden DFB-Pokale),
Sofascore (403 für alles, was nicht wie ein Browser aussieht), api-football.com
(kann es, aber der kostenlose Plan ist auf ältere Saisons beschränkt).

Parser-Test ohne Netz, gegen gespeicherte Abzüge:

```bash
cd tools && python3 test_dfb_parser.py
```

## Datumsdreher

**An der Quelle behoben (Stand 15.09.2026).** OpenLigaDB datierte bei
`ffb1/2026` fünf Spieltage falsch (9, 17, 23, 24, 25) — Tag und Monat waren
vertauscht. Die Einträge sind inzwischen korrigiert, `ffb1/2026` liefert alle
26 Termine richtig, und der Filter verwirft nichts mehr.

`seedkit.spieltag_plausibilitaet()` bleibt als Netz bestehen, denn die Ursache
war nicht einmalig: Ein Importlauf kann denselben Fehler jederzeit wieder
erzeugen. Die Erkennung läuft über Anker — Spieltage mit Tag > 12 können nicht
gedreht sein und dienen als Stützstellen. Ein kippbarer Spieltag gilt als
gedreht, wenn sein Datum nicht zwischen die Nachbaranker passt, das gedrehte
aber schon.

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

## Änderungen

Nur Änderungen an der Pipeline. Die Commits des `seed-bot` stehen nicht hier —
sie sind Daten, keine Änderung am Verhalten.

### 16.09.2026 — Torschützen auch bei den Männern korrigieren

Die DFB-Quelle deckt jetzt Bundesliga (Frauen und Männer) und DFB-Pokal ab,
gesteuert über `providers.DFB_QUELLEN`. Damit werden auch abgekürzte Vornamen
aus OpenLigaDB („T. Skarke") ausgeschrieben, die `loese_abkuerzung()` nicht
auflösen kann. Nachspielzeit wird als `90+2` → 92 gelesen statt auf 90
abgeschnitten.

### 16.09.2026 — Halbstündliche Läufe während der Spiele

Drei Wochenend-Cronfenster plus vorgeschalteter `gate`-Job, der im Seed
nachsieht, ob gerade gespielt wird (`tools/live_gate.py`). Livefenster ist
Anpfiff + 15 bis Anpfiff + 165 Minuten. Der Seed-Job läuft bei einem Ausfall
des Gates trotzdem.

### 15.09.2026 — Torschützinnen aus dem DFB-Datencenter

Neue Zweitquelle `providers.dfb_frauen_bundesliga()` für die
Frauen-Bundesliga, weil OpenLigaDB dort keine Torschützinnen führt. Holt je
Saison einmal den Vereinsspielplan und danach nur für Spiele mit Torlücke die
Schema-Seite. Legt nie ein Spiel an, fällt bei Ausfall weich aus, gedeckelt
auf 10 Detailseiten je Lauf. Dazu `tools/test_dfb_parser.py` mit vier
gespeicherten HTML-Abzügen. Datumsdreher-Abschnitt auf „an der Quelle behoben"
aktualisiert.

### 01.09.2026 — Heartbeat

`heartbeat.yml`, damit GitHub den Cron nicht wegen Inaktivität des Repos
abschaltet.

### 21.08.2026 — Pokal, Namen, dritter Lauf

DFB-Pokal der Männer (`dfb`) mit abgefragt. Abgekürzte Vornamen werden über
den Bestand aufgelöst (`loese_abkuerzung()`), und der Merge überschreibt
gepflegte Namen nicht mehr, ersetzt aber Abkürzungen und wachsende Torlisten
laufender Spiele. TSV 1860 München der Seed-Schreibweise zugeordnet. Dritter
Cron-Lauf um 21:20, damit Abendspiele nicht bis Mitternacht brachliegen.

### 09.08.2026 — Handgepflegte Torschützennamen

Die von Hand vereinheitlichten Namen aus eintracht-archiv.de in den Seed
übernommen.

### 07.08.2026 — Erste Fassung

Datenmodell, Provider für OpenLigaDB und ESPN, Merge- und Validierungslogik,
GitHub Action, Release-Assets. Seed-IDs kanonisiert.
