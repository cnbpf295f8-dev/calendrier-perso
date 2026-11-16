#!/usr/bin/env python3
"""Génère docs/calendrier.ics : 1 à 3 faits par jour, 0 les jours fériés français."""
import json, random, re, time, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

RACINE = Path(__file__).parent
P = json.loads((RACINE / "profil.json").read_text(encoding="utf-8"))
UA = {"User-Agent": "calendrier-perso/1.0 (https://github.com/cnbpf295f8-dev/calendrier-perso; usage personnel)"}
_stats_http = {"echecs": 0}
_cache_recherche = {}
_cache_resume = {}


def get_json(url):
    for essai in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
            time.sleep(0.3)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            attente = 5 * (essai + 1)
            if e.code == 429:
                try:
                    attente = int(e.headers.get("Retry-After", attente))
                except Exception:
                    pass
            time.sleep(min(attente, 30))
        except Exception:
            time.sleep(2 * (essai + 1))
    _stats_http["echecs"] += 1
    return None


# ---------- Jours fériés français ----------
def paques(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois = (h + l - 7 * m + 114) // 31
    jour = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, mois, jour)


def feries(y):
    p = paques(y)
    return {date(y, 1, 1), p + timedelta(1), date(y, 5, 1), date(y, 5, 8),
            p + timedelta(39), p + timedelta(50), date(y, 7, 14),
            date(y, 8, 15), date(y, 11, 1), date(y, 11, 11), date(y, 12, 25)}


# ---------- Faits liés à la date (Wikipédia "Ce jour-là") ----------
_cache = {}


def items_du_jour(d):
    cle = (d.month, d.day)
    if cle in _cache:
        return _cache[cle]
    url = f"https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/all/{d.month:02d}/{d.day:02d}"
    data = get_json(url) or {}
    items = []
    for k in ("selected", "events", "births", "deaths"):
        for it in data.get(k, []):
            pages = it.get("pages") or []
            p = pages[0] if pages else {}
            texte = it.get("text", "")
            extra = " ".join((q.get("description") or "") + " " + (q.get("extract") or "") for q in pages)
            items.append({
                "annee": it.get("year"),
                "texte": texte,
                "blob": (texte + " " + extra).lower(),
                "titre": (p.get("titles") or {}).get("normalized") or texte[:70],
                "lien": ((p.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
            })
    _cache[cle] = items
    return items


def correspond(item, mots):
    return any(re.search(r"\b" + re.escape(m.lower()) + r"\b", item["blob"]) for m in mots)


def choisir_theme(rng, pool):
    noms = [n for n, t in pool.items() if t["poids"] > 0]
    poids = [pool[n]["poids"] for n in noms]
    return rng.choices(noms, weights=poids)[0]


def couper(txt, longueur):
    limite = 220 if longueur == "court" else 650
    if len(txt) <= limite:
        return txt
    coupe = txt[:limite]
    i = coupe.rfind(". ")
    return coupe[: i + 1] if i > 60 else coupe.rstrip() + "…"


# ---------- Faits hors date (Wikipédia, recherche par thème) ----------
def fait_hors_date(rng, mots, used):
    q = rng.choice(mots)
    if q not in _cache_recherche:
        data = get_json("https://fr.wikipedia.org/w/api.php?" + urlencode({
            "action": "query", "list": "search", "srsearch": q,
            "srlimit": 20, "format": "json"}))
        if data is None:
            return None
        _cache_recherche[q] = [r["title"] for r in data.get("query", {}).get("search", [])]
    titres = [t for t in _cache_recherche[q] if t not in used]
    rng.shuffle(titres)
    for t in titres[:5]:
        if t not in _cache_resume:
            r = get_json("https://fr.wikipedia.org/api/rest_v1/page/summary/" + quote(t.replace(" ", "_"), safe=""))
            if r is not None:
                _cache_resume[t] = r
        s = _cache_resume.get(t)
        if s and s.get("type") == "standard" and s.get("extract"):
            return {"titre": s.get("title", t), "texte": s["extract"], "annee": None,
                    "lien": ((s.get("content_urls") or {}).get("desktop") or {}).get("page", "")}
    return None


def fait_secours(d, rng, used):
    """Dernier recours : un fait lié à la date, pour garantir au moins 1 fait par jour."""
    items = [it for it in items_du_jour(d) if it["titre"] not in used]
    if not items:
        return None
    tous = {**P["themes"], **P["themes_surprise"]}
    prefere = [(n, it) for it in items for n, t in tous.items() if correspond(it, t["mots_cles"])]
    if prefere:
        nom, it = rng.choice(prefere)
    else:
        nom, it = "Hors thème", rng.choice(items)
    fait = dict(it)
    fait.update(theme=nom, surprise=False, date_exacte=True)
    fait["texte"] = couper(fait["texte"], "court")
    if isinstance(fait["annee"], int):
        fait["texte"] = f"Il y a {d.year - fait['annee']} ans : {fait['texte']}"
    return fait


def tirer(d, rng, used):
    surprise = rng.random() < P["part_surprise"]
    pool = P["themes_surprise"] if surprise else P["themes"]
    nom = choisir_theme(rng, pool)
    longueur = "long" if rng.random() < P["part_faits_longs"] else "court"
    veut_date = rng.random() < P["part_date_exacte"]
    fait = None
    if veut_date:
        ordre = [nom] + [n for n in pool if n != nom]
        for n in ordre:
            cands = [it for it in items_du_jour(d)
                     if correspond(it, pool[n]["mots_cles"]) and it["titre"] not in used]
            if cands:
                fait = dict(rng.choice(cands))
                nom = n
                fait["date_exacte"] = True
                break
    if fait is None:
        fait = fait_hors_date(rng, pool[nom]["mots_cles"], used)
        if fait is None:
            return None
        fait["date_exacte"] = False
    fait["theme"] = nom
    fait["surprise"] = surprise
    fait["texte"] = couper(fait["texte"], longueur)
    if fait["date_exacte"] and isinstance(fait["annee"], int):
        fait["texte"] = f"Il y a {d.year - fait['annee']} ans : {fait['texte']}"
    return fait


# ---------- Écriture du fichier .ics ----------
def esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def plier(ligne):
    b = ligne.encode("utf-8")
    parts, limite = [], 75
    while len(b) > limite:
        cut = limite
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(b[:cut].decode("utf-8"))
        b = b[cut:]
        limite = 74
    parts.append(b.decode("utf-8"))
    return "\r\n ".join(parts)


def vevent(d, k, f, maintenant):
    uid = f"{d:%Y%m%d}-{k}@calendrier-perso"
    repo = P["depot_github"]

    def vote(sens):
        return f"https://github.com/{repo}/issues/new?title=" + quote(f"vote|{sens}|{f['theme']}|{uid}")

    if f["date_exacte"] and isinstance(f.get("annee"), int):
        resume = f"📅 {f['annee']} · {f['titre']}"
    else:
        resume = f"{'📅' if f['date_exacte'] else '💡'} {f['titre']}"
    desc = f["texte"] + "\n\n"
    if f.get("lien"):
        desc += f["lien"] + "\n"
    desc += f"Thème : {f['theme']}\n👍 {vote('up')}\n👎 {vote('down')}"
    lignes = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{maintenant}",
              f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(1):%Y%m%d}",
              f"SUMMARY:{esc(resume)}", f"DESCRIPTION:{esc(desc)}", "TRANSP:TRANSPARENT"]
    if f.get("lien"):
        lignes.append(f"URL:{f['lien']}")
    lignes.append("END:VEVENT")
    return [plier(x) for x in lignes]


