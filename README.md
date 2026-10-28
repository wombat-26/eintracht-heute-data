# eintracht-heute-data

Zentral gepflegte Spieldaten für die App **EintrachtHeute**. Eine GitHub
Action fragt dreimal täglich OpenLigaDB, ESPN, den DFB-Spielplan und Wikidata
(für ausgeschriebene Torschützennamen) ab, merged die Ergebnisse in `data/seed_matches.json` und veröffentlicht sie als
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
tools/      seedkit.py · providers.py · wikidata_namen.py · update_seed.py
            validate_seed.py · check_abbreviations.py · canonicalize_ids.py
            test_dfb_parser.py · test_wikidata_namen.py · test_sportschau.py
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
| ESPN `uefa.w.europa` | UEFA Women's Europa Cup (im Seed `europacup`) | women |
| Wikidata | Vornamen abgekürzter Torschützen („T. Moffi“ → „Terem Moffi“) | beide |
| eintracht-archiv.de | Quellenlinks (`sourceUrl`), 1. und 15. des Monats | beide |
| DFB-Datencenter | DFB-Pokal der Frauen (Primärquelle, legt Spiele an) | women |
| Sportschau | Abgleich von Termin und Ergebnis des aktuellen Spieltags, nur Meldung | beide |

Nicht abgedeckt und weiterhin nur im Seed: DFB-Pokal (Männer und Frauen),
Europapokal der Männer, Champions League der Männer. Für `dfb`, `ucl` und
`BLSupercup` gäbe es OpenLigaDB-Kürzel — bewusst noch nicht aktiviert.

## Wikidata: abgekürzte Torschützennamen

OpenLigaDB kürzt Vornamen von Spielern ab, die nicht in seinem Bestand
stehen („T. Moffi“, „T. Skarke“). `providers.loese_abkuerzung()` hilft nur,
wenn der Seed den Namen schon kennt. Für den Rest fragt `wikidata_namen.py`
den [Query Service von Wikidata](https://query.wikidata.org) (Daten unter
CC0, für automatisierte Abfragen gedacht, mit identifizierendem
User-Agent).

Die Stufe läuft in `update_seed.py` nach dem Merge, auf Spielen ab
`WIKIDATA_AB_SAISON` (2026/27) mit mindestens einem abgekürzten Namen,
neueste zuerst. Je Nachname und Geschlecht eine Anfrage:

```sparql
SELECT ?spieler ?spielerLabel ?geburt ?vereinLabel ?von ?bis WHERE {
  SERVICE wikibase:mwapi {
    bd:serviceParam wikibase:endpoint "www.wikidata.org" ;
                    wikibase:api "Search" ;
                    mwapi:srsearch "Moffi haswbstatement:P106=Q937857" ;
                    mwapi:srlimit "max" .
    ?spieler wikibase:apiOutputItem mwapi:title .
  }
  ?spieler wdt:P21 wd:Q6581097 .                      # männlich; Q6581072 weiblich
  OPTIONAL { ?spieler wdt:P569 ?geburt }
  OPTIONAL { ?spieler p:P54 ?st . ?st ps:P54 ?verein .
             OPTIONAL { ?st pq:P580 ?von } OPTIONAL { ?st pq:P582 ?bis } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,mul,en". }
}
```

Ein Kandidat ist, wessen Bezeichnung mit der Initiale beginnt und auf den
Nachnamen endet (ohne Akzente, ohne Groß/Klein) und der am Spieltag zwischen
15 und 45 Jahre alt war. Übernommen wird dann in zwei Stufen:

1. **Verein:** Genau ein Kandidat war am Spieltag für die Mannschaft
   gemeldet, für die das Tor zählt (`P54` mit `P580`/`P582`; bei Eigentoren
   der Gegner). Im Protokoll steht `Q…, Verein`.
2. **Nur Name:** Wikidata kennt den Verein nicht oder nicht datiert. Namensvettern,
   die Wikidata am Spieltag nachweislich bei einem anderen Verein führt
   (abgeschlossener Zeitraum, der den Spieltag einschließt; Auswahlmannschaften
   zählen nicht) oder deren Vereinsstationen alle davor enden, scheiden aus.
   Bleibt genau ein Kandidat übrig, wird er übernommen. Im Protokoll
   steht `Q…, nur Name`. Diese Stufe ist nötig, weil die Vereinsstationen
   bei Wikidata lückenhaft sind: In einer Stichprobe führte Wikidata für
   Derry Scherhant gar keinen Verein, für Tim Skarke nur Heidenheim und für
   Rebecka Blomqvist nur Häcken. `OHNE_VEREINSBELEG = False` schaltet sie ab.

