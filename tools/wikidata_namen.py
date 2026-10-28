#!/usr/bin/env python3
"""Abgekuerzte Torschuetzennamen ueber Wikidata ausschreiben.

OpenLigaDB kuerzt Vornamen von Spielern ab, die es nicht in seinem Bestand
fuehrt ("T. Moffi"). providers.loese_abkuerzung() hilft nur, wenn der Seed
den Namen schon kennt. Fuer alles andere fragt diese Stufe Wikidata.

Warum Wikidata: Die Daten stehen unter CC0, und der Query Service ist fuer
automatisierte Abfragen gedacht. Bis 28.10.2026 kamen ausgeschriebene Namen
aus dem DFB-Datencenter; diesen Weg gibt es nicht mehr.

Ablauf je abgekuerztem Namen:

  1. Volltextsuche nach dem Nachnamen, eingeschraenkt auf Fussballspieler
     (P106 = Q937857) und das Geschlecht des Spiels (P21). Eine Anfrage je
     Nachname und Geschlecht, innerhalb eines Laufs zwischengespeichert.
  2. Kandidat ist, wessen Bezeichnung mit der Initiale beginnt und auf den
     Nachnamen endet - ohne Akzente, ohne Gross/Klein - und wer am Spieltag
     zwischen 15 und 45 Jahre alt war, soweit Wikidata ein Geburtsdatum
     kennt.
  3. Vereinsbeleg: War GENAU EIN Kandidat zum Spieldatum fuer die Mannschaft
     gemeldet, fuer die das Tor zaehlt (P54 mit Beginn P580 und Ende P582),
     wird er uebernommen.
  4. Ohne Vereinsbeleg wird nur uebernommen, wenn es ueberhaupt nur EINEN
     Kandidaten gibt und Wikidata ihn am Spieltag nicht nachweislich bei
     einem anderen Verein fuehrt (abgeschlossener Zeitraum mit Beginn und
     Ende, der das Spieldatum einschliesst) und nicht jede seiner Stationen
     vor dem Spieltag endete (Namensvetter, der laengst aufgehoert hat).

Stufe 4 ist noetig, weil die Vereinsstationen bei Wikidata oft luecken-
haft sind. In einer Stichprobe ueber Torschuetzen der Saison 2026/27 fuehrte
Wikidata fuer Derry Scherhant gar keinen Verein, fuer Tim Skarke nur
Heidenheim, fuer Rebecka Blomqvist nur Haecken. Mit Vereinsbeleg allein
waeren 2 von 14 Namen aufgeloest worden. Stufe 4 setzt dafuer auf die
Seltenheit des Namens: Bei "Hahn" gibt es zwei Spielerinnen mit C., beide
bleiben dann abgekuerzt.

Alles andere bleibt abgekuerzt und steht als PRUEFEN-Zeile im Protokoll.
Dieselbe Haltung wie bei loese_abkuerzung(): Ein abgekuerzter, aber
richtiger Name ist harmloser als ein geratener falscher. Welche Stufe
gegriffen hat, steht bei jeder Korrektur dabei ("Verein" oder "nur Name").

Ein Ausfall von Wikidata ist ein Hinweis, kein Abbruch (Regel 4 zielt auf
kaputte Daten, nicht auf ausgebliebene Ergaenzungen).
"""
import json, re, time, unicodedata, urllib.parse, urllib.request

from seedkit import zerlege_abkuerzung

ENDPUNKT = "https://query.wikidata.org/sparql"
# Die Wikimedia-Richtlinie verlangt einen User-Agent mit Kontaktmoeglichkeit.
# Ohne ihn antwortet der Query Service mit 403.
KOPF = {
    "User-Agent": "EintrachtHeute-SeedBot/1.0 "
                  "(https://github.com/wombat-26/eintracht-heute-data)",
    "Accept": "application/sparql-results+json",
}
FUSSBALLSPIELER = "Q937857"
GESCHLECHT = {"men": "Q6581097", "women": "Q6581072"}

# Hoechstens so viele Anfragen je Lauf. Im Normalbetrieb kommen pro Lauf
# eine Handvoll neuer Namen an; die Grenze faengt Ausreisser ab, etwa einen
# Nachhol-Lauf ueber eine ganze Saison.
MAX_ABFRAGEN = 15

