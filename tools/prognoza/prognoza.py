#!/usr/bin/env python3
"""Dnevna prognoza za Gljivarka.

Izvor: ECMWF IFS open data (0,25°, CC BY 4.0) – prognoza do 8 dana unaprijed;
prošli dani: vlastita arhiva (dan 0 svake dnevne prognoze), praznine se nadopunjuju
iz ECMWF arhive na AWS-u (Registry of Open Data).
Visine: Mapzen/Tilezen Terrain Tiles (AWS Open Data).
Granice država: Natural Earth (javno dobro).

Izlaz (u mapu OUT):
  prognoza.json – mreža 0,1° za HR, SI, BA, RS, ME (+ XK), po polju:
     dnevna kiša, srednja/najniža/najviša temperatura za 14 prošlih dana + danas + 7 dana,
     visina, ocjena 0–100 za vrganje, lisičarke, sunčanice i smrčke za danas i 7 dana.
  arhiva.npz – grube dnevne vrijednosti po datumu (za sljedeći dan).
"""
import base64, datetime as dt, io, json, math, os, sys, time, urllib.request
import numpy as np

OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
ARH_IN = sys.argv[2] if len(sys.argv) > 2 else None
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Gljivarko-prognoza/1.0 (github.com/markofrankovic-afk/Gljive-Hrvatske)"}

# ---------------------------------------------------------------- mreže
# gruba (izvor): 0,25°, s rubom; fina (izlaz): 0,1°
CLAT0, CLAT1, CLON0, CLON1, CD = 40.5, 48.5, 11.5, 24.5, 0.25
CLATS = np.round(np.arange(CLAT1, CLAT0 - 1e-9, -CD), 4)       # sjever -> jug
CLONS = np.round(np.arange(CLON0, CLON1 + 1e-9, CD), 4)
FLAT0, FLON0, FD, FROWS, FCOLS = 41.85, 13.35, 0.1, 52, 98      # središta polja; red 0 = jug
FLATS = np.round(FLAT0 + FD * np.arange(FROWS), 4)
FLONS = np.round(FLON0 + FD * np.arange(FCOLS), 4)
PAST, FUT = 21, 8                                                # dana unatrag za račun, dana prognoze
OUT_PAST = 14
DRZAVE = {"HRV", "SVN", "BIH", "SRB", "MNE", "KOS", "XKX"}

def log(*a):
    print(*a, flush=True)

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception as e:
            log("  ponavljam", url[:90], e); time.sleep(3 + 4 * i)
    raise RuntimeError("ne mogu preuzeti " + url)

# ---------------------------------------------------------------- GRIB
def read_grib(path):
    """Vrati {(shortName, step): polje na gruboj mreži [lat, lon]}."""
    import eccodes as ec
    out = {}
    with open(path, "rb") as f:
        while True:
            h = ec.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                sn = ec.codes_get(h, "shortName"); st = int(ec.codes_get(h, "endStep"))
                ni, nj = ec.codes_get(h, "Ni"), ec.codes_get(h, "Nj")
                la1 = ec.codes_get(h, "latitudeOfFirstGridPointInDegrees")
                lo1 = ec.codes_get(h, "longitudeOfFirstGridPointInDegrees")
                di = ec.codes_get(h, "iDirectionIncrementInDegrees"); dj = ec.codes_get(h, "jDirectionIncrementInDegrees")
                v = ec.codes_get_values(h).reshape(nj, ni)
            finally:
                ec.codes_release(h)
            lons = (lo1 + di * np.arange(ni) + 180) % 360 - 180
            lats = la1 - dj * np.arange(nj)
            iy = [int(np.argmin(np.abs(lats - la))) for la in CLATS]
            ix = [int(np.argmin(np.abs(lons - lo))) for lo in CLONS]
            out[(sn, st)] = v[np.ix_(iy, ix)].astype(np.float32)
    return out

