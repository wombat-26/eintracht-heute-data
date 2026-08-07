#!/usr/bin/env python3
"""Prototyp: Merge-/Validierungs-/Delta-Logik fuer die GitHub Action.
Bildet MatchStore.applyUpsert() und SeedLoader/Competition 1:1 nach."""
import json, re, hashlib, collections
from datetime import datetime, timezone, timedelta

COMPETITIONS = {"oberliga","meisterschaft","bundesliga","bundesliga2","dfbPokal",
                "championsLeague","europacup","supercup","ligaPokal","friendly","other"}
GENDERS = {"men","women"}
MATCH_FIELDS = ["id","date","kickoffText","competition","season","matchday","homeTeam",
                "awayTeam","homeScore","awayScore","halftimeHome","halftimeAway",
                "isFinished","goalsLoaded","note","sourceUrl","goals","gender"]
GOAL_FIELDS = ["minute","scorer","forHome","isPenalty","isOwnGoal","order"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
ID_RE   = re.compile(r"^\d{8}-[a-z]{1,8}-[a-z]{1,8}$")

# ---------- ID / Slug (identisch zu OpenLigaDBProvider.makeSeedCompatibleID) ----------
def slug(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())[:8]

def make_id(date_str: str, home: str, away: str) -> str:
    """date_str = 'YYYY-MM-DDTHH:MM:SS' in Europe/Berlin (Ortszeit)."""
    return f"{date_str[:10].replace('-','')}-{slug(home)}-{slug(away)}"

# ---------- Zeitzone Europe/Berlin ohne externe Abhaengigkeit ----------
def _last_sunday(year, month):
    d = datetime(year, month, 31) if month != 6 else datetime(year, month, 30)
    while d.month != month: d -= timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)

def berlin_offset(dt_utc: datetime) -> timedelta:
    """CEST (UTC+2) von letztem So im Maerz 01:00 UTC bis letztem So im Oktober 01:00 UTC."""
    y = dt_utc.year
    start = _last_sunday(y, 3).replace(hour=1)
    end   = _last_sunday(y, 10).replace(hour=1)
    return timedelta(hours=2) if start <= dt_utc.replace(tzinfo=None) < end else timedelta(hours=1)

def utc_to_berlin_str(iso_utc: str) -> str:
    """'2026-08-05T17:00Z' oder '...T17:00:00Z' -> '2026-08-05T19:00:00' (Berlin)."""
    s = iso_utc.rstrip("Z")
    fmt = "%Y-%m-%dT%H:%M:%S" if s.count(":") == 2 else "%Y-%m-%dT%H:%M"
    dt = datetime.strptime(s, fmt)
    return (dt + berlin_offset(dt)).strftime("%Y-%m-%dT%H:%M:%S")