# Nur Nachnamen aus Buchstaben, Bindestrich, Apostroph und Leerzeichen. Was
# OpenLigaDB sonst liefert ("K.-P. Boateng" zerfaellt in "K" und
# "-P. Boateng"), ist keine Suchanfrage wert.
# Stufe 4 (siehe Kopf) ausschalten, wenn nur belegte Vereinszugehoerigkeit
# zaehlen soll: dann bleiben alle Namen ohne Vereinsbeleg abgekuerzt.
OHNE_VEREINSBELEG = True

# Alter am Spieltag, in dem ein Profi-Torschuetze plausibel ist.
ALTER_VON, ALTER_BIS = 15, 45

# Auswahlmannschaften laufen bei Wikidata ebenfalls unter P54, parallel zum
# Verein. Sie sagen nichts darueber, bei welchem Verein jemand war.
_AUSWAHL = re.compile(r"national|auswahl|olympi|\bu[ -]?\d{2}\b", re.I)

_NACHNAME = re.compile(r"[^\W\d_]+(?:[ '’-][^\W\d_]+)*")
_QID = re.compile(r"Q\d+")

# Vereinsnamen tragen bei den Quellen unterschiedlich viel Beiwerk: Der Seed
# fuehrt "TSG Hoffenheim" und "SV Elversberg", Wikidata "TSG 1899 Hoffenheim"
# und "SV 07 Elversberg". Verglichen wird deshalb ueber die tragenden Woerter.
# Bewusst kurz gehalten: "Borussia", "Eintracht" oder "Union" bleiben drin,
# sonst waere Dortmund dasselbe wie Moenchengladbach.
_FUELLWOERTER = {"fc", "sv", "vfl", "vfb", "tsg", "tsv", "sc", "fsv", "ssv",
                 "spvgg", "ffc", "ev"}

ABFRAGE = """SELECT ?spieler ?spielerLabel ?geburt ?vereinLabel ?von ?bis WHERE {
  SERVICE wikibase:mwapi {
    bd:serviceParam wikibase:endpoint "www.wikidata.org" ;
                    wikibase:api "Search" ;
                    mwapi:srsearch "%(suche)s" ;
                    mwapi:srlimit "max" .
    ?spieler wikibase:apiOutputItem mwapi:title .
  }
  ?spieler wdt:P21 wd:%(geschlecht)s .
  OPTIONAL { ?spieler wdt:P569 ?geburt }
  OPTIONAL {
    ?spieler p:P54 ?st .
    ?st ps:P54 ?verein .
    OPTIONAL { ?st pq:P580 ?von }
    OPTIONAL { ?st pq:P582 ?bis }
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,mul,en". }
}"""


# Buchstaben, die Unicode nicht in Grundzeichen und Akzent zerlegt.
_ZUSAMMENGESETZT = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "ł": "l",
                                  "đ": "d", "ð": "d", "ı": "i", "þ": "th"})


def norm(text):
    """Vergleichsform: ohne Akzente, ohne Gross/Klein, Leerraum zusammengezogen.

    Die Quellen setzen Akzente unterschiedlich ("Dōan", "Doan"); casefold
    macht ausserdem aus "ß" ein "ss". "ø" und "æ" zerlegt Unicode nicht, sie
    stehen deshalb in einer eigenen Tabelle ("Højlund" = "Hojlund").
    """
    zerlegt = unicodedata.normalize("NFKD", text or "")
    ohne = "".join(c for c in zerlegt if not unicodedata.combining(c))
    return " ".join(ohne.casefold().translate(_ZUSAMMENGESETZT).split())


def vereinswoerter(name):
    return {w for w in re.findall(r"[a-z]+", norm(name))
            if len(w) > 1 and w not in _FUELLWOERTER}


def verein_passt(team, vereinslabel):
    """Steht hinter beiden Namen derselbe Verein?

    Alle tragenden Woerter des Seed-Namens muessen in der Wikidata-
    Bezeichnung vorkommen. Damit passt "Eintracht Frankfurt" auch auf
    "Eintracht Frankfurt (Frauen)", "Borussia Dortmund" aber nicht auf
    "Borussia Mönchengladbach".
    """
    soll = vereinswoerter(team)
    return bool(soll) and soll <= vereinswoerter(vereinslabel)


def im_zeitraum(datum, von, bis):
    """datum, von, bis als "JJJJ-MM-TT"; fehlende Grenzen sind offen."""
    return (not von or von <= datum) and (not bis or datum <= bis)