def main():
    debut = date.today() - timedelta(days=P["jours_passes"])
    maintenant = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sortie = [plier(x) for x in ["BEGIN:VCALENDAR", "VERSION:2.0",
              "PRODID:-//calendrier-perso//FR", "CALSCALE:GREGORIAN",
              "X-WR-CALNAME:Faits du jour", "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
              "X-PUBLISHED-TTL:PT12H"]]
    used, total, exacts, sans_fait = set(), 0, 0, []
    for i in range(P["jours_passes"] + P["jours_a_venir"] + 1):
        d = debut + timedelta(days=i)
        if d in feries(d.year):
            continue
        rng = random.Random(f"{d.isoformat()}|{P['graine']}")
        n = rng.randint(P["min_par_jour"], P["max_par_jour"])
        avant = total
        for k in range(n):
            fait = None
            for _ in range(5):
                fait = tirer(d, rng, used)
                if fait:
                    break
            if not fait and k == 0:
                fait = fait_secours(d, rng, used)
            if not fait:
                continue
            used.add(fait["titre"])
            total += 1
            exacts += fait["date_exacte"]
            sortie += vevent(d, k, fait, maintenant)
        if total == avant:
            sans_fait.append(d.isoformat())
    sortie.append("END:VCALENDAR")
    dossier = RACINE / "docs"
    dossier.mkdir(exist_ok=True)
    (dossier / "calendrier.ics").write_text("\r\n".join(sortie) + "\r\n", encoding="utf-8")
    pct = round(100 * exacts / total) if total else 0
    print(f"{total} faits écrits, dont {pct} % liés à la date exacte.")
    print(f"Jours sans aucun fait : {len(sans_fait)} {sans_fait[:10]}")
    print(f"Requêtes échouées : {_stats_http['echecs']}")


if __name__ == "__main__":
    main()