# ---------- Validierung ----------
def validate(matches, strict=True):
    errs, warns = [], []
    seen = {}
    for i, m in enumerate(matches):
        w = f"[{i}] {m.get('id','<ohne id>')}"
        for f in MATCH_FIELDS:
            if f not in m: errs.append(f"{w}: Feld '{f}' fehlt")
        mid = m.get("id")
        if not isinstance(mid, str) or not ID_RE.match(mid or ""):
            errs.append(f"{w}: ID-Format ungueltig")
        if mid in seen: errs.append(f"{w}: doppelte ID (auch bei Index {seen[mid]})")
        seen[mid] = i
        d = m.get("date")
        if not isinstance(d, str) or not DATE_RE.match(d or ""):
            errs.append(f"{w}: date '{d}' nicht 'YYYY-MM-DDTHH:MM:SS'")
        else:
            try: datetime.strptime(d, "%Y-%m-%dT%H:%M:%S")
            except ValueError: errs.append(f"{w}: date '{d}' kein gueltiger Zeitpunkt")
            if mid and mid[:8] != d[:10].replace("-", ""):
                errs.append(f"{w}: ID-Datum passt nicht zu date '{d}'")
        if m.get("competition") not in COMPETITIONS:
            errs.append(f"{w}: competition '{m.get('competition')}' NICHT im Swift-Enum -> Decoding der GESAMTEN Datei schlaegt fehl")
        if m.get("gender") not in GENDERS:
            errs.append(f"{w}: gender '{m.get('gender')}' ungueltig")
        for f, t in (("homeTeam",str),("awayTeam",str),("season",str)):
            if not isinstance(m.get(f), t) or not m.get(f):
                errs.append(f"{w}: {f} leer/falscher Typ")
        for f in ("homeScore","awayScore","halftimeHome","halftimeAway","matchday"):
            v = m.get(f)
            if v is not None and not isinstance(v, int): errs.append(f"{w}: {f} weder int noch null")
        for f in ("isFinished","goalsLoaded"):
            if not isinstance(m.get(f), bool): errs.append(f"{w}: {f} kein bool")
        for f in ("note","sourceUrl","kickoffText"):
            v = m.get(f)
            if v is not None and not isinstance(v, str): errs.append(f"{w}: {f} weder string noch null")
        gs = m.get("goals")
        if not isinstance(gs, list): errs.append(f"{w}: goals keine Liste")
        else:
            for j, g in enumerate(gs):
                for f in GOAL_FIELDS:
                    if f not in g: errs.append(f"{w}: goals[{j}] Feld '{f}' fehlt")
                if g.get("order") != j: errs.append(f"{w}: goals[{j}].order={g.get('order')} != Index")
                if not isinstance(g.get("scorer"), str): errs.append(f"{w}: goals[{j}].scorer kein string")
                for f in ("forHome","isPenalty","isOwnGoal"):
                    if not isinstance(g.get(f), bool): errs.append(f"{w}: goals[{j}].{f} kein bool")
                if g.get("minute") is not None and not isinstance(g.get("minute"), int):
                    errs.append(f"{w}: goals[{j}].minute weder int noch null")
        hs, as_ = m.get("homeScore"), m.get("awayScore")
        if (hs is None) != (as_ is None): warns.append(f"{w}: nur eine Halbzeit des Ergebnisses gesetzt")
        if m.get("isFinished") and hs is None: warns.append(f"{w}: isFinished=true ohne Ergebnis")
        if gs and m.get("goalsLoaded") is False: warns.append(f"{w}: hat Tore, aber goalsLoaded=false")
    return errs, warns

# ---------- contentHash (identisch zu MatchStore.contentHash) ----------
def content_hash(matches):
    parts = []
    for f in sorted(matches, key=lambda x: x["id"]):
        hs = str(f["homeScore"]) if f["homeScore"] is not None else "-"
        as_ = str(f["awayScore"]) if f["awayScore"] is not None else "-"
        parts.append(f"{f['id']}|{hs}|{as_}|{len(f.get('goals') or [])}|{f.get('note') or ''}|{f.get('gender') or ''}")
    combined = ";".join(parts)
    h = 0xcbf29ce484222325
    for b in combined.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return str(h)