def alter_passt(geburt, datum):
    """Spielte am Spieltag jemand dieses Geburtsdatums noch/schon im
    Profialter? Ohne (lesbares) Geburtsdatum gilt: ja."""
    try:
        alter = int(datum[:4]) - int(geburt[:4])
    except (TypeError, ValueError):
        return True
    return ALTER_VON <= alter <= ALTER_BIS


def anderer_verein(team, datum, vereine):
    """Fuehrt Wikidata jemanden am Spieltag nachweislich woanders?

    Nachweis heisst: abgeschlossener Zeitraum (Beginn UND Ende), der den
    Spieltag einschliesst. Ein Verein ohne Enddatum beweist nichts - bei
    Wikidata fehlt es oft auch bei laengst beendeten Stationen.
    """
    return any(
        von and bis and von <= datum <= bis
        and not _AUSWAHL.search(v) and not verein_passt(team, v)
        for v, von, bis in vereine)


def karriere_beendet(datum, vereine):
    """Endeten alle Vereinsstationen vor dem Spieltag?

    Dann ist der Kandidat entweder nicht mehr aktiv oder bei einem Verein,
    den Wikidata noch nicht kennt. In beiden Faellen taugt er nicht als
    einziger Treffer ohne Vereinsbeleg: Ein Namensvetter, der laengst aufgehoert
    hat, soll nicht den eigentlichen Torschuetzen ersetzen, nur weil dessen
    Eintrag fehlt. Ohne jede Station oder mit einer laufenden bleibt die
    Frage offen (False).
    """
    stationen = [(v, von, bis) for v, von, bis in vereine if not _AUSWAHL.search(v)]
    return bool(stationen) and all(bis and bis < datum for _, _, bis in stationen)


def name_passt(label, initial, nachname):
    if not label or _QID.fullmatch(label) or zerlege_abkuerzung(label):
        return False
    n = norm(label)
    return n.startswith(norm(initial)) and n.endswith(" " + norm(nachname))


