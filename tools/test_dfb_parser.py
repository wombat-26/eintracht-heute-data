#!/usr/bin/env python3
"""Offline-Test des DFB-Parsers gegen echtes, gespeichertes HTML.

Lauf: python3 test_dfb_parser.py   (aus tools/)

Bewusst ohne Netz und ohne Testframework - die Fixtures in tests/fixtures
sind unveraenderte Abzuege von datencenter.dfb.de. Schlaegt der Test fehl,
nachdem er einmal lief, hat entweder der Parser oder das DFB-Layout sich
geaendert; im zweiten Fall gehoeren neue Abzuege her.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers

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


def main():
    fehler = 0

    # --- Spielplan: eine Anfrage, ganze Saison ---
    print("Vereinsspielplan 2026/27")
    fixtures = providers.dfb_fixtures(lies("dfb_spielplan_2026.html"))
    fehler += pruefe(len(fixtures) == 26, f"26 Spiele erkannt (gefunden: {len(fixtures)})")

    erste = next((f for f in fixtures if f["dfbId"] == "2426960"), None)
    fehler += pruefe(erste is not None, "Spiel 2426960 im Spielplan")
    if erste:
        fehler += pruefe(erste["date"] == "2026-08-22T12:00:00", f"Datum {erste['date']}")
        fehler += pruefe(erste["homeTeam"] == "Eintracht Frankfurt", f"Heim {erste['homeTeam']}")
        fehler += pruefe(erste["awayTeam"] == "1. FC Köln", f"Gast {erste['awayTeam']}")
        fehler += pruefe((erste["homeScore"], erste["awayScore"]) == (2, 0),
                         f"Ergebnis {erste['homeScore']}:{erste['awayScore']}")
        fehler += pruefe(erste["matchday"] == 1, f"Spieltag {erste['matchday']}")
        # Die Anstosszeit steht als "12:00 Uhr" ueber dem Ergebnis - sie darf
        # nicht als Spielstand durchgehen.
        fehler += pruefe(erste["kickoffText"] == "12:00", "Anstosszeit nicht mit Ergebnis verwechselt")

    ungespielt = [f for f in fixtures if f["homeScore"] is None]
    fehler += pruefe(len(ungespielt) > 0, f"{len(ungespielt)} Spiele ohne Ergebnis erkannt")

    # --- Torliste ---
    print("Spielschema 2426960 (Eintracht – 1. FC Köln 2:0)")
    tore = providers.dfb_tore(lies("dfb_spiel_2426960.html"))
    fehler += pruefe(len(tore) == 2, f"2 Tore (gefunden: {len(tore)})")
    if len(tore) == 2:
        fehler += pruefe(all(t["scorer"] == "Laura Freigang" for t in tore),
                         "Torschuetzin ausgeschrieben, ohne Komma-Form")
        fehler += pruefe([t["minute"] for t in tore] == [12, 45],
                         f"Minuten {[t['minute'] for t in tore]}")
        fehler += pruefe(all(t["forHome"] for t in tore), "beide Tore der Heimmannschaft")
        fehler += pruefe([t["order"] for t in tore] == [0, 1], "order fortlaufend")
        fehler += pruefe(not any(t["isPenalty"] or t["isOwnGoal"] for t in tore),
                         "keine falschen Elfmeter-/Eigentor-Flags")

    # --- Elfmeter: steht nur als Klammerzusatz im Text, nicht als Symbol ---
    print("Spielschema mit Elfmeter")
    tore_e = providers.dfb_tore(lies("dfb_spiel_elfmeter.html"))
    fehler += pruefe(len(tore_e) > 0, f"{len(tore_e)} Tore erkannt")
    fehler += pruefe(any(t["isPenalty"] for t in tore_e), "mindestens ein Elfmeter erkannt")
    fehler += pruefe(all(t["scorer"] and t["scorer"] != "–" for t in tore_e),
                     "jede Torschuetzin mit Namen")
    for t in tore_e:
        if t["isPenalty"]:
            print(f"     Elfmeter: {t['minute']}' {t['scorer']} (forHome={t['forHome']})")

    # --- Heim/Auswaerts: Das Spiel endete 4:3, die Verteilung muss stimmen ---
    print("Spielschema mit Toren auf beiden Seiten (Eintracht – RB Leipzig 4:3)")
    tore_b = providers.dfb_tore(lies("dfb_spiel_beide_seiten.html"))
    heim = sum(1 for t in tore_b if t["forHome"])
    gast = sum(1 for t in tore_b if not t["forHome"])
    fehler += pruefe((heim, gast) == (4, 3), f"Verteilung {heim}:{gast} entspricht dem Endstand")
    fehler += pruefe([t["minute"] for t in tore_b] == sorted(t["minute"] for t in tore_b),
                     "Tore in zeitlicher Reihenfolge")

    # --- Nachspielzeit: "90+2" muss 92 ergeben, nicht 90 ---
    # OpenLigaDB zaehlt durch. Wuerde hier abgeschnitten, waere ein Tor nach
    # einem DFB-Abgleich schlechter datiert als vorher.
    print("Spielschema mit Tor in der Nachspielzeit (Union – Eintracht 3:3)")
    tore_n = providers.dfb_tore(lies("dfb_spiel_nachspielzeit.html"))
    fehler += pruefe(len(tore_n) == 6, f"6 Tore (gefunden: {len(tore_n)})")
    fehler += pruefe([t["minute"] for t in tore_n] == [3, 10, 11, 67, 79, 92],
                     f"Minuten {[t['minute'] for t in tore_n]}")
    fehler += pruefe(any(t["scorer"] == "Tim Skarke" for t in tore_n),
                     "abgekuerzter Vorname ausgeschrieben (T. Skarke → Tim Skarke)")

    # --- Abgleich OpenLigaDB gegen DFB ---
    # Grundlage ist der gespeicherte Spielplan. Daraus werden "OpenLigaDB"-
    # Datensaetze gebaut und gezielt verfaelscht; der Abgleich muss genau
    # die Verfaelschung melden und sonst schweigen.
    print("Abgleich gegen den DFB-Spielplan")
    from seedkit import make_id
    zeilen = providers.dfb_fixtures(lies("dfb_spielplan_2026.html"))

    def als_openligadb(f, **aenderung):
        m = {"id": make_id(f["date"], f["homeTeam"], f["awayTeam"]),
             "date": f["date"], "matchday": f["matchday"],
             "homeTeam": f["homeTeam"], "awayTeam": f["awayTeam"],
             "homeScore": f["homeScore"], "awayScore": f["awayScore"]}
        m.update(aenderung)
        return m

    sauber = [als_openligadb(f) for f in zeilen]
    fehler += pruefe(providers.dfb_abweichungen(sauber, zeilen) == [],
                     "identische Daten: keine Meldung")

    gespielt = next(f for f in zeilen if f["homeScore"] is not None)
    offen = next(f for f in zeilen if f["homeScore"] is None)

    abw = providers.dfb_abweichungen(
        [als_openligadb(offen, date=offen["date"][:10] + "T15:30:00")], zeilen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin",
                     f"falsche Anstosszeit gemeldet ({abw})")

    abw = providers.dfb_abweichungen(
        [als_openligadb(gespielt, homeScore=gespielt["homeScore"] + 1)], zeilen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Ergebnis",
                     f"falsches Ergebnis gemeldet ({abw})")

    abw = providers.dfb_abweichungen(
        [als_openligadb(offen, awayTeam="Phantom FC")], zeilen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Paarung",
                     f"unbekannte Paarung gemeldet ({abw})")

    # Nicht terminierte Spieltage: Platzhalter im Zeitraum ist in Ordnung,
    # ausserhalb nicht.
    zr = next((f for f in zeilen if f.get("zeitraum")), None)
    fehler += pruefe(zr is not None, "Zeitraum im Spielplan erkannt"
                     + (f" ({zr['zeitraum'][0]} bis {zr['zeitraum'][1]})" if zr else ""))
    if zr:
        von, bis = zr["zeitraum"]
        fehler += pruefe(providers.dfb_abweichungen(
            [als_openligadb(zr, date=von + "T15:30:00")], zeilen) == [],
            "Platzhalter im Zeitraum ist keine Abweichung")
        abw = providers.dfb_abweichungen(
            [als_openligadb(zr, date="2027-06-30T15:30:00")], zeilen)
        fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin",
                         "Platzhalter ausserhalb des Zeitraums gemeldet")

    # Keine Meldung, wenn eine Seite nur das Datum kennt ...
    abw = providers.dfb_abweichungen(
        [als_openligadb(offen, date=offen["date"][:10] + "T00:00:00")], zeilen)
    fehler += pruefe(abw == [], "fehlende Uhrzeit ist keine Abweichung")
    # ... und keine, wenn OpenLigaDB einen Zwischenstand fuehrt und der DFB
    # noch "-:-" zeigt.
    abw = providers.dfb_abweichungen(
        [als_openligadb(offen, homeScore=1, awayScore=0)], zeilen)
    fehler += pruefe(abw == [], "Zwischenstand ohne DFB-Ergebnis ist keine Abweichung")

    # --- Torschuetzinnen gegen den DFB ---
    print("Torschuetzinnen gegen den DFB")
    dfb_4_3 = providers.dfb_tore(lies("dfb_spiel_beide_seiten.html"))
    seed_4_3 = [dict(g) for g in dfb_4_3]
    fehler += pruefe(providers.tore_abweichungen(seed_4_3, dfb_4_3) == [],
                     "identische Listen: keine Meldung")

    # Die Quellen setzen Akzente unterschiedlich: OpenLigaDB "Erëleta",
    # DFB "Ereleta". Das ist dieselbe Spielerin.
    mit_akzent = [dict(g) for g in dfb_4_3]
    for g in mit_akzent:
        if g["scorer"] == "Ereleta Memeti":
            g["scorer"] = "Erëleta Memeti"
    fehler += pruefe(any(g["scorer"] == "Erëleta Memeti" for g in mit_akzent)
                     and providers.tore_abweichungen(mit_akzent, dfb_4_3) == [],
                     "Akzentunterschied ist keine Abweichung")

    falsch = [dict(g) for g in dfb_4_3]
    falsch[0]["scorer"] = "Laura Freigang"
    abw = providers.tore_abweichungen(falsch, dfb_4_3)
    fehler += pruefe(len(abw) == 1 and abw[0][0] == "Torschuetzin",
                     f"falscher Name gemeldet ({abw})")

    fehler += pruefe(len(providers.tore_abweichungen(seed_4_3[:-1], dfb_4_3)) == 1
                     and providers.tore_abweichungen(seed_4_3[:-1], dfb_4_3)[0][0] == "Anzahl Tore",
                     "fehlendes Tor als Anzahl gemeldet, nicht als Folgefehler")

    seite = [dict(g) for g in dfb_4_3]
    seite[0]["forHome"] = not seite[0]["forHome"]
    abw = providers.tore_abweichungen(seite, dfb_4_3)
    fehler += pruefe(len(abw) == 1 and abw[0][0] == "Mannschaft",
                     "falsche Mannschaft gemeldet")

    knapp = [dict(g) for g in dfb_4_3]
    knapp[0]["minute"] += 1
    weit = [dict(g) for g in dfb_4_3]
    weit[0]["minute"] += 5
    fehler += pruefe(providers.tore_abweichungen(knapp, dfb_4_3) == [],
                     "eine Minute Unterschied ist keine Abweichung")
    fehler += pruefe([a[0] for a in providers.tore_abweichungen(weit, dfb_4_3)] == ["Minute"],
                     "fuenf Minuten Unterschied gemeldet")

    kurz = [dict(g) for g in dfb_4_3]
    kurz[0]["scorer"] = "E. Memeti"
    fehler += pruefe(providers.tore_abweichungen(kurz, dfb_4_3) == [],
                     "Abkuerzung ist Sache des Tor-Nachtrags, keine Meldung")

    # --- Die Saison-Slugs aus dem Auswahlfeld, Grundlage der Slug-Suche ---
    print("Saison-Erkennung")
    fehler += pruefe(providers._dfb_saison_passt("google-pixel-frauen-bundesliga-2026-2027", 2026),
                     "aktueller Slug-Aufbau")
    fehler += pruefe(providers._dfb_saison_passt("2022-23", 2022), "alter Slug-Aufbau")
    fehler += pruefe(not providers._dfb_saison_passt("2022-23", 2026), "falsche Saison wird verworfen")

    print()
    if fehler:
        print(f"{fehler} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
