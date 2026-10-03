#!/usr/bin/env python3
"""Einstiegspunkt der GitHub Action: Quellen abfragen, in den Seed mergen,
validieren, Artefakte schreiben.

Grundregeln (siehe PLAN):
  1. Nie neu generieren, nur ergaenzen. Der Seed enthaelt Daten aus
     eintracht-archiv.de (sourceUrl, Torschuetzennamen, Notizen), die keine
     API liefert.
  2. Upsert-Semantik der App exakt spiegeln (seedkit.upsert).
  3. Bestehende IDs niemals umschreiben.
  4. Abbruch statt Notfallreparatur - lieber veraltet als kaputt.
  5. Kein Commit ohne inhaltliche Aenderung.
  6. gender aus dem Liga-Kuerzel, nie aus der API raten.
"""
import argparse, json, gzip, hashlib, os, sys, time
from datetime import datetime, timezone, timedelta, date

from seedkit import (merge, validate, content_hash, canon_dump, MATCH_FIELDS,
                     spieltag_plausibilitaet, zerlege_abkuerzung)
import providers

# gender ergibt sich allein aus dem abgefragten Liga-Kuerzel.
#
# "dfb" ist der DFB-Pokal der Maenner. Erst ab 2026 abgefragt, aus demselben
# Grund wie bei bl1: Abgeschlossene Runden stehen kuratiert im Seed, und
# OpenLigaDB weicht bei alten Spielzeiten in Terminen und Vereinsschreibweisen
# ab - was ueber die Match-ID Dubletten erzeugt. Optional, weil die Eintracht
# nach einem Pokal-Aus in der Saison gar nicht mehr vorkommt; eine leere
# Antwort ist dann der Normalfall.
LIGEN = [
    {"shortcut": "bl1",  "gender": "men",   "first": 2003, "optional": False},
    {"shortcut": "dfb",  "gender": "men",   "first": 2026, "optional": True},
    {"shortcut": "ffb1", "gender": "women", "first": 2026, "optional": True},
    {"shortcut": "wsc",  "gender": "women", "first": 2026, "optional": True},
]
# ESPN-Wettbewerbe als Paar aus Slug und Wettbewerb im Seed.
#
# Frueher eine reine Slug-Liste, weil nur die Women's Champions League
# angebunden war und providers.espn() "championsLeague" als Standard setzt.
# Seit 2025/26 gibt es mit dem UEFA Women's Europa Cup einen zweiten
# europaeischen Frauenwettbewerb - wer in der CL-Qualifikation ausscheidet,
# landet dort. Er laeuft im Seed als "europacup", die App beschriftet das
# neutral als "Europapokal".
ESPN_SLUGS = [
    ("uefa.wchampions_qual", "championsLeague"),
    ("uefa.wchampions",      "championsLeague"),
    ("uefa.w.europa",        "europacup"),
]

# DFB-Datencenter als Zweitquelle fuer Torschuetzen.
#
# Zwei Luecken, die es schliesst. Bei den Frauen fuehrt OpenLigaDB gar keine
# Torschuetzinnen - was dort steht, hat ein Mensch von Hand eingetragen. Bei
# den Maennern kuerzt OpenLigaDB Vornamen von Spielern ab, die nicht im
# gepflegten Bestand stehen ("T. Skarke"); loese_abkuerzung() kommt da nicht
# weiter, weil es nur aufloesen kann, was der Seed schon kennt. Das
# Datencenter schreibt Namen immer aus.
#
# Welche Wettbewerbe abgedeckt sind, steht in providers.DFB_QUELLEN:
# Bundesliga (Frauen und Maenner) und DFB-Pokal der Maenner. Der Europapokal
# liegt nicht beim DFB, die UWCL kommt von ESPN.
#
# Der Provider legt nie ein Spiel an - er sieht ausschliesslich Partien, die
# nach dem Merge dieses Laufs ohnehin im Seed stuenden. Faellt die Quelle
# aus, bleibt das Ergebnis ohne Namen; das ist kein Datenfehler und faerbt
# den Lauf nicht rot.
#
# Erst ab 2026, weil aeltere Saisons ihre Torschuetzen kuratiert aus
# eintracht-archiv.de haben - dieselbe Begruendung wie bei bl1 und dfb.
DFB_AB_SAISON = 2026
# Detailseiten je Lauf und Wettbewerb. Ein Rueckstand (etwa nach einem
# laengeren Ausfall) verteilt sich damit ueber mehrere Laeufe, statt das
# Datencenter in einem Schwung mit einer ganzen Saison zu belegen.
DFB_MAX_DETAIL = 10

