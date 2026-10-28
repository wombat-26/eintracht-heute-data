#!/usr/bin/env python3
"""Offline-Test der Namensaufloesung ueber Wikidata.

Lauf: python3 test_wikidata_namen.py   (aus tools/)

Ohne Netz und ohne Testframework. tests/fixtures/wikidata_moffi.json ist die
unveraenderte Antwort des Query Service auf die Abfrage fuer "Moffi"; alle
anderen Antworten werden hier aus kleinen Bausteinen gebaut, damit jeder Fall
sichtbar bleibt, den die Regeln unterscheiden.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikidata_namen as w
from update_seed import wikidata_kandidaten

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures")


def pruefe(bedingung, text):
    if not bedingung:
        print(f"FEHLER: {text}")
        return 1
    print(f"  ok: {text}")
    return 0


def antwort(*spieler):
    """Baut eine SPARQL-Antwort. Spieler = (qid, name, geburt, [(verein, von, bis)])."""
    zeilen = []
    for qid, name, geburt, vereine in spieler:
        for v in vereine or [None]:
            z = {"spieler": {"value": f"http://www.wikidata.org/entity/{qid}"},
                 "spielerLabel": {"value": name}}
            if geburt:
                z["geburt"] = {"value": geburt + "T00:00:00Z"}
            if v:
                z["vereinLabel"] = {"value": v[0]}
                if v[1]:
                    z["von"] = {"value": v[1] + "T00:00:00Z"}
                if v[2]:
                    z["bis"] = {"value": v[2] + "T00:00:00Z"}
            zeilen.append(z)
    return {"results": {"bindings": zeilen}}


class Attrappe:
    """Ersatz fuer das Netz: antwortet je Nachname und zaehlt die Anfragen."""
    def __init__(self, antworten):
        self.antworten, self.anfragen = antworten, []

    def __call__(self, abfrage):
        self.anfragen.append(abfrage)
        for nachname, a in self.antworten.items():
            if f'"{nachname} haswbstatement' in abfrage:
                if isinstance(a, Exception):
                    raise a
                return a
        return antwort()


def loeser(antworten, **kw):
    return w.Aufloeser(holen=Attrappe(antworten), pause=0, **kw)


def ablauf_in_main(echt):
    """update_seed.main() von Anfang bis Ende, Netz ersetzt: Ein Spiel mit
    "T. Moffi" muss ausgeschrieben im geschriebenen Seed landen, und ein
    zweiter Lauf mit derselben OpenLigaDB-Antwort darf ihn nicht wieder
    abkuerzen."""
    import contextlib, io, tempfile
    import update_seed

    print("update_seed.main() mit ersetztem Netz")
    mini = json.load(open(os.path.join(FIX, "mini_seed.json"), encoding="utf-8"))["matches"]
    spiel = {
        "id": "20260912-hamburge-eintrach", "date": "2026-09-12T15:30:00",
        "kickoffText": "15:30", "competition": "bundesliga", "season": "2026/27",
        "matchday": 4, "homeTeam": "Hamburger SV", "awayTeam": "Eintracht Frankfurt",
        "homeScore": 1, "awayScore": 0, "halftimeHome": 1, "halftimeAway": 0,
        "isFinished": True, "goalsLoaded": True, "note": None, "sourceUrl": None,
        "gender": "men",
        "goals": [{"minute": 17, "scorer": "T. Moffi", "forHome": True,
                   "isPenalty": False, "isOwnGoal": False, "order": 0}],
    }
    # Zum Vergleich: ein altes Spiel mit Abkuerzung darf unberuehrt bleiben.
    alt = json.loads(json.dumps(mini[0]))
    alt["goals"][0]["scorer"] = "R. Wischnowsky"

    ordner = tempfile.mkdtemp()
    seedpfad = os.path.join(ordner, "seed.json")
    with open(seedpfad, "w", encoding="utf-8") as f:
        json.dump({"matches": mini[1:] + [alt]}, f, ensure_ascii=False)

    fehler = 0
    ori_sammle, ori_hole, ori_sleep = update_seed.sammle, w._hole, w.time.sleep
    update_seed.sammle = lambda *a, **k: [json.loads(json.dumps(spiel))]
    anfragen = []
    w._hole = lambda q, timeout=30: anfragen.append(q) or echt
    w.time.sleep = lambda s: None
    try:
        for lauf in (1, 2):
            with contextlib.redirect_stdout(io.StringIO()) as aus:
                rc = _lauf(update_seed, seedpfad, ordner)
            geschrieben = json.load(open(os.path.join(ordner, "seed_matches.json"),
                                         encoding="utf-8"))["matches"]
            m = next(x for x in geschrieben if x["id"] == spiel["id"])
            fehler += pruefe(rc == 0, f"Lauf {lauf}: Rueckgabe 0")
            fehler += pruefe(m["goals"][0]["scorer"] == "Terem Moffi",
                             f"Lauf {lauf}: im Seed steht {m['goals'][0]['scorer']}")
            if lauf == 1:
                ausgabe = open(os.path.join(ordner, "output.txt"), encoding="utf-8").read()
                zusammenfassung = open(os.path.join(ordner, "ausgabe.txt"), encoding="utf-8").read()
                fehler += pruefe("1 Namen nach Wikidata" in ausgabe,
                                 "Commit-Text nennt die Wikidata-Korrektur")
                fehler += pruefe("T. Moffi | Terem Moffi" in zusammenfassung,
                                 "Job-Zusammenfassung zeigt alt und neu")
            # Der naechste Lauf startet vom geschriebenen Seed.
            with open(seedpfad, "w", encoding="utf-8") as f:
                json.dump({"matches": geschrieben}, f, ensure_ascii=False)
        fehler += pruefe(len(anfragen) == 1,
                         f"zweiter Lauf fragt nicht erneut ({len(anfragen)} Anfrage(n) insgesamt)")
        a = next(x for x in geschrieben if x["id"] == alt["id"])
        fehler += pruefe(a["goals"][0]["scorer"] == "R. Wischnowsky",
                         "Spiel aus 1960/61 bleibt unberuehrt")
    finally:
        update_seed.sammle, w._hole, w.time.sleep = ori_sammle, ori_hole, ori_sleep
    return fehler


def _lauf(update_seed, seedpfad, ordner):
    ausgabe = os.path.join(ordner, "ausgabe.txt")
    os.environ["GITHUB_OUTPUT"] = os.path.join(ordner, "output.txt")
    os.environ["GITHUB_STEP_SUMMARY"] = ausgabe
    argv = sys.argv
    sys.argv = ["update_seed.py", "--seed", seedpfad, "--out-dir", ordner]
    try:
        return update_seed.main()
    finally:
        sys.argv = argv


def main():
    fehler = 0

    # --- Die echte Antwort: T. Moffi, Hamburger SV, September 2026 ---
    print("Moffi (gespeicherte Antwort des Query Service)")
    echt = json.load(open(os.path.join(FIX, "wikidata_moffi.json"), encoding="utf-8"))
    a = loeser({"Moffi": echt})
    name, info = a.aufloesen("T. Moffi", "Hamburger SV", "2026-09-12", "men")
    fehler += pruefe(name == "Terem Moffi", f"T. Moffi -> {name}")
    fehler += pruefe(info == "Q65261290, Verein", f"Beleg: {info}")

    # Am 2021-03-01 war er laut Wikidata in Lorient. Fuer den HSV also nicht.
    name, info = a.aufloesen("T. Moffi", "Hamburger SV", "2021-03-01", "men")
    fehler += pruefe(name is None and "nachweislich" in info,
                     f"Station bei anderem Verein belegt -> bleibt abgekuerzt ({info})")

    # Vor der ersten Station: kein Beleg, aber auch kein Widerspruch.
    name, info = a.aufloesen("T. Moffi", "Hamburger SV", "2016-03-01", "men")
    fehler += pruefe(name == "Terem Moffi" and info.endswith("nur Name"),
                     f"vor der ersten Station -> nur Name ({info})")

    # Frauen: Dieselbe Abfrage kaeme mit anderem Geschlechtsfilter zurueck.
    fehler += pruefe(all("wd:Q6581097" in q for q in a._holen.anfragen),
                     "Herrenspiel fragt nach P21 = maennlich")
    name, _ = a.aufloesen("T. Moffi", "Hamburger SV", "2026-09-12", "women")
    fehler += pruefe(len(a._holen.anfragen) == 2 and "wd:Q6581072" in a._holen.anfragen[-1],
                     "Frauenspiel fragt gesondert nach P21 = weiblich")

    # --- Zwischenspeicher: ein Nachname, eine Anfrage ---
    print("Zwischenspeicher und Grenze")
    a = loeser({"Moffi": echt})
    for _ in range(3):
        a.aufloesen("T. Moffi", "Hamburger SV", "2026-09-12", "men")
    fehler += pruefe(a.abfragen == 1, f"drei Aufrufe, eine Anfrage ({a.abfragen})")

    a = loeser({}, max_abfragen=2)
    grund = [a.aufloesen(f"A. {n}", "FC X", "2026-09-01", "men")[1]
             for n in ("Alpha", "Beta", "Gamma", "Delta")]
    fehler += pruefe(a.abfragen == 2 and "Grenze" in grund[2],
                     f"nach 2 Anfragen Schluss ({a.abfragen}; {grund[2]})")

    # --- Ausfall: nur ein Versuch, dann Ruhe ---
    print("Ausfall von Wikidata")
    a = loeser({"Alpha": OSError("Timeout"), "Beta": antwort()})
    n1, g1 = a.aufloesen("A. Alpha", "FC X", "2026-09-01", "men")
    n2, g2 = a.aufloesen("B. Beta", "FC X", "2026-09-01", "men")
    fehler += pruefe(n1 is None and "nicht erreichbar" in g1, f"Ausfall gemeldet ({g1})")
    fehler += pruefe(n2 is None and a.abfragen == 1,
                     f"danach keine weitere Anfrage ({a.abfragen})")

    # --- Stufe 3: Vereinsbeleg entscheidet zwischen Namensvettern ---
    print("Mehrere Kandidaten")
    zwei = antwort(
        ("Q1", "Jonas Hahn", "1999-01-01", [("Eintracht Frankfurt", "2020-07-01", None)]),
        ("Q2", "Jan Hahn", "1997-01-01", [("1. FC Köln", "2018-07-01", None)]))
    a = loeser({"Hahn": zwei})
    name, info = a.aufloesen("J. Hahn", "Eintracht Frankfurt", "2026-09-01", "men")
    fehler += pruefe(name == "Jonas Hahn" and info == "Q1, Verein",
                     f"zwei J. Hahn, nur einer bei Frankfurt -> {name}")
    name, info = a.aufloesen("J. Hahn", "FC Augsburg", "2026-09-01", "men")
    fehler += pruefe(name is None and "Vereinsbeleg" in info,
                     f"zwei Kandidaten, kein Verein passt -> offen ({info})")

    beide = antwort(
        ("Q1", "Jonas Hahn", "1999-01-01", [("Eintracht Frankfurt", "2020-07-01", None)]),
        ("Q2", "Jan Hahn", "1997-01-01", [("Eintracht Frankfurt (Frauen)", "2018-07-01", None)]))
    a = loeser({"Hahn": beide})
    name, info = a.aufloesen("J. Hahn", "Eintracht Frankfurt", "2026-09-01", "men")
    fehler += pruefe(name is None and "mehrdeutig" in info,
                     f"zwei Kandidaten bei demselben Verein -> mehrdeutig ({info})")

    # --- Stufe 4: ein Kandidat, Wikidata kennt den Verein nicht ---
    print("Ohne Vereinsbeleg")
    skarke = antwort(("Q3", "Tim Skarke", "1996-07-16", [("1. FC Heidenheim", None, None)]))
    a = loeser({"Skarke": skarke})
    name, info = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name == "Tim Skarke" and info == "Q3, nur Name",
                     f"einziger Kandidat, Station ohne Daten -> {name} ({info})")

    nie_gemeldet = antwort(("Q4", "Derry Scherhant", "2000-01-01", None))
    a = loeser({"Scherhant": nie_gemeldet})
    name, _ = a.aufloesen("D. Scherhant", "Eintracht Frankfurt", "2026-09-01", "men")
    fehler += pruefe(name == "Derry Scherhant", "Spieler ganz ohne Vereinsangabe")

    woanders = antwort(("Q5", "Tim Skarke", "1996-07-16",
                        [("FC Augsburg", "2020-07-01", "2027-06-30")]))
    a = loeser({"Skarke": woanders})
    name, info = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name is None and "nachweislich" in info,
                     "abgeschlossene Station bei anderem Verein am Spieltag -> offen")

    # Offene Station (ohne Ende) beweist nichts: bei Wikidata fehlt das Ende
    # auch bei laengst beendeten Stationen.
    offen = antwort(("Q5", "Tim Skarke", "1996-07-16", [("FC Augsburg", "2020-07-01", None)]))
    a = loeser({"Skarke": offen})
    name, _ = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name == "Tim Skarke", "Station ohne Enddatum ist kein Widerspruch")

    # Auswahlmannschaften laufen parallel zum Verein
    auswahl = antwort(("Q6", "Tim Skarke", "1996-07-16",
                       [("Deutsche Fußballnationalmannschaft", "2020-01-01", "2030-01-01"),
                        ("Deutschland U21", "2015-01-01", "2030-01-01")]))
    a = loeser({"Skarke": auswahl})
    name, _ = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name == "Tim Skarke", "Nationalmannschaft zaehlt nicht als anderer Verein")

    # Ein Namensvetter, der laengst aufgehoert hat, ersetzt keinen Torschuetzen,
    # dessen Eintrag fehlt.
    rentner = antwort(("Q9", "Tim Skarke", "1985-03-03",
                       [("FC Augsburg", "2005-07-01", "2016-06-30")]))
    a = loeser({"Skarke": rentner})
    name, info = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name is None and "enden vor dem" in info,
                     f"einziger Kandidat ohne laufende Station -> offen ({info})")
    fehler += pruefe(w.karriere_beendet("2026-09-01", [("FC X", "2000-01-01", "2010-01-01")])
                     and not w.karriere_beendet("2026-09-01", [])
                     and not w.karriere_beendet("2026-09-01", [("FC X", "2000-01-01", None)])
                     and not w.karriere_beendet("2026-09-01",
                                                [("Deutschland U21", "2000-01-01", "2010-01-01")]),
                     "karriere_beendet: nur bei lauter abgeschlossenen Vereinsstationen")

    # Mehrere Namensvettern: wer nachweislich woanders war oder aufgehoert hat,
    # scheidet aus; bleibt genau einer uebrig, ist er es.
    junger = ("Q10", "Tim Skarke", "2003-01-01", [])
    woanders = ("Q11", "Tom Skarke", "1999-01-01",
                [("FC Augsburg", "2024-07-01", "2027-06-30")])
    pensioniert = ("Q9", "Tim Skarke", "1985-03-03",
                   [("FC Augsburg", "2005-07-01", "2016-06-30")])
    a = loeser({"Skarke": antwort(junger, woanders, pensioniert)})
    name, info = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name == "Tim Skarke" and info == "Q10, nur Name",
                     f"Namensvettern per Verein ausgeschlossen ({name}, {info})")
    zweiter = ("Q12", "Tom Skarke", "2002-02-02", [])
    a = loeser({"Skarke": antwort(junger, zweiter)})
    name, _ = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name is None, "zwei Kandidaten ohne Vereinsdaten bleiben offen")

    # Schalter
    alt = w.OHNE_VEREINSBELEG
    w.OHNE_VEREINSBELEG = False
    try:
        a = loeser({"Skarke": skarke})
        name, info = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    finally:
        w.OHNE_VEREINSBELEG = alt
    fehler += pruefe(name is None and "Vereinsbeleg" in info,
                     "OHNE_VEREINSBELEG = False: nur belegte Vereine")

    # --- Alter ---
    print("Alter am Spieltag")
    kind = antwort(("Q7", "Tim Skarke", "2015-01-01", None))
    a = loeser({"Skarke": kind})
    name, info = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name is None and "kein Kandidat" in info, "11-Jaehriger scheidet aus")
    opa = antwort(("Q8", "Tim Skarke", "1930-01-01", None))
    a = loeser({"Skarke": opa})
    name, _ = a.aufloesen("T. Skarke", "1. FC Union Berlin", "2026-09-01", "men")
    fehler += pruefe(name is None, "96-Jaehriger scheidet aus")
    fehler += pruefe(w.alter_passt(None, "2026-09-01") and w.alter_passt("???", "2026-09-01"),
                     "ohne lesbares Geburtsdatum gilt der Kandidat weiter")

    # --- Namenstreffer ---
    print("Namensvergleich")
    fehler += pruefe(w.name_passt("Erëleta Memeti", "E", "Memeti"), "Akzent im Vornamen")
    fehler += pruefe(w.name_passt("Éric Dier", "E", "Dier"), "Akzent auf der Initiale")
    fehler += pruefe(w.name_passt("Rasmus Højlund", "R", "Hojlund")
                     and w.name_passt("Martin Ødegaard", "M", "Odegaard")
                     and w.name_passt("Łukasz Fabiański", "L", "Fabianski"),
                     "ø, ł und Co. zerlegt Unicode nicht, die Tabelle faengt sie ab")
    fehler += pruefe(not w.name_passt("Mark Uth", "T", "Uth"), "andere Initiale")
    fehler += pruefe(not w.name_passt("Tim Uthmann", "T", "Uth"), "Nachname nur als Wortanfang")
    fehler += pruefe(not w.name_passt("T. Moffi", "T", "Moffi"), "abgekuerztes Label zaehlt nicht")
    fehler += pruefe(not w.name_passt("Q65261290", "T", "Moffi"), "Label ohne Namen (nur QID)")
    fehler += pruefe(w.verein_passt("Eintracht Frankfurt", "Eintracht Frankfurt (Frauen)"),
                     "Verein: Zusatz in Klammern")
    fehler += pruefe(w.verein_passt("TSG Hoffenheim", "TSG 1899 Hoffenheim"),
                     "Verein: Gruendungsjahr im Namen")
    fehler += pruefe(not w.verein_passt("Borussia Dortmund", "Borussia Mönchengladbach"),
                     "Verein: Borussia ist nicht gleich Borussia")

    # --- schreibe_aus ---
    print("Torlisten umschreiben")
    spiele = [{
        "id": "20260912-hamburg-eintrach", "date": "2026-09-12T15:30:00", "gender": "men",
        "homeTeam": "Hamburger SV", "awayTeam": "Eintracht Frankfurt",
        "goals": [
            {"scorer": "T. Moffi", "forHome": True, "isOwnGoal": False},
            {"scorer": "Hugo Larsson", "forHome": False, "isOwnGoal": False},
            # Eigentor: zaehlt fuer Frankfurt, der Spieler spielt beim HSV
            {"scorer": "T. Moffi", "forHome": False, "isOwnGoal": True},
            {"scorer": "K.-P. Boateng", "forHome": False, "isOwnGoal": False},
        ]}]
    a = loeser({"Moffi": echt})
    korr, offen = w.schreibe_aus(spiele, a)
    namen = [g["scorer"] for g in spiele[0]["goals"]]
    fehler += pruefe(namen[:3] == ["Terem Moffi", "Hugo Larsson", "Terem Moffi"],
                     f"Namen ausgeschrieben, volle Namen unberuehrt ({namen})")
    fehler += pruefe(len(korr) == 2, f"zwei Korrekturen ({len(korr)})")
    fehler += pruefe(a.abfragen == 1, "Moffi zweimal, eine Anfrage")
    fehler += pruefe(len(offen) == 1 and offen[0][1] == "K.-P. Boateng",
                     f"Doppelinitiale bleibt offen ({offen})")
    fehler += pruefe(namen[3] == "K.-P. Boateng", "Doppelinitiale unveraendert")

    # --- Auswahl der Spiele ---
    print("Welche Spiele geprueft werden")
    bestand = [
        {"id": "a", "season": "2026/27", "date": "2026-09-01T15:30:00",
         "goals": [{"scorer": "T. Moffi"}]},
        {"id": "b", "season": "2026/27", "date": "2026-10-01T15:30:00",
         "goals": [{"scorer": "T. Skarke"}]},
        {"id": "c", "season": "2025/26", "date": "2026-04-01T15:30:00",
         "goals": [{"scorer": "T. Moffi"}]},
        {"id": "d", "season": "2026/27", "date": "2026-09-15T15:30:00",
         "goals": [{"scorer": "Terem Moffi"}]},
        {"id": "e", "season": "2026/27", "date": "2026-09-20T15:30:00", "goals": []},
        {"id": "f", "season": "", "date": "2026-09-20T15:30:00",
         "goals": [{"scorer": "T. Moffi"}]},
    ]
    ids = [m["id"] for m in wikidata_kandidaten(bestand)]
    fehler += pruefe(ids == ["b", "a"],
                     f"nur neue Saison, nur Abgekuerztes, neueste zuerst ({ids})")

    fehler += ablauf_in_main(echt)

    print()
    if fehler:
        print(f"{fehler} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
