"""Colab bilježnica: po jedna reprezentativna fotografija za vrste atlasa bez slike (i zamjena NC slika).
   python3 tools/foto/make_foto_nb.py -> tools/foto/GljiveHrvatske_fotografije.ipynb"""
import json, os, re
HERE = os.path.dirname(os.path.abspath(__file__))
NEED = json.load(open(os.path.join(HERE, "need.json")))
src = open(os.path.join(HERE, "..", "trening", "make_nb.py")).read()
ISPR = re.search(r"ISPRAVCI = (\{.*?\n\})", src, re.S).group(1)

cells = []
def md(t): cells.append(("markdown", t.strip("\n")))
def code(t): cells.append(("code", t.strip("\n")))

md("""
# 🍄 Gljive Hrvatske – fotografije za atlas

Za svaku vrstu bez fotografije uzima **glavnu fotografiju vrste s iNaturalista** (onu koju zajednica postavi kao reprezentativnu), a ako je nema, najbolje ocijenjenu provjerenu fotografiju nalaza ili fotografiju s **Wikimedia Commonsa**. Uzima samo licence **CC0, CC BY i CC BY-SA** (dopuštaju i komercijalnu upotrebu uz navođenje autora).

**Pokretanje:** Runtime → **Run all**. GPU nije potreban. Traje oko 20–30 minuta.
Na kraju se skine **atlas_foto.zip** (oko 20 MB), a kopija ostaje na Google Driveu u mapi *GljiveModel*. Taj zip pošalji Claudeu.
""")

code("""
import json, time, io, re, zipfile, html as _html
from pathlib import Path
import requests
from PIL import Image
try:
    from google.colab import drive
    drive.mount('/content/drive')
    OUT = Path('/content/drive/MyDrive/GljiveModel')
except ImportError:
    OUT = Path('GljiveModel')
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path('/content/foto' if Path('/content').exists() else 'foto_work'); WORK.mkdir(exist_ok=True)
S = requests.Session(); S.headers['User-Agent'] = 'GljiveHrvatske-atlas/1.0 (github.com/markofrankovic-afk/Gljive-Hrvatske)'
API = 'https://api.inaturalist.org/v1/'
LIC_OK = ['cc0', 'cc-by', 'cc-by-sa']
LIC_TXT = {'cc0': 'CC0', 'cc-by': 'CC BY', 'cc-by-sa': 'CC BY-SA'}
def api(path, **params):
    for k in range(5):
        try:
            r = S.get(API + path, params=params, timeout=60); time.sleep(0.7)
            if r.status_code == 200: return r.json()
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(20 * (k + 1)); continue
            return None
        except requests.RequestException:
            time.sleep(15 * (k + 1))
    return None
TAKS_F = OUT / 'taksoni.json'
TAKS = json.loads(TAKS_F.read_text()) if TAKS_F.exists() else {}
print('Spremljenih taksona s Drivea:', len(TAKS))
""")

code("TREBA = json.loads(" + repr(json.dumps(NEED, ensure_ascii=False)) + ")\nISPRAVCI = " + ISPR + "\nprint(len(TREBA), 'vrsta treba fotografiju')")

