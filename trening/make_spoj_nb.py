"""Colab bilježnica: kako spojiti više fotografija istog nalaza (bez treniranja).
   python3 make_spoj_nb.py -> GljiveHrvatske_spajanje.ipynb"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
cells = []
def md(t): cells.append(("markdown", t.strip("\n")))
def code(t): cells.append(("code", t.strip("\n")))

md("""
# 🍄 Gljive Hrvatske – kako spojiti više fotografija

Bez treniranja. Uzima nalaze s iNaturalista na kojima model **v6 nije učio** (isti 10 % nalaza koji su bili izdvojeni za provjeru) i koji imaju **dvije fotografije istog primjerka**. Za svaki nalaz model procijeni obje fotografije točno kao aplikacija (5 pogleda po slici), a zatim se uspoređuju načini spajanja:

* **prosjek** (kako aplikacija radi sada)
* **množenje** (geometrijska sredina: slike se moraju složiti)
* **prva vodi** (prva fotografija ima dvostruku težinu)
* **sigurnija slika** (uzima se fotografija u kojoj je model sigurniji)

**Pokretanje:** Runtime → **Run all**. GPU nije potreban. Traje oko 25–35 minuta (najdulje čitanje fotografija s Drivea).
Na kraju se ispiše tablica i skine **spajanje_rezultati.zip**. Pošalji Claudeu tablicu (snimku) i taj zip.
""")

code("""
%pip install -q onnxruntime
import json, io, os, hashlib, tarfile, zipfile, random, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
import onnxruntime as ort
try:
    from google.colab import drive
    drive.mount('/content/drive')
    OUT = Path('/content/drive/MyDrive/GljiveModel'); WORK = Path('/content/spoj')
except ImportError:
    OUT = Path(os.environ.get('GH_OUT', 'GljiveModel')); WORK = Path(os.environ.get('GH_WORK', 'spoj_work'))
WORK.mkdir(parents=True, exist_ok=True)
NAJVISE_NALAZA = 1200   # koliko nalaza s dvije fotografije testiramo
PO_VRSTI = 3            # najviše nalaza po vrsti, da test pokrije što više vrsta
random.seed(7)
print('Drive:', OUT, '| postoji:', OUT.exists())
""")

code("""
# ----- 1. Model v6 (isti kao u aplikaciji) -----
zp = OUT / 'GljiveModel_v6.zip'
assert zp.exists(), 'Nema GljiveModel_v6.zip u mapi GljiveModel na Driveu'
M = WORK / 'model'; M.mkdir(exist_ok=True)
with zipfile.ZipFile(zp) as z: z.extractall(M)
meta = json.loads((M / 'meta.json').read_text()); LABELS = json.loads((M / 'labels.json').read_text())
dijelovi = sorted(M.glob('model.onnx.*'))
with open(WORK / 'model.onnx', 'wb') as f:
    for d in dijelovi: f.write(d.read_bytes())
so = ort.SessionOptions(); so.intra_op_num_threads = os.cpu_count() or 2
SES = ort.InferenceSession(str(WORK / 'model.onnx'), so, providers=['CPUExecutionProvider'])
INP = SES.get_inputs()[0].name
S = meta['input_size'][2]; CROP = meta.get('crop_pct', 0.875)
MEAN = np.array(meta['mean'], np.float32)[:, None, None]; STD = np.array(meta['std'], np.float32)[:, None, None]
print('Model', meta.get('model'), '| razreda', len(LABELS), '| ulaz', S, 'px')
""")

code("""
# ----- 2. Nalazi s dvije fotografije koje model nije vidio pri učenju -----
TAKS = json.loads((OUT / 'taksoni.json').read_text())
def u_provjeri(oid): return int(hashlib.md5(str(oid).encode()).hexdigest(), 16) % 10 == 0
NALAZI = []
for li, ime in enumerate(LABELS):
    t = TAKS.get(ime)
    if not t: continue
    f = OUT / 'popis' / f"{t['id']}.json"
    if not f.exists(): continue
    c = json.loads(f.read_text()); fot = c['fotke'] if isinstance(c, dict) else c
    po = {}
    for p in fot:
        if str(p['pid']).startswith('c') or not u_provjeri(p['oid']): continue
        po.setdefault(p['oid'], []).append(p['pid'])
    dva = [(oid, ps[:2]) for oid, ps in po.items() if len(ps) >= 2]
    random.shuffle(dva)
    for oid, ps in dva[:PO_VRSTI]: NALAZI.append({'oid': oid, 'pid': ps, 'y': li, 'ime': ime})
random.shuffle(NALAZI); NALAZI = NALAZI[:NAJVISE_NALAZA]
print('Nalaza s dvije fotografije:', len(NALAZI), '| vrsta:', len({n['y'] for n in NALAZI}))
""")

code("""
# ----- 3. Izvadi samo potrebne fotografije iz slike.tar (čitanje s Drivea traje nekoliko minuta) -----
SL = WORK / 'slike'; SL.mkdir(exist_ok=True)
treba = {f"{p}.jpg" for n in NALAZI for p in n['pid']}
ima = {f.name for f in SL.glob('*.jpg')}
if not treba <= ima:
    t0 = time.time(); nadeno = 0
    with tarfile.open(OUT / 'slike.tar', 'r|') as t:
        for m in t:
            nm = m.name.rsplit('/', 1)[-1]
            if nm in treba and m.isfile():
                (SL / nm).write_bytes(t.extractfile(m).read()); nadeno += 1
    print('Izvađeno', nadeno, 'fotografija za', round((time.time() - t0) / 60, 1), 'min')