# ---------- Upsert (identisch zu MatchStore.applyUpsert) ----------
def upsert(existing, incoming):
    """existing=None -> Neuanlage. Gibt (merged, changed:bool) zurueck."""
    if existing is None:
        m = {f: incoming.get(f) for f in MATCH_FIELDS}
        m["goals"] = normalize_goals(incoming.get("goals") or [])
        m["goalsLoaded"] = bool(m["goals"])
        if m.get("isFinished") is None: m["isFinished"] = False
        return m, True
    m = json.loads(json.dumps(existing))
    before = json.dumps(m, sort_keys=True, ensure_ascii=False)

    m["date"] = incoming["date"]                       # Datum immer uebernehmen
    if incoming.get("gender"): m["gender"] = incoming["gender"]
    m["kickoffText"] = incoming.get("kickoffText")     # wird immer gesetzt (auch auf None)

    inc_comp = incoming.get("competition")
    cur = m.get("competition")
    if not cur or cur == "other":
        m["competition"] = inc_comp
    elif cur == "bundesliga" and inc_comp == "bundesliga2":
        pass                                            # nie herabstufen
    # sonst: bestehende Zuordnung behalten

    if not m.get("season"): m["season"] = incoming.get("season")
    if m.get("matchday") is None: m["matchday"] = incoming.get("matchday")
    m["homeTeam"] = incoming["homeTeam"]
    m["awayTeam"] = incoming["awayTeam"]
    for f in ("homeScore","awayScore","halftimeHome","halftimeAway","note","sourceUrl"):
        if incoming.get(f) is not None: m[f] = incoming[f]
    m["isFinished"] = incoming.get("isFinished", m.get("isFinished"))

    ing = normalize_goals(incoming.get("goals") or [])
    if ing:
        new_named = any(g["scorer"].strip() for g in ing)
        old_named = any(g["scorer"].strip() for g in (m.get("goals") or []))
        old = m.get("goals") or []
        replace = (not old) or (new_named and not old_named) or (new_named and len(ing) >= len(old))
        if replace:
            m["goals"] = ing
            m["goalsLoaded"] = True
    return m, json.dumps(m, sort_keys=True, ensure_ascii=False) != before

def normalize_goals(goals):
    out = []
    for i, g in enumerate(goals):
        out.append({"minute": g.get("minute"), "scorer": g.get("scorer") or "–",
                    "forHome": bool(g.get("forHome")), "isPenalty": bool(g.get("isPenalty")),
                    "isOwnGoal": bool(g.get("isOwnGoal")), "order": i})
    return out

# ---------- Fixture-Key (identisch zu MatchStore.fixtureKey, ohne Datum) ----------
def fixture_key(m):
    return "|".join([m.get("season") or "", m.get("competition") or "",
                     m.get("homeTeam") or "", m.get("awayTeam") or "", m.get("gender") or ""])

def is_placeholder(m):
    return m.get("homeScore") is None and m.get("awayScore") is None \
           and not m.get("isFinished") and not (m.get("goals") or [])

# ---------- Merge eines ganzen Laufs ----------
def merge(seed_matches, fetched, prune_moved=True):
    by_id = {m["id"]: m for m in seed_matches}
    stats = collections.Counter()
    for f in fetched:
        cur = by_id.get(f["id"])
        merged, changed = upsert(cur, f)
        if cur is None: stats["neu"] += 1
        elif changed:   stats["geaendert"] += 1
        else:           stats["unveraendert"] += 1
        by_id[f["id"]] = merged
    if prune_moved:
        live = {f["id"] for f in fetched}
        groups = collections.defaultdict(list)
        for m in by_id.values(): groups[fixture_key(m)].append(m)
        for grp in groups.values():
            if len(grp) < 2: continue
            if not any(m["id"] in live for m in grp): continue
            for cand in grp:
                if cand["id"] not in live and is_placeholder(cand):
                    del by_id[cand["id"]]; stats["verschoben_entfernt"] += 1
    return sorted(by_id.values(), key=lambda m: (m["date"], m["id"])), stats

# ---------- Delta ----------
def make_delta(old_matches, new_matches):
    o = {m["id"]: m for m in old_matches}
    n = {m["id"]: m for m in new_matches}
    canon = lambda m: json.dumps({f: m.get(f) for f in MATCH_FIELDS}, sort_keys=True, ensure_ascii=False)
    upserts = [m for i, m in n.items() if i not in o or canon(m) != canon(o[i])]
    removed = sorted(set(o) - set(n))
    return {"upserts": sorted(upserts, key=lambda m: (m["date"], m["id"])), "removed": removed}