code("""
def autor_iz(attr):
    a = attr or ''
    m = re.search(r'\\(c\\)\\s*(.+?),\\s*(some|all|no) rights', a)
    if m: return m.group(1).strip()
    m = re.search(r'^(.+?),\\s*no known copyright', a)
    return (m.group(1) if m else a).strip() or 'nepoznat autor'

def takson(lat):
    t = TAKS.get(lat)
    if t: return t['id']
    q = ISPRAVCI.get(lat, lat)
    for path in ('taxa', 'taxa/autocomplete'):
        j = api(path, q=q, per_page=20) or {}
        for r in j.get('results', []):
            if r.get('iconic_taxon_name') not in (None, 'Fungi', 'Protozoa'): continue
            if r.get('name', '').lower() == q.lower() or (r.get('matched_term') or '').lower() == q.lower():
                return r['id']
    return None

def iz_inat(tid):
    t = ((api(f'taxa/{tid}') or {}).get('results') or [None])[0]
    if t:
        for tp in t.get('taxon_photos') or []:
            p = tp.get('photo') or {}
            if p.get('license_code') in LIC_OK and p.get('url'):
                return {'url': p['url'].replace('/square.', '/medium.'), 'autor': autor_iz(p.get('attribution')),
                        'licencija': LIC_TXT[p['license_code']], 'izvor': f"https://www.inaturalist.org/photos/{p['id']}", 'put': 'iNat glavna'}
    j = api('observations', taxon_id=tid, quality_grade='research', photos='true', photo_license=','.join(LIC_OK),
            order_by='votes', order='desc', per_page=10) or {}
    for o in j.get('results', []):
        for p in o.get('photos', []):
            if p.get('license_code') in LIC_OK and p.get('url'):
                return {'url': p['url'].replace('/square.', '/medium.'), 'autor': autor_iz(p.get('attribution')),
                        'licencija': LIC_TXT[p['license_code']], 'izvor': f"https://www.inaturalist.org/observations/{o['id']}", 'put': 'iNat nalaz'}
    return None

CAPI = 'https://commons.wikimedia.org/w/api.php'
LOSE = re.compile(r'micro|spore|basidi|cystid|herbar|drawing|illustrat|plate|tafel|stamp|map|distribution|\\.svg|\\.tif', re.I)
def iz_commons(lat):
    q = ISPRAVCI.get(lat, lat)
    for k in range(4):
        try:
            r = S.get(CAPI, params={'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6,
                      'gsrsearch': f'"{q}" filetype:bitmap', 'gsrlimit': 15, 'prop': 'imageinfo',
                      'iiprop': 'url|extmetadata', 'iiurlwidth': 640}, timeout=60); time.sleep(1)
            if r.status_code == 200: break
            time.sleep(20 * (k + 1))
        except requests.RequestException: time.sleep(15)
    else: return None
    pages = sorted((r.json().get('query') or {}).get('pages', {}).values(), key=lambda p: p.get('index', 99))
    for p in pages:
        t = p.get('title', '')
        if LOSE.search(t) or q.split()[0].lower() not in t.lower(): continue
        ii = (p.get('imageinfo') or [{}])[0]; em = ii.get('extmetadata') or {}
        lic = (em.get('LicenseShortName', {}).get('value') or '')
        if re.search(r'NC|ND|GFDL$', lic) or not re.search(r'CC0|CC BY|Public domain|PD', lic, re.I): continue
        autor = re.sub('<[^>]+>', '', _html.unescape(em.get('Artist', {}).get('value') or '')).strip() or 'nepoznat autor'
        return {'url': ii.get('thumburl') or ii.get('url'), 'autor': autor[:120], 'licencija': lic,
                'izvor': ii.get('descriptionurl'), 'put': 'Commons'}
    return None

def spremi(url, f):
    for k in range(3):
        try:
            r = S.get(url, timeout=60)
            if r.status_code != 200: time.sleep(5); continue
            im = Image.open(io.BytesIO(r.content)).convert('RGB')
            w, h = im.size; tw = min(w, h * 4 / 3); th = tw * 3 / 4
            im = im.crop(((w - tw) / 2, (h - th) / 2, (w + tw) / 2, (h + th) / 2)).resize((400, 300), Image.LANCZOS)
            im.save(f, 'JPEG', quality=82, optimize=True); return True
        except Exception:
            time.sleep(5)
    return False
""")

code("""
IDX_F = WORK / 'index_novo.json'
IDX = json.loads(IDX_F.read_text()) if IDX_F.exists() else {}
NEMA = []
for i, v in enumerate(TREBA):
    if v['id'] in IDX and (WORK / (v['id'] + '.jpg')).exists(): continue
    tid = takson(v['lat'])
    p = iz_inat(tid) if tid else None
    if not p: p = iz_commons(v['lat'])
    if p and spremi(p['url'], WORK / (v['id'] + '.jpg')):
        IDX[v['id']] = {k: p[k] for k in ('autor', 'licencija', 'izvor', 'put')}
    else:
        NEMA.append(v['lat'])
    if i % 25 == 0 or i == len(TREBA) - 1:
        IDX_F.write_text(json.dumps(IDX, ensure_ascii=False))
        print(f'{i+1}/{len(TREBA)}', v['lat'], '->', p and p['put'])
IDX_F.write_text(json.dumps(IDX, ensure_ascii=False))
print('Pronađeno', len(IDX), '| bez fotografije', len(NEMA)); print(NEMA[:50])
""")

code("""
ZIP = OUT / 'atlas_foto.zip'
with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_STORED) as z:
    z.writestr('index_novo.json', json.dumps(IDX, ensure_ascii=False, indent=0))
    z.writestr('nema.json', json.dumps(NEMA, ensure_ascii=False))
    for k in IDX: z.write(WORK / (k + '.jpg'), 'foto/' + k + '.jpg')
print('✅ Gotovo:', ZIP, round(ZIP.stat().st_size / 1e6, 1), 'MB — pošalji Claudeu datoteku atlas_foto.zip')
try:
    from google.colab import files; files.download(str(ZIP))
except Exception:
    pass
""")

nb = {"nbformat": 4, "nbformat_minor": 5,
      "metadata": {"colab": {"provenance": [], "name": "GljiveHrvatske_fotografije.ipynb"},
                   "kernelspec": {"name": "python3", "display_name": "Python 3"}, "language_info": {"name": "python"}},
      "cells": []}
for i, (kind, s) in enumerate(cells):
    lines = s.split("\n"); lines = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    c = {"cell_type": kind, "metadata": {}, "source": lines, "id": f"c{i:02d}"}
    if kind == "code": c.update(execution_count=None, outputs=[])
    nb["cells"].append(c)
json.dump(nb, open(os.path.join(HERE, "GljiveHrvatske_fotografije.ipynb"), "w"), ensure_ascii=False, indent=1)
print("ok", len(NEED), "vrsta,", len(cells), "ćelija")