# Quellenlinks auf eintracht-archiv.de.
#
# Die URL ergibt sich aus Datum und Geschlecht (siehe providers.archiv_urls),
# zu recherchieren ist nichts. Geprueft werden muss trotzdem: Die Seiten
# entstehen von Hand und mit Verzug, ein blind gesetzter Link zeigte sonst
# auf eine 404-Seite.
#
# Deshalb laeuft das nicht bei jedem Lauf mit, sondern nur, wenn
# --archiv-links gesetzt ist - im Workflow der Lauf am 1. und 15. Jeder
# Versuch kostet eine Anfrage, und ein Spiel, dessen Seite noch fehlt,
# bleibt bis zum naechsten Mal offen.
ARCHIV_MAX_PRUEFUNGEN = 25

# OpenLigaDB-Ligen, deren Datensaetze gegen den DFB-Spielplan geprueft
# werden (providers.dfb_abgleich). Die Frauen-Bundesliga, weil OpenLigaDB
# dort nachweislich unzuverlaessig ist, die Maenner-Bundesliga zur
# Sicherheit. Der DFB-Pokal fehlt bewusst: Der Abgleich ordnet ueber den
# Spieltag zu, und Pokalrunden fuehrt der DFB nicht als Spieltag.
DFB_ABGLEICH = {"ffb1": ("bundesliga", "women"),
               "bl1":  ("bundesliga", "men")}


# Torschuetzinnen gegen den DFB pruefen (providers.tore_abweichungen).
#
# Jedes Ergebnis einmal: Welche Spiele mit welcher Torliste schon geprueft
# sind, steht in PRUEFSTAND_DATEI neben dem Seed. Neu geprueft wird nur,
# wenn sich die Torliste im Seed geaendert hat - oder wenn der DFB beim
# letzten Mal noch keine Ereignisse hatte; das holt er mit Tagen Verzug nach,
# deshalb dann hoechstens einmal pro Tag erneut.
#
# Eine gemeldete Abweichung wird bei jedem Lauf wieder gemeldet, aber aus
# dem Pruefstand, ohne erneute Anfrage - bis die Torliste sich aendert, weil
# sie an der Quelle korrigiert wurde.
TORPRUEFUNG = {("bundesliga", "women")}
TORPRUEFUNG_MAX = 4          # Detailseiten je Lauf
PRUEFSTAND_DATEI = "dfb_torpruefung.json"


def _torsignatur(tore):
    roh = json.dumps([[g.get("minute"), g.get("scorer"), bool(g.get("forHome"))]
                      for g in tore], ensure_ascii=False)
    return hashlib.sha1(roh.encode()).hexdigest()[:12]