def _hole(abfrage, timeout=30):
    url = ENDPUNKT + "?" + urllib.parse.urlencode({"query": abfrage, "format": "json"})
    req = urllib.request.Request(url, headers=KOPF)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _sparql_text(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def lies_antwort(daten):
    """SPARQL-JSON ->
    {qid: {"name": str, "geburt": "JJJJ-MM-TT" | None, "vereine": [(label, von, bis)]}}"""
    out = {}
    for b in daten.get("results", {}).get("bindings", []):
        qid = b["spieler"]["value"].rsplit("/", 1)[-1]
        eintrag = out.setdefault(qid, {
            "name": b.get("spielerLabel", {}).get("value", ""),
            "geburt": None, "vereine": []})
        geburt = (b.get("geburt") or {}).get("value", "")[:10]
        if geburt and not eintrag["geburt"]:
            eintrag["geburt"] = geburt
        if "vereinLabel" in b:
            station = (
                b["vereinLabel"]["value"],
                (b.get("von") or {}).get("value", "")[:10] or None,
                (b.get("bis") or {}).get("value", "")[:10] or None)
            # Die Abfrage liefert jede Station je Geburtsdatum erneut.
            if station not in eintrag["vereine"]:
                eintrag["vereine"].append(station)
    return out


class Aufloeser:
    """Loest abgekuerzte Namen auf. Ein Objekt je Lauf: Es merkt sich die
    Antworten und zaehlt die Anfragen gegen MAX_ABFRAGEN.

    `holen` und `pause` sind fuer Tests austauschbar."""

    def __init__(self, holen=None, max_abfragen=MAX_ABFRAGEN, pause=1.0):
        self._holen = holen or _hole
        self._max = max_abfragen
        self._pause = pause
        self._cache = {}
        self.abfragen = 0
        # Sobald Wikidata einmal nicht antwortet, fragt dieser Lauf nicht
        # weiter: sonst kostete jeder offene Name einen vollen Timeout, und
        # ein Query Service, der gerade drosselt (429), bekaeme noch mehr.
        self._ausfall = None

    def _kandidaten(self, nachname, gender):
        schluessel = (norm(nachname), gender)
        if schluessel in self._cache:
            return self._cache[schluessel]
        if self.abfragen >= self._max:
            return None
        if self.abfragen:
            time.sleep(self._pause)
        self.abfragen += 1
        abfrage = ABFRAGE % {
            "suche": _sparql_text(f"{nachname} haswbstatement:P106={FUSSBALLSPIELER}"),
            "geschlecht": GESCHLECHT[gender],
        }
        self._cache[schluessel] = lies_antwort(self._holen(abfrage))
        return self._cache[schluessel]

    def aufloesen(self, name, team, datum, gender):
        """-> (ausgeschriebener Name, "Q..., Verein" | "Q..., nur Name")
        oder (None, Grund)."""
        zerlegt = zerlege_abkuerzung(name)
        if not zerlegt:
            return None, "nicht abgekuerzt"
        initial, nachname = zerlegt
        if gender not in GESCHLECHT:
            return None, f"Geschlecht {gender!r} unbekannt"
        if not _NACHNAME.fullmatch(nachname):
            return None, "Nachname nicht suchbar"
        if self._ausfall:
            return None, self._ausfall
        try:
            eintraege = self._kandidaten(nachname, gender)
        except Exception as e:
            # Netz, HTTP-Fehler, Timeout, kaputtes JSON: Alles dasselbe fuer
            # den Aufrufer - die Namen bleiben abgekuerzt.
            self._ausfall = f"Wikidata nicht erreichbar – {e}"
            return None, self._ausfall
        if eintraege is None:
            return None, f"Grenze von {self._max} Abfragen je Lauf erreicht"

        namentlich = {q: e for q, e in eintraege.items()
                      if name_passt(e["name"], initial, nachname)
                      and alter_passt(e["geburt"], datum)}
        if not namentlich:
            return None, "kein Kandidat bei Wikidata"
        treffer = {q: e for q, e in namentlich.items()
                   if any(verein_passt(team, v) and im_zeitraum(datum, von, bis)
                          for v, von, bis in e["vereine"])}
        if len(treffer) == 1:
            qid, e = next(iter(treffer.items()))
            return e["name"], f"{qid}, Verein"
        if len(treffer) > 1:
            return None, f"mehrdeutig – {_liste(treffer)}"
        # Kein Vereinsbeleg. Namensvettern, die Wikidata nachweislich woanders
        # fuehrt oder deren Karriere vorbei ist, scheiden aus; uebrig bleiben
        # darf hoechstens ein einziger Kandidat.
        if OHNE_VEREINSBELEG:
            moeglich = {q: e for q, e in namentlich.items()
                        if not anderer_verein(team, datum, e["vereine"])
                        and not karriere_beendet(datum, e["vereine"])}
            if len(moeglich) == 1:
                qid, e = next(iter(moeglich.items()))
                return e["name"], f"{qid}, nur Name"
            if len(namentlich) == 1:
                qid, e = next(iter(namentlich.items()))
                if anderer_verein(team, datum, e["vereine"]):
                    return None, (f"{e['name']} ({qid}) war am {datum} nachweislich "
                                  f"bei einem anderen Verein als {team}")
                return None, (f"{e['name']} ({qid}): alle Stationen bei Wikidata "
                              f"enden vor dem {datum}")
        return None, (f"kein Vereinsbeleg für {team} am {datum} – "
                      f"{_liste(namentlich)}")


def _liste(kandidaten):
    return ", ".join(f"{e['name']} ({q})" for q, e in sorted(kandidaten.items()))


def schreibe_aus(matches, aufloeser, log=None):
    """Ersetzt abgekuerzte Torschuetzennamen an Ort und Stelle.
    -> (korrekturen, offen) als Listen von (id, alt, neu_oder_grund)

    Die Mannschaft eines Torschuetzen ist die, fuer die das Tor zaehlt -
    ausser beim Eigentor, das zaehlt fuer den Gegner.
    """
    protokoll = log or (lambda s: None)
    korrekturen, offen = [], []
    for m in matches:
        datum = (m.get("date") or "")[:10]
        for g in m.get("goals") or []:
            alt = g.get("scorer") or ""
            if not zerlege_abkuerzung(alt):
                continue
            fuer_heim = bool(g.get("forHome")) != bool(g.get("isOwnGoal"))
            team = m.get("homeTeam") if fuer_heim else m.get("awayTeam")
            neu, info = aufloeser.aufloesen(alt, team or "", datum, m.get("gender"))
            if neu:
                g["scorer"] = neu
                korrekturen.append((m["id"], alt, f"{neu} ({info})"))
                protokoll(f"  Wikidata {m['id']}: {alt} → {neu} ({info})")
            else:
                offen.append((m["id"], alt, info))
                protokoll(f"  PRUEFEN: {m['id']} {alt} ({team}) – {info}")
    return korrekturen, offen
