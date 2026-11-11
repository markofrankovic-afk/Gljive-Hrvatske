#!/usr/bin/env python3
"""Traži zamjenske fotografije sa slobodnom licencom (CC BY, CC BY-SA, CC0, javno dobro)
na Wikimedia Commonsu za vrste čija je sadašnja fotografija CC BY-NC.
Rezultat: out/<id>__<n>.jpg + out/kandidati.json (autor, licenca, izvor)."""
import json, os, re, sys, time, urllib.parse, urllib.request
UA = {"User-Agent": "GljivarkoFoto/1.0 (https://github.com/markofrankovic-afk/Gljive-Hrvatske)"}
VRSTE = {"lisicarka": "Cantharellus cibarius", "lazna-lisicarka": "Hygrophoropsis aurantiaca", "sumporaca": "Hypholoma fasciculare",
         "x-inocybe-sindonia": "Inocybe sindonia", "x-mycena-abramsii": "Mycena abramsii"}
def get(url, raw=False):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                d = r.read(); return d if raw else json.loads(d)
        except Exception as e:
            print("  ponavljam", e); time.sleep(3 + 3 * i)
    return None
def api(host, **p):
    p.update(format="json", formatversion="2")
    return get("https://%s/w/api.php?%s" % (host, urllib.parse.urlencode(p)))
def clean(s): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()
os.makedirs("out", exist_ok=True); res = {}
for vid, lat in VRSTE.items():
    print(vid, lat); files = []
    for host in ("en.wikipedia.org", "de.wikipedia.org", "hr.wikipedia.org", "fr.wikipedia.org"):
        d = api(host, action="query", titles=lat, redirects="1", prop="pageimages", piprop="name")
        for pg in (d or {}).get("query", {}).get("pages", []):
            if pg.get("pageimage") and pg["pageimage"] not in files: files.append(pg["pageimage"])
    d = api("commons.wikimedia.org", action="query", list="search", srsearch='"%s" filetype:bitmap' % lat, srnamespace="6", srlimit="12")
    for h in (d or {}).get("query", {}).get("search", []):
        f = h["title"].split(":", 1)[1]
        if f not in files and f.lower().endswith((".jpg", ".jpeg")): files.append(f)
    n = 0; res[vid] = []
    for f in files:
        if n >= 6: break
        d = api("commons.wikimedia.org", action="query", titles="File:" + f, prop="imageinfo", iiprop="url|size|extmetadata", iiurlwidth="800")
        try: ii = d["query"]["pages"][0]["imageinfo"][0]
        except Exception: continue
        m = ii.get("extmetadata", {}); lic = clean(m.get("LicenseShortName", {}).get("value")); u = lic.upper()
        if not (u.startswith("CC BY") or u.startswith("CC0") or "PUBLIC DOMAIN" in u) or "NC" in u or "ND" in u: print("  preskačem", f, lic); continue
        if ii.get("width", 0) < 700 or ii.get("width", 0) < ii.get("height", 0) * 0.9: print("  preskačem (format)", f); continue
        img = get(ii["thumburl"], raw=True)
        if not img: continue
        n += 1; name = "%s__%d.jpg" % (vid, n); open("out/" + name, "wb").write(img)
        res[vid].append({"datoteka": name, "commons": f, "autor": clean(m.get("Artist", {}).get("value"))[:120], "licencija": lic,
                         "izvor": ii.get("descriptionurl"), "opis": clean(m.get("ImageDescription", {}).get("value"))[:200]})
        print("  +", name, lic, f); time.sleep(1)
json.dump(res, open("out/kandidati.json", "w"), ensure_ascii=False, indent=1)