def apply_delta(base_matches, delta):
    by_id = {m["id"]: m for m in base_matches}
    for m in delta["upserts"]: by_id[m["id"]] = m
    for i in delta.get("removed", []): by_id.pop(i, None)
    return sorted(by_id.values(), key=lambda m: (m["date"], m["id"]))

def canon_dump(matches):
    return json.dumps({"matches": [{f: m.get(f) for f in MATCH_FIELDS} for m in matches]},
                      ensure_ascii=False, indent=2, sort_keys=False)

# ---------- Plausibilitaetsfilter gegen Datumsdreher ----------
def _gedreht(datum_iso):
    """'2026-08-11' -> '2026-11-08'. None, wenn der Tag > 12 ist und die
    Vertauschung folglich keinen gueltigen Monat ergeben wuerde."""
    y, mo, tg = datum_iso[:10].split("-")
    if not (1 <= int(tg) <= 12):
        return None
    return f"{y}-{tg}-{mo}"


def spieltag_plausibilitaet(fetched):
    """Verwirft Datensaetze, bei denen Tag und Monat vertauscht sind.

    Hintergrund: OpenLigaDB datiert fuenf Spieltage der Frauen-Bundesliga
    2026/27 falsch - der 9. auf den 11.08.2026 statt 08.11.2026, der 17.
    auf den 02.07.2027 statt 07.02.2027, dazu 23, 24 und 25. Betroffen ist
    jeweils der komplette Spieltag (alle sieben Partien), alle stammen aus
    einem Import-Lauf.

    Erkennung ueber Anker: Ein Dreher ist nur moeglich, wenn der Tag <= 12
    ist - sonst ergaebe die Vertauschung keinen gueltigen Monat. Spieltage
    mit Tag > 12 sind also garantiert richtig datiert und dienen als
    Stuetzstellen. Ein kippbarer Spieltag gilt als gedreht, wenn sein Datum
    NICHT zwischen die benachbarten Anker passt, das gedrehte aber schon.

    WICHTIG - ein reiner Monotonie-Test genuegt NICHT (frueherer Ansatz,
    verworfen): Springt ein Dreher nach vorn statt zurueck (07.02. wird zu
    02.07.), erscheinen stattdessen alle FOLGENDEN, korrekten Spieltage als
    Verstoss. Gegen die echten ffb1/2026-Daten haette das sechs echte
    Termine verworfen und zwei Geister durchgelassen. Der Ankertest trifft
    exakt die fuenf betroffenen Spieltage, ohne Fehlalarm.

    Bewusst KEINE automatische Korrektur - das gedrehte Datum waere hier
    zwar richtig, bliebe aber geraten. Datensaetze mit Ergebnis bleiben
    immer unangetastet.

    Rueckgabe: (uebernehmen, verworfen, verdaechtige_spieltage)
    """
    termin = {}
    for f in fetched:
        md = f.get("matchday")
        if md is None:
            continue
        d = f["date"][:10]
        termin[md] = min(termin[md], d) if md in termin else d
    if len(termin) < 2:
        return list(fetched), [], []

    anker = {md: d for md, d in termin.items() if _gedreht(d) is None}
    if not anker:
        return list(fetched), [], []      # ohne Stuetzstellen nichts anfassen

    verdaechtig = set()
    for md, d in termin.items():
        alt = _gedreht(d)
        if alt is None:
            continue
        vor  = max((anker[k] for k in anker if k < md), default=None)
        nach = min((anker[k] for k in anker if k > md), default=None)
        passt     = (vor is None or d   > vor) and (nach is None or d   < nach)
        passt_alt = (vor is None or alt > vor) and (nach is None or alt < nach)
        if not passt and passt_alt:
            verdaechtig.add(md)

    ok, verworfen = [], []
    for f in fetched:
        hat_ergebnis = f.get("homeScore") is not None or bool(f.get("goals"))
        if f.get("matchday") in verdaechtig and not hat_ergebnis:
            verworfen.append(f)
        else:
            ok.append(f)
    return ok, verworfen, sorted(verdaechtig)