NALAZI = [n for n in NALAZI if all((SL / f"{p}.jpg").exists() for p in n['pid'])]
print('Spremno nalaza:', len(NALAZI))
""")

code("""
# ----- 4. Procjena svake fotografije točno kao u aplikaciji (5 pogleda: cijela, zrcalno, 3 izreza) -----
VIEWS = [dict(), dict(flip=1), dict(zoom=1.4), dict(zoom=0.85), dict(zoom=0.7)]
def view(img, v):
    z = v.get('zoom', 1); R = round(S / CROP) * z; k = R / min(img.size)
    w, h = round(img.width * k), round(img.height * k)
    im = img.resize((w, h), Image.BICUBIC)
    c = Image.new('RGB', (S, S), (0, 0, 0)); c.paste(im, ((S - w) // 2, (S - h) // 2))
    if v.get('flip'): c = ImageOps.mirror(c)
    a = (np.asarray(c, np.float32) / 255.0).transpose(2, 0, 1)
    return (a - MEAN) / STD
def softmax(x): x = x - x.max(); e = np.exp(x); return e / e.sum()
def procjena(path):
    img = Image.open(path).convert('RGB'); acc = np.zeros(len(LABELS), np.float64)
    for v in VIEWS:
        out = SES.run(None, {INP: view(img, v)[None]})[0][0]
        acc += softmax(out.astype(np.float64))
    return acc / len(VIEWS)
P1 = np.zeros((len(NALAZI), len(LABELS)), np.float32); P2 = np.zeros_like(P1); Y = np.array([n['y'] for n in NALAZI])
t0 = time.time()
for i, n in enumerate(NALAZI):
    P1[i] = procjena(SL / f"{n['pid'][0]}.jpg"); P2[i] = procjena(SL / f"{n['pid'][1]}.jpg")
    if i % 100 == 0: print(f'{i}/{len(NALAZI)}', round((time.time() - t0) / 60, 1), 'min')
print('Gotovo za', round((time.time() - t0) / 60, 1), 'min')
""")

code("""
# ----- 5. Usporedba načina spajanja -----
def norm(p): return p / p.sum(1, keepdims=True)
eps = 1e-9
NACINI = {
  'samo prva fotografija': P1,
  'prosjek (sada)': norm(P1 + P2),
  'množenje': norm(np.exp((np.log(P1 + eps) + np.log(P2 + eps)) / 2)),
  'prva vodi (2:1)': norm(np.exp((2 * np.log(P1 + eps) + np.log(P2 + eps)) / 3)),
  'sigurnija slika': np.where((P1.max(1) >= P2.max(1))[:, None], P1, P2),
}
def ocjena(P):
    o = np.argsort(-P, 1); top1 = o[:, 0] == Y; top3 = (o[:, :3] == Y[:, None]).any(1)
    p1 = P[np.arange(len(P)), o[:, 0]]; p2 = P[np.arange(len(P)), o[:, 1]]
    nes = (p1 < 0.4) | (p2 >= 0.6 * p1)          # približno pravilo „Nisam siguran“ iz aplikacije
    sig = ~nes
    return dict(top1=top1.mean(), top3=top3.mean(), nesiguran=nes.mean(),
                tocno_kad_siguran=(top1[sig].mean() if sig.any() else 0), krivo_a_siguran=(sig & ~top1).mean())
print(f"{'način':26s} {'točno':>7s} {'među 3':>7s} {'nesiguran':>10s} {'točno kad siguran':>18s} {'krivo a siguran':>16s}")
REZ = {}
for k, P in NACINI.items():
    r = ocjena(P); REZ[k] = {a: round(float(b), 4) for a, b in r.items()}
    print(f"{k:26s} {r['top1']*100:6.1f}% {r['top3']*100:6.1f}% {r['nesiguran']*100:9.1f}% {r['tocno_kad_siguran']*100:17.1f}% {r['krivo_a_siguran']*100:15.1f}%")
print('\\nNalaza:', len(NALAZI))
""")

code("""
# ----- 6. Spremi rezultate za Claudea (top 40 vjerojatnosti po fotografiji) -----
def top(p, k=40):
    i = np.argsort(-p)[:k]; return [[int(a), round(float(p[a]), 5)] for a in i]
data = {'labels': LABELS, 'rez': REZ, 'nalazi': [{'oid': n['oid'], 'y': int(n['y']), 'p1': top(P1[i]), 'p2': top(P2[i])} for i, n in enumerate(NALAZI)]}
ZIP = OUT / 'spajanje_rezultati.zip'
with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED) as z: z.writestr('spajanje.json', json.dumps(data))
print('✅ Gotovo:', ZIP, round(ZIP.stat().st_size / 1e6, 2), 'MB — pošalji Claudeu tablicu iznad i datoteku spajanje_rezultati.zip')
try:
    from google.colab import files; files.download(str(ZIP))
except Exception:
    pass
""")

nb = {"nbformat": 4, "nbformat_minor": 5,
      "metadata": {"colab": {"provenance": [], "name": "GljiveHrvatske_spajanje.ipynb"},
                   "kernelspec": {"name": "python3", "display_name": "Python 3"}, "language_info": {"name": "python"}},
      "cells": []}
for i, (kind, s) in enumerate(cells):
    lines = s.split("\n"); lines = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    c = {"cell_type": kind, "metadata": {}, "source": lines, "id": f"c{i:02d}"}
    if kind == "code": c.update(execution_count=None, outputs=[])
    nb["cells"].append(c)
json.dump(nb, open(os.path.join(HERE, "GljiveHrvatske_spajanje.ipynb"), "w"), ensure_ascii=False, indent=1)
print("ok", len(cells), "ćelija")