def torpruefung(matches, stand, saison, log, heute=None):
    """Prueft Torlisten gegen den DFB. -> (neuer_stand, meldungen)

    `stand` ist der Inhalt von PRUEFSTAND_DATEI: {id: {"sig", "status",
    "tag", "meldungen"}}. status ist "ok", "abweichung" oder "offen" (der
    DFB hatte noch keine Ereignisse).
    """
    heute = (heute or date.today()).isoformat()
    label = f"{saison}/{str(saison + 1)[-2:]}"
    neu_stand, meldungen, abrufe = {}, [], 0
    urls = {}

    for m in matches:
        art = (m.get("competition"), m.get("gender"))
        if (art not in TORPRUEFUNG or m.get("season") != label
                or not m.get("isFinished")
                or (m.get("homeScore") or 0) + (m.get("awayScore") or 0) == 0
                or not m.get("goals")):
            continue
        mid, sig = m["id"], _torsignatur(m["goals"])
        alt = stand.get(mid)

        faellig = (alt is None or alt.get("sig") != sig
                   or (alt.get("status") == "offen" and alt.get("tag") != heute))
        if not faellig or abrufe >= TORPRUEFUNG_MAX:
            if alt is not None:
                neu_stand[mid] = alt
                if alt.get("status") == "abweichung" and alt.get("sig") == sig:
                    meldungen += [(mid, *x) for x in alt.get("meldungen", [])]
            continue

        if art not in urls:
            urls[art] = providers.dfb_spiel_urls(saison, *art)
        url = urls[art].get(mid)
        if not url:
            # Keine DFB-Zeile zu dieser ID - meldet bereits der Abgleich
            # (Termin oder Paarung).
            continue
        try:
            dfb_tore = providers.dfb_tore(providers._dfb_html(url))
        except Exception as e:
            log(f"  Hinweis: Torpruefung {mid} – {e}")
            if alt is not None:
                neu_stand[mid] = alt
            continue
        abrufe += 1
        time.sleep(0.8)

        if not dfb_tore:
            neu_stand[mid] = {"sig": sig, "status": "offen", "tag": heute}
            continue
        abw = providers.tore_abweichungen(m["goals"], dfb_tore)
        if abw:
            neu_stand[mid] = {"sig": sig, "status": "abweichung", "tag": heute,
                              "meldungen": [list(x) for x in abw]}
            meldungen += [(mid, *x) for x in abw]
        else:
            neu_stand[mid] = {"sig": sig, "status": "ok", "tag": heute}

    offen = sum(1 for v in neu_stand.values() if v["status"] == "offen")
    log(f"  Torpruefung {label}: {abrufe} Detailseiten, "
        f"{sum(1 for v in neu_stand.values() if v['status'] == 'ok')} ok, "
        f"{offen} beim DFB noch ohne Ereignisse")
    return neu_stand, meldungen