def retrieve(source, date, steps, target):
    from ecmwf.opendata import Client
    last = None
    for i in range(3):
        try:
            Client(source=source).retrieve(date=date, time=0, type="fc", stream="oper", step=steps, param=["2t", "tp"], target=target)
            return
        except Exception as e:
            last = e; log("  ponavljam ECMWF", source, date, e); time.sleep(10 + 10 * i)
    raise last

def day_values(F, k):
    """Dan k (UTC, 0 = dan pokretanja): kiša mm, Tsr, Tmin, Tmax (°C) na gruboj mreži."""
    s0, s1 = 24 * k, 24 * k + 24
    tp1 = F[("tp", s1)]; tp0 = F[("tp", s0)] if s0 > 0 else 0
    P = np.clip((tp1 - tp0) * 1000.0, 0, None)
    ts = [F[("2t", s)] - 273.15 for s in range(s0, s1 + 1, 3) if ("2t", s) in F]
    T = np.stack(ts)
    return np.stack([P, T.mean(0), T.min(0), T.max(0)]).astype(np.float32)

# ---------------------------------------------------------------- visine (Terrain Tiles)
def dem_fine_and_coarse():
    z = 8
    def tx(lon): return int((lon + 180) / 360 * 2 ** z)
    def ty(lat): r = math.radians(lat); return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)
    x0, x1, y0, y1 = tx(CLON0), tx(CLON1), ty(CLAT1), ty(CLAT0)
    from PIL import Image
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    big = np.zeros((H, W), np.float32)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            im = np.asarray(Image.open(io.BytesIO(get("https://s3.amazonaws.com/elevation-tiles-prod/terrarium/%d/%d/%d.png" % (z, x, y)))).convert("RGB")).astype(np.float32)
            big[(y - y0) * 256:(y - y0 + 1) * 256, (x - x0) * 256:(x - x0 + 1) * 256] = im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
    big = np.clip(big, 0, None)
    def px(lon, lat):
        n = 2 ** z * 256
        X = (lon + 180) / 360 * n - x0 * 256
        r = np.radians(lat); Y = (1 - np.log(np.tan(r) + 1 / np.cos(r)) / math.pi) / 2 * n - y0 * 256
        return X, Y
    def box_mean(lat, lon, half):
        xa, ya = px(lon - half, lat + half); xb, yb = px(lon + half, lat - half)
        a = big[int(max(0, ya)):int(min(H, yb)) + 1, int(max(0, xa)):int(min(W, xb)) + 1]
        return float(a.mean()) if a.size else 0.0
    fine = np.array([[box_mean(la, lo, FD / 2) for lo in FLONS] for la in FLATS], np.float32)
    coarse = np.array([[box_mean(la, lo, CD / 2) for lo in CLONS] for la in CLATS], np.float32)
    return fine, coarse

# ---------------------------------------------------------------- maska država
def country_mask():
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    gj = json.loads(get("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson"))
    polys = [prep(shape(f["geometry"])) for f in gj["features"]
             if f["properties"].get("ADM0_A3") in DRZAVE or f["properties"].get("ISO_A3") in DRZAVE]
    m = np.zeros((FROWS, FCOLS), bool)
    for r, la in enumerate(FLATS):
        for c, lo in enumerate(FLONS):
            p = Point(float(lo), float(la))
            m[r, c] = any(pp.contains(p) for pp in polys)
    return m

# ---------------------------------------------------------------- interpolacija gruba -> fina
def bilinear(G):
    """G: [..., len(CLATS), len(CLONS)] -> [..., FROWS, FCOLS]"""
    fy = (CLAT1 - FLATS) / CD; fx = (FLONS - CLON0) / CD
    y0 = np.floor(fy).astype(int); x0 = np.floor(fx).astype(int)
    wy = (fy - y0)[:, None]; wx = (fx - x0)[None, :]
    a = G[..., y0[:, None], x0[None, :]]; b = G[..., y0[:, None], x0[None, :] + 1]
    c = G[..., y0[:, None] + 1, x0[None, :]]; d = G[..., y0[:, None] + 1, x0[None, :] + 1]
    return a * (1 - wy) * (1 - wx) + b * (1 - wy) * wx + c * wy * (1 - wx) + d * wy * wx