Alles andere bleibt abgekürzt und steht als `PRUEFEN` im Protokoll: mehrere
Kandidaten, Namensvetter beim selben Verein, Doppelinitialen („K.-P.“),
Nachnamen mit Sonderzeichen. Ein abgekürzter, aber richtiger Name ist
harmloser als ein geratener falscher.

- **Fällt weich aus.** Antwortet Wikidata nicht (Netz, Drosselung, Timeout),
  versucht der Lauf es nicht weiter; die Namen bleiben abgekürzt und der Lauf
  wird nicht rot.
- **Gedeckelt auf 15 Anfragen je Lauf** (`MAX_ABFRAGEN`), eine Sekunde
  Pause dazwischen. Antworten werden innerhalb eines Laufs zwischengespeichert.
- **Hält über spätere Läufe.** `upsert()` ersetzt die Torliste eines
  beendeten Spiels nicht durch eine mit mehr Abkürzungen. Bei laufenden
  Spielen holt jeder Lauf die Liste neu und löst sie neu auf.
- **Ältere Saisons bleiben unberührt.** Ihre Namen stammen kuratiert aus
  eintracht-archiv.de.

Test ohne Netz, mit gespeicherter Antwort für „Moffi“ und gebauten
Antworten für jeden Fall der Regeln, dazu ein Durchlauf von
`update_seed.main()`:

```bash
cd tools && python3 test_wikidata_namen.py
```

## DFB-Datencenter: nur noch der Pokal der Frauen