def melde(text):
    """Schreibt eine Zeile zusaetzlich in die Job-Zusammenfassung von GitHub
    Actions. Was dort steht, sieht man auf der Uebersichtsseite des Laufs,
    ohne das Protokoll aufzuklappen."""
    ziel = os.environ.get("GITHUB_STEP_SUMMARY")
    if ziel:
        with open(ziel, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def aktuelle_saison(heute=None):
    heute = heute or date.today()
    return heute.year if heute.month >= 8 else heute.year - 1


def roster(seed, gender):
    """Bereits bekannte, ausgeschriebene Torschuetzennamen eines Geschlechts.

    Grundlage fuer providers.loese_abkuerzung(). Nach Geschlecht getrennt,
    damit ein abgekuerzter Herrenname nicht auf eine gleichnamige Spielerin
    trifft - "E. Baum" haette sonst "Lisa Baum" als Kandidatin.
    """
    namen = set()
    for m in seed:
        if m.get("gender") != gender:
            continue
        for g in m.get("goals") or []:
            s = (g.get("scorer") or "").strip()
            if s and " " in s:
                namen.add(s)
    return namen


def torluecken(matches, saison, competition, gender):
    """Spiele eines Wettbewerbs mit fehlender oder mangelhafter Torliste.
    -> (kandidaten, unvollstaendig)

    Zwei Faelle, beide vom Datencenter behebbar:

      1. Ergebnis da, Torliste leer. Bei den Frauen der Normalfall -
         OpenLigaDB fuehrt dort keine Torschuetzinnen.
      2. Abgekuerzte Vornamen ("T. Skarke", "R. Fellhauer"). Das betrifft
         vor allem die Maenner: OpenLigaDB kuerzt bei Spielern, die nicht im
         gepflegten Bestand stehen, und loese_abkuerzung() kann nur
         aufloesen, was der Seed schon kennt. Das Datencenter schreibt Namen
         immer aus, und upsert() ersetzt eine Torliste, sobald die neue
         weniger Abkuerzungen enthaelt.

    Der dritte denkbare Fall - weniger Eintraege als Tore im Endstand - geht
    NICHT in die Kandidaten. upsert() ersetzt eine vorhandene, benannte
    Torliste nicht, auch keine halbe; ein Abruf verbraeuchte also in jedem
    Lauf eine Detailseite, ohne je etwas zu aendern. Solche Spiele kommen
    stattdessen als zweite Liste zurueck und werden protokolliert: Sie
    gehoeren an der Quelle korrigiert, nicht hier.

    Torlose Spiele fallen ganz heraus - bei einem 0:0 gibt es nichts
    nachzutragen.
    """
    label = f"{saison}/{str(saison + 1)[-2:]}"
    kandidaten, unvollstaendig = {}, []
    for m in matches:
        if (m.get("gender") != gender or m.get("competition") != competition
                or m.get("season") != label or m.get("homeScore") is None):
            continue
        tore = (m.get("homeScore") or 0) + (m.get("awayScore") or 0)
        if tore == 0:
            continue
        gs = m.get("goals") or []
        namen_unklar = any(zerlege_abkuerzung(g.get("scorer") or "")
                           or not (g.get("scorer") or "").strip()
                           or g.get("scorer") == "–" for g in gs)
        if not gs or namen_unklar:
            kandidaten[m["id"]] = m
        elif len(gs) < tore:
            unvollstaendig.append(f"{m['id']} ({len(gs)} von {tore} Toren)")
    return kandidaten, unvollstaendig


def archivluecken(matches, saison, heute=None):
    """Spiele einer Saison ohne Quellenlink, deren Archivseite existieren kann.

    Geschlecht und Wettbewerb spielen keine Rolle - das Archiv fuehrt alles
    unter demselben Datumsschema.

    Kuenftige Partien fallen heraus: Die Seite entsteht erst nach dem Spiel,
    ein Versuch waere garantiert vergeblich. Alles andere bleibt Kandidat,
    auch ueber mehrere Laeufe hinweg, weil das Archiv von Hand gepflegt wird
    und Tage bis Wochen hinterherhaengt.
    """
    heute = (heute or date.today()).isoformat()
    label = f"{saison}/{str(saison + 1)[-2:]}"
    return {m["id"]: m for m in matches
            if m.get("season") == label
            and not (m.get("sourceUrl") or "").strip()
            and m.get("date", "")[:10] <= heute}


def sammle(saisons, espn_tage, log, seed=None, archiv=False):
    gefunden = []
    rosters = {g: roster(seed or [], g) for g in ("men", "women")}
    for cfg in LIGEN:
        for s in saisons:
            if s < cfg["first"]:
                continue
            try:
                roh = providers.openligadb(cfg["shortcut"], s, cfg["gender"],
                                           roster=rosters.get(cfg["gender"]))
            except Exception as e:
                stufe = "Hinweis" if cfg["optional"] else "FEHLER"
                log(f"  {stufe}: {cfg['shortcut']}/{s} nicht abrufbar – {e}")
                if not cfg["optional"]:
                    raise
                continue
            ok, verworfen, verdaechtig = spieltag_plausibilitaet(roh)
            if verworfen:
                log(f"  {cfg['shortcut']}/{s}: {len(verworfen)} Spiele mit vertauschtem "
                    f"Tag/Monat verworfen (Spieltage {verdaechtig})")
            log(f"  {cfg['shortcut']}/{s}: {len(ok)} Spiele")

            abgleich = DFB_ABGLEICH.get(cfg["shortcut"])
            if abgleich and s >= DFB_AB_SAISON:
                try:
                    abw = providers.dfb_abgleich(s, ok, *abgleich, log=log)
                except Exception as e:
                    abw = []
                    log(f"  Hinweis: Abgleich {cfg['shortcut']}/{s} – {e}")
                if abw:
                    melde(f"### PRUEFEN: {cfg['shortcut']}/{s} weicht vom DFB ab")
                    melde("| Spiel | Feld | OpenLigaDB | DFB |")
                    melde("| --- | --- | --- | --- |")
                for mid, art, ist, soll in abw:
                    log(f"  PRUEFEN: {mid} {art} – OpenLigaDB {ist}, DFB {soll}")
                    melde(f"| {mid} | {art} | {ist} | {soll} |")
                if not abw:
                    log(f"  {cfg['shortcut']}/{s}: mit DFB abgeglichen, keine Abweichung")

            gefunden += ok
            time.sleep(0.5)

    for tag in espn_tage:
        for sl, wettbewerb in ESPN_SLUGS:
            try:
                treffer = providers.espn(sl, tag, "women", competition=wettbewerb)
            except Exception as e:
                log(f"  Hinweis: ESPN {sl}/{tag} – {e}")
                continue
            if treffer:
                log(f"  ESPN {sl} {tag}: {len(treffer)} Spiele")
            gefunden += treffer
            time.sleep(0.3)

    # Der DFB-Pokal der Frauen kommt sonst nirgends her: OpenLigaDB fuehrt
    # ihn nicht, ESPN kennt keine deutsche Frauenliga, und eintracht-archiv.de
    # traegt erst Tage spaeter nach. Das Datencenter ist hier deshalb
    # Primaerquelle und legt die Spiele an - anders als weiter unten, wo es
    # nur Torschuetzen zu bereits bekannten Partien ergaenzt.
    for s in saisons:
        if s < DFB_AB_SAISON:
            continue
        for (competition, gender) in providers.DFB_PRIMAERQUELLEN:
            try:
                gefunden += providers.dfb_spiele(s, competition, gender, log=log)
            except Exception as e:
                log(f"  Hinweis: DFB-Spielplan/{competition}/{gender}/{s} "
                    f"nicht nutzbar – {e}")

    # DFB-Datencenter zuletzt: Welche Spiele Tore brauchen, steht erst fest,
    # wenn die Ergebnisse dieses Laufs beruecksichtigt sind. Der Probe-Merge
    # nutzt dieselbe upsert-Semantik wie der echte weiter unten, damit die
    # Vorauswahl nicht auf einer eigenen, abweichenden Regel beruht.
    # prune_moved bleibt aus: Hier wird nichts geschrieben, und das
    # Entfernen verschobener Platzhalter gehoert in den echten Merge.
    if seed is not None:
        vorschau, _ = merge(seed, gefunden, prune_moved=False)
        for s in saisons:
            if s < DFB_AB_SAISON:
                continue
            for (competition, gender) in providers.DFB_QUELLEN:
                kandidaten, unvollstaendig = torluecken(vorschau, s, competition, gender)
                if unvollstaendig:
                    # Der Upsert ruehrt eine vorhandene, benannte Torliste
                    # nicht an - auch keine halbe. Das ist hier nur zu
                    # melden, nicht zu beheben.
                    log(f"  PRUEFEN: unvollstaendige Torlisten, die automatisch "
                        f"nicht gefuellt werden: {', '.join(unvollstaendig)}")
                if not kandidaten:
                    continue
                log(f"  {len(kandidaten)} Spiele ohne oder mit unklarer "
                    f"Torliste ({competition}/{gender} {s})")
                try:
                    gefunden += providers.dfb_torschuetzen(
                        s, kandidaten, competition, gender,
                        log=log, max_detail=DFB_MAX_DETAIL)
                except Exception as e:
                    # Bewusst kein raise: Eine ausgefallene Zweitquelle ist
                    # ein Ergebnis ohne Namen, kein kaputter Seed.
                    log(f"  Hinweis: DFB-Datencenter/{competition}/{gender}/{s} "
                        f"nicht nutzbar – {e}")

        if archiv:
            for s in saisons:
                kandidaten = archivluecken(vorschau, s)
                if not kandidaten:
                    continue
                log(f"  {len(kandidaten)} gespielte Partien ohne Quellenlink "
                    f"(Saison {s})")
                try:
                    gefunden += providers.archiv_links(
                        kandidaten, log=log,
                        max_pruefungen=ARCHIV_MAX_PRUEFUNGEN)
                except Exception as e:
                    # Wie beim Datencenter: ein fehlender Link ist kein
                    # kaputter Seed.
                    log(f"  Hinweis: eintracht-archiv/{s} nicht nutzbar – {e}")
    return gefunden


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="data/seed_matches.json")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--window-days", type=int, default=120)
    ap.add_argument("--espn-back", type=int, default=3)
    ap.add_argument("--espn-forward", type=int, default=2)
    ap.add_argument("--full-backfill", action="store_true",
                    help="alle Saisons ab first statt nur der laufenden")
    ap.add_argument("--offline-fixture",
                    help="statt der APIs diese JSON-Datei als Quelle nutzen (Tests)")
    ap.add_argument("--archiv-links", action="store_true",
                    help="fehlende Quellenlinks auf eintracht-archiv.de suchen "
                         "(kostet eine Anfrage je offenem Spiel - im Workflow "
                         "nur am 1. und 15.)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    zeilen = []
    def log(s):
        print(s)
        zeilen.append(s)

    doc = json.load(open(args.seed, encoding="utf-8"))
    seed = doc["matches"]
    log(f"Seed geladen: {len(seed)} Spiele, Hash {content_hash(seed)}")

    vorher_fehler, _ = validate(seed)
    if vorher_fehler:
        log(f"ABBRUCH: Der vorhandene Seed ist bereits fehlerhaft ({len(vorher_fehler)}).")
        for e in vorher_fehler[:5]:
            log(f"  {e}")
        return 1

    if args.offline_fixture:
        gefunden = json.load(open(args.offline_fixture, encoding="utf-8"))
        log(f"Fixture: {len(gefunden)} Spiele (keine Netzabfrage)")
    else:
        laufend = aktuelle_saison()
        saisons = (list(range(2003, laufend + 1)) if args.full_backfill else [laufend])
        heute = date.today()
        espn_tage = [heute + timedelta(days=d)
                     for d in range(-args.espn_back, args.espn_forward + 1)]
        log(f"Quellen: Saisons {saisons[0]}–{saisons[-1]}, "
            f"ESPN {espn_tage[0]}–{espn_tage[-1]}")
        gefunden = sammle(saisons, espn_tage, log, seed,
                          archiv=args.archiv_links)

    log(f"Insgesamt {len(gefunden)} Datensaetze von den Quellen")

    neu, stats = merge(seed, gefunden)
    log(f"Merge: {dict(stats)}")

    # --- Schutzschranken (Regel 4) ---
    fehler, warnungen = validate(neu)
    if fehler:
        log(f"ABBRUCH: {len(fehler)} Validierungsfehler nach dem Merge.")
        for e in fehler[:10]:
            log(f"  {e}")
        return 1
    if len(neu) < len(seed) - 5:
        log(f"ABBRUCH: Spielzahl faellt von {len(seed)} auf {len(neu)}.")
        return 1

    # --- Torschuetzinnen gegen den DFB ---
    # Laeuft auf dem fertig gemergten Bestand: geprueft wird, was die App
    # tatsaechlich bekommt, egal aus welcher Quelle es stammt.
    pruefstand_neu = None
    if not args.offline_fixture:
        pfad = os.path.join(args.out_dir, PRUEFSTAND_DATEI)
        try:
            with open(pfad, encoding="utf-8") as f:
                pruefstand = json.load(f)
        except (OSError, ValueError):
            pruefstand = {}
        try:
            pruefstand_neu, tor_meldungen = torpruefung(
                neu, pruefstand, aktuelle_saison(), log)
        except Exception as e:
            # Wie alle DFB-Zugriffe: ein Ausfall ist ein Hinweis, kein Abbruch.
            log(f"  Hinweis: Torpruefung nicht moeglich – {e}")
            pruefstand_neu, tor_meldungen = None, []
        if tor_meldungen:
            melde("### PRUEFEN: Torschuetzinnen weichen vom DFB ab")
            melde("| Spiel | Feld | Seed (OpenLigaDB) | DFB |")
            melde("| --- | --- | --- | --- |")
        for mid, art, ist, soll in tor_meldungen:
            log(f"  PRUEFEN: {mid} {art} – Seed {ist}, DFB {soll}")
            melde(f"| {mid} | {art} | {ist} | {soll} |")
        if pruefstand_neu is not None and pruefstand_neu != pruefstand and not args.dry_run:
            os.makedirs(args.out_dir, exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                json.dump(pruefstand_neu, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.write("\n")
            # Eigener Output, damit der Workflow den Pruefstand auch dann
            # committet, wenn sich am Seed nichts geaendert hat - sonst
            # wuerde jeder Lauf dieselben Seiten erneut holen.
            _setze_output(pruefstand="true")

    # Vergleich ueber die vollstaendig serialisierte Datei, NICHT ueber
    # content_hash: Der deckt nur id, Ergebnis, Toranzahl, Notiz und
    # Geschlecht ab (so ist er in MatchStore definiert und muss es bleiben,
    # damit die App denselben Wert berechnet). Terminverschiebungen,
    # praezisierte Anstosszeiten, Spieltagsnummern und Halbzeitstaende
    # aendern ihn nicht - genau die Faelle also, die zu Saisonbeginn am
    # haeufigsten sind. Mit content_hash als Abbruchkriterium haette die
    # Action solche Aktualisierungen nie committet.
    vorher_datei = canon_dump(seed)
    nachher_datei = canon_dump(neu)
    if vorher_datei == nachher_datei:
        log("Keine inhaltliche Aenderung – kein Commit.")
        _setze_output(changed="false", summary="keine Aenderung")
        return 0
    if content_hash(neu) == content_hash(seed):
        log("Hinweis: Aenderungen betreffen nur Felder ausserhalb des "
            "contentHash (Termine, Spieltage, Halbzeit). Die App loest damit "
            "keinen Seed-Re-Import aus – das Fenster liefert sie trotzdem aus.")

    if args.dry_run:
        log("--dry-run: nichts geschrieben.")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")

    with open(os.path.join(args.out_dir, "seed_matches.json"), "w", encoding="utf-8") as f:
        f.write(canon_dump(neu) + "\n")

    kompakt = json.dumps({"matches": [{k: m.get(k) for k in MATCH_FIELDS} for m in neu]},
                         ensure_ascii=False, separators=(",", ":")).encode()
    _gz(os.path.join(args.out_dir, "seed_matches.json.gz"), kompakt)

    grenze = (date.today() - timedelta(days=args.window_days)).isoformat()
    fenster = [m for m in neu if m["date"][:10] >= grenze]
    fenster_bytes = json.dumps(
        {"schema": 1, "version": version, "windowFrom": grenze,
         "matches": [{k: m.get(k) for k in MATCH_FIELDS} for m in fenster]},
        ensure_ascii=False, separators=(",", ":")).encode()
    _gz(os.path.join(args.out_dir, "window.json.gz"), fenster_bytes)

    manifest = {
        "schema": 1,
        "version": version,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "matchCount": len(neu),
        "contentHash": content_hash(neu),
        "window": {"url": "window.json.gz", "from": grenze,
                   "bytes": _size(os.path.join(args.out_dir, "window.json.gz")),
                   "sha256": hashlib.sha256(fenster_bytes).hexdigest()},
        "full": {"url": "seed_matches.json.gz",
                 "bytes": _size(os.path.join(args.out_dir, "seed_matches.json.gz")),
                 "sha256": hashlib.sha256(kompakt).hexdigest()},
        "minSupportedBundle": "2026-01-01",
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    log(f"Artefakte: seed_matches.json ({len(neu)} Spiele), "
        f"window.json.gz ({manifest['window']['bytes']} B, {len(fenster)} Spiele), "
        f"seed_matches.json.gz ({manifest['full']['bytes']} B)")
    if warnungen:
        log(f"{len(warnungen)} Warnungen (kein Abbruch)")

    teile = [f"{v} {k}" for k, v in sorted(stats.items()) if k != "unveraendert"]
    _setze_output(changed="true", summary=", ".join(teile) or "aktualisiert")
    return 0


def _gz(pfad, daten):
    with gzip.GzipFile(pfad, "wb", compresslevel=9, mtime=0) as f:
        f.write(daten)


def _size(p):
    return os.path.getsize(p)


def _setze_output(**kv):
    ziel = os.environ.get("GITHUB_OUTPUT")
    if not ziel:
        return
    with open(ziel, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


if __name__ == "__main__":
    sys.exit(main())