# ---------------------------------------------------------------- ocjene
def trap(x, a, b, c, d):
    return np.clip(np.minimum((x - a) / (b - a), (d - x) / (d - c)), 0, 1)

SEZ = {  # težina po mjesecu (1–12)
    "vrganji":   {5: .25, 6: .7, 7: .8, 8: .9, 9: 1, 10: .9, 11: .35},
    "lisicarke": {5: .2, 6: .8, 7: 1, 8: 1, 9: .9, 10: .6, 11: .2},
    "suncanice": {6: .4, 7: .7, 8: .9, 9: 1, 10: .9, 11: .4},
    "smrcci":    {2: .15, 3: .7, 4: 1, 5: .8, 6: .3},
}
# (kiša od-do dana unatrag, mm za početak, mm za punu ocjenu, temperatura a,b,c,d za 7-dnevni prosjek)
PAR = {
    "vrganji":   ((4, 14), 10, 35, (7, 12, 19, 25)),
    "lisicarke": ((2, 10), 10, 30, (10, 15, 22, 27)),
    "suncanice": ((3, 12), 10, 30, (8, 12, 20, 25)),
    "smrcci":    ((1, 10), 8, 25, (4, 8, 14, 18)),
}
GRUPE = ["vrganji", "lisicarke", "suncanice", "smrcci"]

def scores(S, dates):
    """S: [4, dana, R, C] (P, Tm, Tn, Tx), dates: datumi za dane; vraća [4 skupine, FUT, R, C] uint8."""
    P, Tm, Tn, Tx = S
    res = np.zeros((4, FUT) + P.shape[1:], np.uint8)
    for gi, g in enumerate(GRUPE):
        (ra, rb), r0, r1, tt = PAR[g]
        for k in range(FUT):
            i = PAST + k
            w = SEZ[g].get(dates[i].month, 0)
            if w == 0:
                continue
            rain = P[i - rb:i - ra + 1].sum(0)
            fr = np.clip((rain - r0) / (r1 - r0), 0, 1)
            T7 = Tm[i - 6:i + 1].mean(0)
            ft = trap(T7, *tt)
            mn = Tn[i - 2:i + 1].min(0)
            frost = np.where(mn <= -3, .1, np.where(mn <= 0, .4, 1.0)) if g != "smrcci" else np.where(mn <= -3, .3, 1.0)
            dry = np.where((P[i - 3:i + 1].sum(0) < 1) & (Tx[i - 3:i + 1].max(0) > 28), .7, 1.0)
            res[gi, k] = np.round(100 * w * np.sqrt(fr * ft) * frost * dry).clip(0, 100).astype(np.uint8)
    return res

