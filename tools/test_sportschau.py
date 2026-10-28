#!/usr/bin/env python3
"""Offline-Test des Sportschau-Abgleichs gegen gespeicherte Ausschnitte.

Lauf: python3 test_sportschau.py   (aus tools/)

Die Fixtures in tests/fixtures/sportschau_*.html sind Ausschnitte der
Spielplan-Uebersichten von sportschau.de. Schlaegt der Test fehl, nachdem er
einmal lief, hat entweder der Parser oder das Sportschau-Layout sich
geaendert; im zweiten Fall gehoeren neue Ausschnitte her.
"""
import os, sys, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers
from seedkit import make_id

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures")


def lies(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def pruefe(bedingung, text):
    if not bedingung:
        print(f"FEHLER: {text}")
        return 1
    print(f"  ok: {text}")
    return 0


def als_openligadb(f, **aenderung):
    m = {"id": make_id(f["date"], f["homeTeam"], f["awayTeam"]),
         "date": f["date"], "matchday": f["matchday"],
         "homeTeam": f["homeTeam"], "awayTeam": f["awayTeam"],
         "homeScore": f["homeScore"], "awayScore": f["awayScore"]}
    m.update(aenderung)
    return m


def main():
    fehler = 0

    print("Spieltag lesen")
    tag, maenner = providers.sportschau_spieltag(
        lies("sportschau_bundesliga_spieltag7.html"), 2026)
    fehler += pruefe(tag == 7 and len(maenner) == 4, f"Spieltag 7, 4 Spiele ({tag}, {len(maenner)})")
    erstes = maenner[0]
    fehler += pruefe(erstes["homeTeam"] == "VfB Stuttgart"
                     and erstes["awayTeam"] == "Borussia Mönchengladbach",
                     f"Paarung {erstes['homeTeam']} – {erstes['awayTeam']}")
    fehler += pruefe((erstes["homeScore"], erstes["awayScore"]) == (2, 3),
                     f"Endstand, nicht Halbzeit ({erstes['homeScore']}:{erstes['awayScore']})")
    fehler += pruefe(erstes["date"] == "2026-10-23T20:30:00",
                     f"Sommerzeit: 18:30 UTC = 20:30 ({erstes['date']})")
    fehler += pruefe(all(f["homeScore"] is not None for f in maenner), "beendete Spiele haben ein Ergebnis")

    tag, frauen = providers.sportschau_spieltag(
        lies("sportschau_bundesliga_frauen_spieltag9.html"), 2026)
    fehler += pruefe(tag == 9 and len(frauen) == 3, f"Frauen: Spieltag 9, 3 Spiele ({tag}, {len(frauen)})")
    fehler += pruefe(frauen[0]["date"] == "2026-11-06T18:30:00",
                     f"Winterzeit: 17:30 UTC = 18:30 ({frauen[0]['date']})")
    fehler += pruefe(all(f["homeScore"] is None and f["awayScore"] is None for f in frauen),
                     "Spiele vor dem Anstoss haben kein Ergebnis (nicht 0:0)")
    fehler += pruefe(providers.sportschau_spieltag(
        lies("sportschau_bundesliga_spieltag7.html"), 2025) == (None, []),
        "Seite einer anderen Saison wird verworfen")
    fehler += pruefe(providers.sportschau_spieltag("<html><title>Startseite</title></html>", 2026)
                     == (None, []), "Seite ohne Spieltag wird verworfen")

    print("Abgleich")
    sauber = [als_openligadb(f) for f in maenner]
    fehler += pruefe(providers.abweichungen(sauber, maenner) == [], "identische Daten: keine Meldung")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], date="2026-11-06T20:30:00")], frauen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin"
                     and abw[0][3] == "2026-11-06 18:30", f"falsche Anstosszeit gemeldet ({abw})")

    abw = providers.abweichungen(
        [als_openligadb(maenner[0], homeScore=3)], maenner)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Ergebnis" and abw[0][3] == "2:3",
                     f"falsches Ergebnis gemeldet ({abw})")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], awayTeam="Phantom FC")], frauen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Paarung",
                     f"unbekannte Paarung gemeldet ({abw})")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], homeScore=1, awayScore=0)], frauen)
    fehler += pruefe(abw == [], "Zwischenstand bei OpenLigaDB ist keine Abweichung")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], matchday=12)], frauen)
    fehler += pruefe(abw == [], "anderer Spieltag als die Seite fuehrt: nichts zu pruefen")

    # Ohne Uhrzeit bei Sportschau ("--:--") zaehlt nur das Datum.
    ohne = lies("sportschau_bundesliga_frauen_spieltag9.html").replace(
        'class="match-time">18:30<', 'class="match-time">--:--<', 1)
    _, offen = providers.sportschau_spieltag(ohne, 2026)
    fehler += pruefe(offen[0]["date"] == "2026-11-06T00:00:00", f"Anstoss offen ({offen[0]['date']})")
    fehler += pruefe(providers.abweichungen(
        [als_openligadb(frauen[0], date="2026-11-06T18:30:00")], offen) == [],
        "fehlende Uhrzeit ist keine Abweichung")
    abw = providers.abweichungen(
        [als_openligadb(frauen[0], date="2026-11-07T18:30:00")], offen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin", "anderer Tag wird trotzdem gemeldet")

    print("sportschau_abgleich")
    original = providers.sportschau_seite
    meldungen = []
    log = meldungen.append
    try:
        providers.sportschau_seite = lambda c, g: lies("sportschau_bundesliga_frauen_spieltag9.html")
        abw = providers.sportschau_abgleich(
            2026, [als_openligadb(frauen[0], date="2026-11-06T20:30:00")],
            "bundesliga", "women", log=log)
        fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin", "Abweichung aus der Seite")

        providers.sportschau_seite = lambda c, g: (_ for _ in ()).throw(
            urllib.error.URLError("nicht erreichbar"))
        meldungen.clear()
        abw = providers.sportschau_abgleich(
            2026, [als_openligadb(frauen[0])], "bundesliga", "women", log=log)
        fehler += pruefe(abw == [] and meldungen and "nicht erreichbar" in meldungen[0],
                         "Ausfall ist ein Hinweis, kein Fehler")

        providers.sportschau_seite = lambda c, g: "<html><title>Wartung</title></html>"
        meldungen.clear()
        abw = providers.sportschau_abgleich(
            2026, [als_openligadb(frauen[0])], "bundesliga", "women", log=log)
        fehler += pruefe(abw == [] and meldungen and "Layout" in meldungen[0],
                         "Seite ohne Spiele ist ein Hinweis")

        def nie(c, g):
            raise AssertionError("darf nicht abgerufen werden")
        providers.sportschau_seite = nie
        fehler += pruefe(providers.sportschau_abgleich(2026, [als_openligadb(frauen[0])],
                                                       "dfbPokal", "women") == [],
                         "unbekannter Wettbewerb: keine Anfrage")
        fehler += pruefe(providers.sportschau_abgleich(2026, [], "bundesliga", "women") == [],
                         "keine Spiele: keine Anfrage")
    finally:
        providers.sportschau_seite = original

    print("Torschuetzinnen aus dem Liveticker")
    ticker = lies("sportschau_liveticker_frankfurt_koeln.html")
    tore, problem = providers.sportschau_tore(ticker, 2, 0)
    fehler += pruefe(problem is None and len(tore) == 2, f"Frankfurt - Koeln: 2 Tore ({problem})")
    fehler += pruefe([(g["scorer"], g["minute"], g["forHome"]) for g in tore]
                     == [("Laura Freigang", 12, True), ("Laura Freigang", 45, True)],
                     "Name voll ausgeschrieben, Minute, Seite")
    fehler += pruefe(not any(g["isPenalty"] or g["isOwnGoal"] for g in tore)
                     and [g["order"] for g in tore] == [0, 1], "keine Sonderfaelle, Reihenfolge")
    fehler += pruefe(providers.sportschau_tore(ticker, 2, 1)[0] == []
                     and "Endstand" in providers.sportschau_tore(ticker, 2, 1)[1],
                     "Tore passen nicht zum Endstand: nicht uebernehmen")
    # Gebaute Faelle - die Schussarten Elfmeter und Eigentor kamen in den
    # geprueften Spielen nicht vor, hier steht nur die vorsichtige Behandlung.
    elfer = ticker.replace("event goal right-foot home", "event goal penalty home", 1)
    tore, problem = providers.sportschau_tore(elfer, 2, 0)
    fehler += pruefe(problem is None and tore[0]["isPenalty"] and not tore[1]["isPenalty"],
                     "Elfmeter erkannt")
    kopf = ticker.replace("event goal right-foot home", "event goal head home", 1)
    tore, problem = providers.sportschau_tore(kopf, 2, 0)
    fehler += pruefe(problem is None and len(tore) == 2, f"Kopfball (Klasse head) ist bekannt ({problem})")
    eigen = ticker.replace("event goal right-foot home", "event goal own-goal home", 1)
    tore, problem = providers.sportschau_tore(eigen, 2, 0)
    fehler += pruefe(tore == [] and "Eigentor" in problem, "Eigentor wird nicht uebernommen")
    fremd = ticker.replace("event goal right-foot home", "event goal bicycle-kick home", 1)
    tore, problem = providers.sportschau_tore(fremd, 2, 0)
    fehler += pruefe(tore == [] and "unbekannte Torart" in problem, "unbekannte Schussart wird nicht uebernommen")
    fehler += pruefe(providers.sportschau_tore("<html></html>", 0, 0)[0] == [], "Seite ohne Ereignisliste")
    nachspiel = ticker.replace('<div class="match_event-minute">45.</div>',
                               '<div class="match_event-minute">45.<span class="extra-minute">+2</span></div>')
    fehler += pruefe(providers.sportschau_tore(nachspiel, 2, 0)[0][1]["minute"] == 47,
                     "Nachspielzeit 45+2 = 47")

    print("sportschau_torschuetzinnen")
    md1 = lies("sportschau_bundesliga_frauen_spieltag1.html")
    uebersicht = lies("sportschau_bundesliga_frauen_spieltag9.html") + (
        '<a href="/live-und-ergebnisse/fussball/deutschland-bundesliga-frauen/se120974/2026-2027/'
        'ro318374/spieltag/md1/spiele-und-ergebnisse"></a>')
    abgerufen = []

    def html(pfad):
        abgerufen.append(pfad)
        if pfad.endswith("/md1/spiele-und-ergebnisse"):
            return md1
        if pfad.endswith("/ma12306058/eintracht-frankfurt_1-fc-koeln/liveticker"):
            return ticker
        raise OSError("unerwartet: " + pfad)

    spiel = {"id": "20260822-eintrach-fckln", "date": "2026-08-22T12:00:00", "matchday": 1,
             "homeTeam": "Eintracht Frankfurt", "awayTeam": "1. FC Köln",
             "homeScore": 2, "awayScore": 0, "goals": [], "goalsLoaded": False}
    orig_html, orig_seite = providers._sportschau_html, providers.sportschau_seite
    try:
        providers._sportschau_html = html
        providers.sportschau_seite = lambda c, g: uebersicht
        meldungen = []
        out = providers.sportschau_torschuetzinnen(
            2026, {spiel["id"]: spiel}, "bundesliga", "women", log=meldungen.append, pause=0)
        fehler += pruefe(len(out) == 1 and out[0]["goalsLoaded"] and len(out[0]["goals"]) == 2,
                         f"Spiel gefunden, Tore eingetragen ({meldungen})")
        fehler += pruefe(out and out[0]["date"] == spiel["date"] and spiel["goals"] == [],
                         "Kopie: Seed-Daten bleiben, Original unveraendert")
        fehler += pruefe(len(abgerufen) == 2, f"zwei Abrufe: Spieltag und Liveticker ({len(abgerufen)})")

        abgerufen.clear()
        falsch = dict(spiel, homeScore=3)
        out = providers.sportschau_torschuetzinnen(
            2026, {falsch["id"]: falsch}, "bundesliga", "women", pause=0)
        fehler += pruefe(out == [] and len(abgerufen) == 1, "anderer Endstand: kein Treffer, kein Ticker-Abruf")

        abgerufen.clear()
        zwei = {"a": dict(spiel, id="a"), "b": dict(spiel, id="b", date="2026-08-23T12:00:00")}
        out = providers.sportschau_torschuetzinnen(
            2026, zwei, "bundesliga", "women", max_spiele=1, pause=0)
        fehler += pruefe(len(out) == 1, "Obergrenze je Lauf")
    finally:
        providers._sportschau_html, providers.sportschau_seite = orig_html, orig_seite

    print()
    if fehler:
        print(f"{fehler} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