Das [DFB-Datencenter](https://datencenter.dfb.de) wird nur noch für den
**DFB-Pokal der Frauen** gelesen (`providers.dfb_spiele`), auf der
Vereinsspielplan-Seite, eine Anfrage je Saison. Es ist Primärquelle, weil
OpenLigaDB den Pokal nicht führt, ESPN keine deutsche Frauenliga kennt und
eintracht-archiv.de erst Tage später nachträgt, und legt Spiele an. Den
Abgleich von Termin und Ergebnis der Bundesliga übernimmt seit dem 28.10.2026
Sportschau, siehe unten.

**Torschützen holt die Pipeline dort seit dem 28.10.2026 nicht mehr.** Die
Nutzungsbedingungen des Datencenters untersagen Datencrawling und
Datenscraping; deshalb liest die Pipeline keine Detailseiten einzelner Spiele
mehr. Folge: Für die **Frauen** gibt es keine automatische
Torschützinnen-Quelle mehr. OpenLigaDB führt dort keine, und
Wikidata kann nur vorhandene Namen ausschreiben, nicht fehlende ergänzen. Was
in der Ereignisliste steht, kommt von eintracht-archiv.de.

Der Pokal-Slug ist nicht zu erraten (`dfb-pokal-1132`, eingetragen in
`providers.DFB_PRIMAERQUELLEN`). Der Saison-Slug wird nicht gebaut, sondern
aus dem Auswahlfeld der Seite nachgezogen; der Pokal nutzt das Format
`2026-27`.

HTML bricht, wenn der DFB sein Layout ändert. Ein Ausfall ist deshalb immer
nur ein Hinweis im Protokoll (Regel 4 zielt auf kaputte Daten, nicht auf
ausgebliebene Ergänzungen).

Geprüft und verworfen für Torschützinnen: ESPN (kennt keine deutsche
Frauenliga — nur `eng.w.1`, `esp.w.1`, `fra.w.1`, `ned.w.1`, `aus.w.1` und
die UWCL), TheSportsDB (für Deutschland nur Bundesliga, 2. Bundesliga und
die beiden DFB-Pokale), Sofascore (403 für alles, was nicht wie ein Browser
aussieht), api-football.com (kann es, aber der kostenlose Plan ist auf ältere
Saisons beschränkt).

Spielplan-Parser, ohne Netz, gegen einen gespeicherten Abzug:

```bash
cd tools && python3 test_dfb_parser.py
```

## Sportschau: Abgleich von Termin und Ergebnis

OpenLigaDB ist bei den Frauen unzuverlässig (falsche Termine, vertauschter Tag
und Monat, verspätete Ansetzungen). Deshalb vergleicht `update_seed.py` die
Spiele von `ffb1` und `bl1` mit der Spielübersicht von
[sportschau.de](https://www.sportschau.de) (`providers.sportschau_abgleich`):

| Liga | Seite |
| --- | --- |
| Bundesliga Männer | `/live-und-ergebnisse/fussball/deutschland-bundesliga/spiele-und-ergebnisse` |
| Bundesliga Frauen | `/live-und-ergebnisse/fussball/deutschland-bundesliga-frauen/spiele-und-ergebnisse` |

- **Zwei Anfragen je Lauf**, eine je Liga, mit dem User-Agent
  `EintrachtHeute-SeedBot/1.0 (+github.com/wombat-26)`. Keine Detailseiten und
  nichts unter `/live-und-ergebnisse/widget/`, das die `robots.txt` sperrt.
- **Nur der aktuelle Spieltag.** Die Seite zeigt einen Spieltag; welchen, steht
  im Titel („8. Spieltag - 2026/2027“). Geprüft werden Termin (Anstoß in UTC,
  umgerechnet nach Berlin), Ergebnis (erst bei „Beendet“) und Paarung. Der Seed
  führt je Spieltag nur die Eintracht-Partie, verglichen wird also eine Partie
  je Liga. Frühere Spieltage prüft nichts mehr; der DFB-Abgleich lief über die
  ganze Saison.
- **Nur Meldung.** Abweichungen stehen als `PRUEFEN` im Protokoll und als
  Tabelle in der Job-Zusammenfassung. Der Seed bleibt unverändert, die App
  fragt OpenLigaDB selbst ab.
- **Keine Torschützen.** Dafür bräuchte es je Spiel eine Detailseite.
- Fällt Sportschau aus, ändert sich das Layout oder gehört die Seite zu einer
  anderen Saison, steht ein Hinweis im Protokoll und der Lauf läuft weiter.

Stand 28.10.2026: Die `robots.txt` erlaubt den Abruf. Sie sperrt KI-Trainings-Bots,
das trifft hier nicht zu, denn die Daten werden verglichen, nicht gesammelt. Eigene
Nutzungsbedingungen für die Ergebnisseiten habe ich nicht gefunden; die Zahlen
stammen vom Datenanbieter hs-data.com, den die ARD lizenziert. Bleibt es
bei zwei Anfragen je Lauf, ist die Last gering. Lässt sich der Abruf nicht
mehr halten, genügt es, `ABGLEICH` in `update_seed.py` zu leeren.

Parser und Abgleich, ohne Netz, gegen gespeicherte Ausschnitte:

```bash
cd tools && python3 test_sportschau.py
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

### 28.10.2026 — Sportschau statt DFB für den Abgleich

Der Abgleich von Termin und Ergebnis liest nicht mehr das DFB-Datencenter,
sondern die Spielübersichten von sportschau.de
(`providers.sportschau_abgleich`), siehe oben. Das Datencenter bleibt nur für
den Pokal der Frauen. Entfernt sind `providers.dfb_abgleich` und
`DFB_QUELLEN`; `dfb_abweichungen` heißt jetzt `abweichungen` und vergleicht
gegen jede Zweitquelle. Neu sind `tools/test_sportschau.py` und zwei
Ausschnitte in `tests/fixtures`.

Geprüft wird nur noch der aktuelle Spieltag, nicht mehr die ganze Saison.

### 28.10.2026 — Namen über Wikidata statt Torschützen vom DFB

Abgekürzte Torschützennamen aus OpenLigaDB („T. Moffi“) schreibt jetzt
`wikidata_namen.py` über den Query Service von Wikidata aus, siehe oben. Neu
sind `tools/wikidata_namen.py`, `tools/test_wikidata_namen.py` und die Stufe
in `update_seed.py`.

Entfernt ist jeder Abruf von Torschützen im DFB-Datencenter:
`providers.dfb_torschuetzen`, `dfb_tore`, `tore_abweichungen` und
`dfb_spiel_urls`, die Torprüfung der Frauen-Bundesliga samt
`data/dfb_torpruefung.json` und der Workflow-Zweig dafür, die vier
Detailseiten-Abzüge in `tests/fixtures` und die Tests dazu. Der
Pokal-Spielplan der Frauen bleibt, der Abgleich von Termin und Ergebnis läuft
über Sportschau (siehe oben).
Für die Frauen gibt es damit keine automatische Torschützinnen-Quelle mehr.

### 15.10.2026 — contentHash mit Torschützen

`seedkit.content_hash` (und `MatchStore.contentHash` in der App) umfasst
jetzt auch Torschütze und Minute jedes Tors. Anlass: Neun Tore von Rasmus
Kristensen standen unter Jørgen Kristensen. Eine reine Namenskorrektur
änderte den Hash nicht, bestehende Installationen hätten den mitgelieferten
Seed deshalb nicht neu importiert, und das Fenster deckt nur 120 Tage ab.

Beide Implementierungen müssen Zeichen für Zeichen übereinstimmen. Die App
importiert nach dem Update auf die neue Definition einmalig komplett neu.

### 03.10.2026 — Torschützinnen gegen den DFB prüfen, Männer im Abgleich

*Die Torprüfung ist seit dem 28.10.2026 entfernt. Der Abgleich von Termin und
Ergebnis für die Männer-Bundesliga läuft seit dem 28.10.2026 über Sportschau.*

Jedes Ergebnis der Frauen-Bundesliga wird einmal gegen die DFB-Detailseite
geprüft: Name, Mannschaft, Minute (eine Minute Toleranz) und Anzahl. Akzente
zählen nicht als Abweichung („Erëleta" = „Ereleta"). Was geprüft ist, steht
in `data/dfb_torpruefung.json`; neu geprüft wird nur, wenn sich die Torliste
ändert oder der DFB beim letzten Mal noch keine Ereignisse hatte (dann
höchstens einmal täglich). Höchstens vier Detailseiten je Lauf.

Bei einer Abweichung gilt der DFB: Seine Torliste ersetzt die im Seed, die
Job-Zusammenfassung zeigt vorher und nachher. Das hält, weil `upsert()` eine
benannte Torliste eines beendeten Spiels nicht mehr überschreibt. Übernommen
wird nur, wenn die DFB-Liste zum Endstand passt und kein Eigentor enthält;
sonst wird nur gemeldet.

Termine und Ergebnisse bleiben dagegen reine Meldung. Die App fragt
OpenLigaDB für die Frauen selbst ab; ein Termin, der im Seed vom DFB käme,
erzeugte nach Abpfiff ein zweites Spiel unter der OpenLigaDB-ID.

Der Termin- und Ergebnisabgleich läuft jetzt auch für die Männer-Bundesliga.
Der DFB-Pokal bleibt außen vor: Pokalrunden führt der DFB nicht als Spieltag.

### 03.10.2026 — Frauen-Bundesliga gegen den DFB prüfen

*Seit dem 28.10.2026 über Sportschau statt DFB (`providers.sportschau_abgleich`).*

OpenLigaDB ist bei den Frauen unzuverlässig. Jeder `ffb1`-Datensatz wird
jetzt gegen den DFB-Spielplan geprüft (`providers.dfb_abgleich`): Termin,
Ergebnis und ob es die Paarung an diesem Spieltag gibt. Abweichungen stehen
als PRUEFEN-Zeile im Protokoll und als Tabelle in der Job-Zusammenfassung.
Nur Meldung, keine Korrektur — die App fragt OpenLigaDB selbst ab, behoben
werden muss es dort.

Nicht terminierte Spieltage zeigt der DFB als Zeitraum (`11.12. ~ 14.12.2026`).
Ein OpenLigaDB-Platzhalter in diesem Zeitraum gilt nicht als Abweichung.

Anlass: Eintracht – FC Bayern München stand bei OpenLigaDB auf Sonntag 15:30,
gespielt wurde Samstag 17:55.

### 01.10.2026 — UEFA Women's Europa Cup

Neuer ESPN-Slug `uefa.w.europa`, im Seed als `europacup`. `ESPN_SLUGS` führt
dafür jetzt Paare aus Slug und Wettbewerb. Die beiden Qualifikationsspiele
gegen FH (23.09. und 30.09.) einmalig per `--espn-back 9` nachgeholt.

Release-Assets werden jetzt auch veröffentlicht, wenn der Seed außerhalb
eines Bot-Laufs geändert wurde (Handnachtrag, lokaler Nachhol-Lauf). Bisher
hing der Upload allein daran, dass der Lauf selbst etwas änderte.

### 27.09.2026 — DFB-Pokal der Frauen

Der Wettbewerb kam bisher aus keiner Quelle: OpenLigaDB führt ihn nicht (832
Ligen geprüft), ESPN kennt keine deutsche Frauenliga, eintracht-archiv.de
trägt erst Tage später nach. Das Datencenter ist dafür jetzt Primärquelle
und legt die Spiele an (`providers.dfb_spiele`), statt nur Tore zu
ergänzen. Slug: `dfb-pokal-1132`, Saisonformat `2026-27`.

### 16.09.2026 — Quellenlinks automatisch ergänzen

Neuer Lauf am 1. und 15. jeden Monats (`--archiv-links`): Für gespielte
Partien der laufenden Saison ohne `sourceUrl` wird die Seite auf
eintracht-archiv.de gesucht. Die URL ergibt sich aus Datum und Geschlecht,
zu recherchieren ist nichts — geprüft wird trotzdem, weil das Archiv von
Hand gepflegt wird und Tage bis Wochen hinterherhängt.

### 16.09.2026 — Torschützen auch bei den Männern korrigieren

*Seit dem 28.10.2026 entfernt, Namen kommen von Wikidata.*

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

*Seit dem 28.10.2026 entfernt. Geblieben ist der Vereinsspielplan.*

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