# ---------------------------------------------------------------- glavno
def main():
    t0 = time.time()
    arh = {}
    if ARH_IN and os.path.exists(ARH_IN):
        z = np.load(ARH_IN)
        arh = {k: z[k].astype(np.float32) for k in z.files}
        log("arhiva:", len(arh), "dana")
    # 1) današnja prognoza (ili jučerašnja ako današnja još nije objavljena)
    today = dt.datetime.utcnow().date()
    steps = list(range(0, 145, 3)) + list(range(150, 24 * FUT + 1, 6))
    run = None
    for back in (0, 1):
        d = today - dt.timedelta(days=back)
        try:
            retrieve("ecmwf", d.strftime("%Y%m%d"), steps, "fc.grib2"); run = d; break
        except Exception as e:
            log("prognoza za", d, "nije dostupna:", e)
    if run is None:
        for back in (0, 1):
            d = today - dt.timedelta(days=back)
            try:
                retrieve("aws", d.strftime("%Y%m%d"), steps, "fc.grib2"); run = d; break
            except Exception as e:
                log("AWS prognoza za", d, "nije dostupna:", e)
    if run is None:
        raise SystemExit("nema prognoze")
    F = read_grib("fc.grib2"); os.remove("fc.grib2")
    fut = [day_values(F, k) for k in range(FUT)]
    arh[run.isoformat()] = fut[0]
    log("prognoza", run, "%.0fs" % (time.time() - t0))
    # 2) prošli dani: popuni praznine iz AWS arhive
    for back in range(PAST, 0, -1):
        d = run - dt.timedelta(days=back); key = d.isoformat()
        if key in arh:
            continue
        try:
            retrieve("aws", d.strftime("%Y%m%d"), list(range(0, 25, 3)), "p.grib2")
            arh[key] = day_values(read_grib("p.grib2"), 0); os.remove("p.grib2"); log("  dopunjeno", key)
        except Exception as e:
            log("  nedostaje", key, e)
    # zadrži 40 dana
    for k in sorted(arh)[:-40]:
        del arh[k]
    np.savez_compressed(os.path.join(OUT, "arhiva.npz"), **arh)
    dates = [run + dt.timedelta(days=i) for i in range(-PAST, FUT)]
    seq, have = [], 0
    for i, d in enumerate(dates):
        if i < PAST:
            v = arh.get(d.isoformat())
            if v is None:   # nema podatka: najbliži poznati dan
                near = [arh[k] for k in sorted(arh) if k <= d.isoformat()] or [fut[0]]
                v = near[-1]
            else:
                have += 1
        else:
            v = fut[i - PAST]
        seq.append(v)
    G = np.stack(seq, 1)                        # [4, dana, clat, clon]
    # 3) fina mreža, visine, maska
    zf, zc = dem_fine_and_coarse()
    mask = country_mask()
    FG = bilinear(G)                            # [4, dana, R, C]
    zci = bilinear(zc[None])[0]
    lapse = (-0.0065 * (zf - zci))[None, None]
    FG[1:] = FG[1:] + lapse
    sc = scores(FG, dates)
    # 4) izlaz za polja unutar država
    rr, cc = np.nonzero(mask)
    idx = (rr * FCOLS + cc).astype(np.uint16)
    days = slice(PAST - OUT_PAST, PAST + FUT)
    def b64(a): return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
    P = np.clip(np.round(FG[0][days][:, rr, cc]), 0, 255).astype(np.uint8).T          # [polja, 22]
    def t8(a): return np.clip(np.round(a[days][:, rr, cc] * 2), -127, 127).astype(np.int8).T
    out = {
        "v": 1, "d0": run.isoformat(), "made": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "grid": {"lat0": FLAT0, "lon0": FLON0, "d": FD, "rows": FROWS, "cols": FCOLS},
        "past": OUT_PAST, "fut": FUT, "groups": GRUPE, "pastHave": have, "pastNeed": PAST, "n": int(len(idx)),
        "cells": b64(idx), "elev": b64(np.round(zf[rr, cc]).astype(np.uint16)),
        "P": b64(P), "Tm": b64(t8(FG[1])), "Tn": b64(t8(FG[2])), "Tx": b64(t8(FG[3])),
        "S": b64(sc[:, :, rr, cc].transpose(2, 0, 1).astype(np.uint8)),                # [polja, 4, 8]
        "src": "ECMWF IFS open data (CC BY 4.0); Mapzen/Tilezen Terrain Tiles; Natural Earth",
    }
    with open(os.path.join(OUT, "prognoza.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    log("gotovo: %d polja, prošlih dana iz arhive %d/%d, %.0fs, %d kB" % (len(idx), have, PAST, time.time() - t0, os.path.getsize(os.path.join(OUT, "prognoza.json")) // 1024))
    for gi, g in enumerate(GRUPE):
        log("  %-10s danas max %3d  sr %5.1f" % (g, sc[gi, 0][mask].max(), sc[gi, 0][mask].mean()))

if __name__ == "__main__":
    main()
