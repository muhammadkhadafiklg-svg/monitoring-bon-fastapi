"""Pemuat dan pengolah data monitoring bon sementara (tanpa Streamlit).
Data dibaca sekali lalu disimpan di memori server dan dipakai bersama oleh semua pengunjung."""
import io
import os
import threading
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

TZ = ZoneInfo("Asia/Jakarta")
BATAS_JAM = 24
BLOKIR_JAM = 24
MASTER_ID = "1vcQ8iIARrUS_L1mXcXFu3d6DWWZgqBESBXSm5FsFRKc"
SEMUA = "SEMUA PERIODE"
STATUS_LIST = ["Diajukan", "Disetujui", "Dicairkan", "Direalisasi", "Selesai", "Ditolak", "Kedaluwarsa"]
KOLOM = ["ID", "TglAjuan", "Store", "NIP", "Nama", "Keperluan", "Nominal", "Status", "Approver",
         "TglApprove", "CatatanMgr", "Cashier", "TglCair", "TglRealisasi", "NominalRealisasi",
         "Sisa", "CatatanRealisasi", "FotoID", "TglSelesai"]
KOLOM_TGL = ["TglAjuan", "TglApprove", "TglCair", "TglRealisasi", "TglSelesai"]
BATAS_TAMPIL = 500


def env(k, d=""):
    return os.environ.get(k, d).strip()


# ------------------------------------------------------------------ cache bersama
_cache, _locks, _glock = {}, {}, threading.Lock()


def cached(key, ttl, fn):
    """Satu proses pemuatan untuk semua pengunjung. Jika sumber gagal, data lama tetap dipakai."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    with _glock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            val = fn()
        except Exception:
            if hit:
                return hit[1]
            raise
        _cache[key] = (time.time(), val)
        return val


def kosongkan_cache():
    _cache.clear()


# ------------------------------------------------------------------ pengambil data
def perbaiki_url(u):
    u = u.strip()
    if "/edit" in u or "/export" in u or "/gviz" in u:
        raise ValueError("BON_CSV_URL harus link Publish to web (tab Bon, format CSV).")
    u = u.replace("/pubhtml", "/pub").replace("output=html", "output=csv")
    if "output=" not in u:
        u += ("&" if "?" in u else "?") + "output=csv"
    return u


def ambil_csv(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        teks = r.read().decode("utf-8-sig", errors="replace")
    if teks.lstrip()[:1] == "<":
        raise ValueError("Link mengembalikan halaman web, bukan CSV. Periksa pengaturan berbagi/publikasi.")
    return pd.read_csv(io.StringIO(teks), dtype=str, keep_default_na=False)


def arsip_ids():
    out = {}
    for b in env("ARSIP_IDS").split(","):
        if "=" in b:
            th, sid = b.split("=", 1)
            out[th.strip()] = sid.strip()
    return out


def url_master():
    u = env("MASTER_CSV_URL")
    if u:
        return perbaiki_url(u) if "/pub" in u else u
    gid = env("MASTER_GID")
    return f"https://docs.google.com/spreadsheets/d/{MASTER_ID}/export?format=csv" + (f"&gid={gid}" if gid else "")


def olah_master(m):
    kol = list(m.columns)

    def cari(*kunci):
        for k in kunci:
            for c in kol:
                if k in c.lower():
                    return c
        return None

    c_kode = cari("kode") or kol[0]
    c_nama, c_fra, c_reg = cari("nama store", "nama toko", "toko"), cari("fra"), cari("regional", "region")
    o = pd.DataFrame({
        "Store": m[c_kode].str.strip().str.upper(),
        "NamaStore": m[c_nama].str.strip() if c_nama else "",
        "FRA": m[c_fra].str.strip() if c_fra else "",
        "Regional": m[c_reg].str.strip() if c_reg else "",
    })
    return o[o["Store"] != ""].drop_duplicates("Store")


def tgl(s):
    s = s.astype(str).str.strip().replace("", pd.NA)
    m = s.dropna().str.extract(r"^(\d{1,2})/(\d{1,2})/\d{4}")
    if m.dropna().empty:
        return pd.to_datetime(s, errors="coerce", format="mixed")
    a, b = pd.to_numeric(m[0], errors="coerce"), pd.to_numeric(m[1], errors="coerce")
    dayfirst = bool((a > 12).any()) or not bool((b > 12).any())
    return pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, format="mixed")


def angka(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[^\d\-]", "", regex=True), errors="coerce").fillna(0)


def rp(n):
    return "Rp {:,.0f}".format(n).replace(",", ".")


def fmt_tgl(d):
    return d.strftime("%d/%m/%Y %H:%M") if pd.notna(d) else ""


def tanda(r):
    if r["Gantung"]:
        return f"GANTUNG {r['LamaJam']} JAM" + (", STORE TERKUNCI" if r.get("Macet", False) else "")
    if r["Status"] == "Dicairkan":
        return f"Belum realisasi ({r['LamaJam']} jam)"
    if r["Terlambat"]:
        return f"Terlambat realisasi ({r['LamaReal']} jam)"
    return ""


def olah(raw):
    hilang = [k for k in KOLOM if k not in raw.columns]
    if hilang:
        raise ValueError("Kolom tab Bon tidak lengkap: " + ", ".join(hilang))
    df = raw[raw["ID"].str.strip() != ""].copy()
    for k in KOLOM_TGL:
        df[k] = tgl(df[k])
    for k in ["Nominal", "NominalRealisasi", "Sisa"]:
        df[k] = angka(df[k])
    df["Store"] = df["Store"].str.strip().str.upper()
    df["Bulan"] = df["TglAjuan"].dt.strftime("%Y-%m")
    now = datetime.now(TZ).replace(tzinfo=None)
    sel = (df["Status"] == "Dicairkan") & df["TglCair"].notna()
    df["LamaJam"] = 0
    df.loc[sel, "LamaJam"] = ((now - df.loc[sel, "TglCair"]).dt.total_seconds() // 3600).astype(int)
    df["Gantung"] = sel & (df["LamaJam"] >= BATAS_JAM)
    df["Macet"] = sel & (df["LamaJam"] >= BLOKIR_JAM)
    ada = df["TglCair"].notna() & df["TglRealisasi"].notna()
    detik = (df["TglRealisasi"] - df["TglCair"]).dt.total_seconds()
    df["LamaReal"] = 0
    df.loc[ada, "LamaReal"] = (detik[ada] // 3600).astype(int)
    df["Terlambat"] = ada & (detik > BATAS_JAM * 3600)
    df = df.sort_values("TglAjuan", ascending=False)
    catatan = ""
    try:
        df = df.merge(olah_master(cached("master", 600, lambda: ambil_csv(url_master()))), on="Store", how="left")
    except Exception as e:  # noqa: BLE001
        catatan = f"Master store belum bisa dibaca ({e}). Nama store, FRA, dan regional dikosongkan."
    for k in ("NamaStore", "FRA", "Regional"):
        if k not in df:
            df[k] = ""
        df[k] = df[k].fillna("")
    df["LabelStore"] = [f"{s} - {n}" if n else s for s, n in zip(df["Store"], df["NamaStore"])]
    df = df.reset_index(drop=True)
    df.attrs["catatan"] = catatan
    return df


def bulan_opsi(now=None):
    now = now or datetime.now(TZ)
    out, y, m = [], now.year, now.month
    for _ in range(12):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def get_data(periode):
    def bangun():
        url = env("BON_CSV_URL")
        if not url:
            raise ValueError("BON_CSV_URL belum diisi di Environment.")
        raws = [cached("bon", 60, lambda: ambil_csv(perbaiki_url(url)))]
        ids = arsip_ids()
        for b in (bulan_opsi() if periode == SEMUA else [periode]):
            sid = ids.get(b[:4])
            if not sid:
                continue
            try:
                raws.append(cached(f"arsip:{sid}:{b}", 300, lambda sid=sid, b=b: ambil_csv(
                    f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={b}")))
            except Exception:  # noqa: BLE001
                pass  # tab arsip bulan itu belum ada
        return olah(pd.concat(raws, ignore_index=True).drop_duplicates("ID", keep="first"))

    return cached(f"data:{periode}", 60, bangun)


# ------------------------------------------------------------------ penyaringan halaman
def saring(periode="", regional="", fra="", store="", status="", cari=""):
    now = datetime.now(TZ)
    opsi = bulan_opsi(now)
    if periode not in [SEMUA] + opsi:
        periode = opsi[0]
    data = get_data(periode)
    reg_list = sorted(x for x in data["Regional"].unique() if x)
    regional = regional if regional in reg_list else ""
    d0 = data if not regional else data[data["Regional"] == regional]
    fra_list = sorted(x for x in d0["FRA"].unique() if x)
    fra = fra if fra in fra_list else ""
    d0 = d0 if not fra else d0[d0["FRA"] == fra]
    lbl = d0.drop_duplicates("Store").set_index("Store")["LabelStore"].to_dict()
    store = store if store in lbl else ""
    status = status if status in STATUS_LIST else ""
    dasar = d0 if not store else d0[d0["Store"] == store]
    cari = (cari or "").strip()[:60]
    if cari:
        q = cari.lower()
        dasar = dasar[dasar[["ID", "Nama", "NIP", "Keperluan"]].agg(" ".join, axis=1).str.lower().str.contains(q, regex=False)]
    per = dasar if periode == SEMUA else dasar[dasar["Bulan"] == periode]
    cair = per[per["Status"].isin(["Dicairkan", "Direalisasi", "Selesai"])]
    blm = per[per["Status"] == "Dicairkan"]
    gantung = dasar[dasar["Gantung"]].sort_values("LamaJam", ascending=False)
    store_kunci = sorted(gantung[gantung["Macet"]]["Store"].unique())
    det = per if not status else per[per["Status"] == status]
    if per.empty:
        rk = pd.DataFrame(columns=["LabelStore", "FRA", "Regional", "Jumlah", "Diajukan", "Dicairkan", "Belum",
                                   "Gantung", "Macet", "Selesai"])
    else:
        rk = per.groupby("Store").agg(Jumlah=("ID", "count"), Diajukan=("Nominal", "sum"),
                                      Selesai=("Status", lambda x: int((x == "Selesai").sum())))
        rk["Dicairkan"] = cair.groupby("Store")["Nominal"].sum()
        rk["Belum"] = blm.groupby("Store")["Nominal"].sum()
        rk["Gantung"] = gantung.groupby("Store").size()
        rk["Macet"] = gantung[gantung["Macet"]].groupby("Store").size()
        rk = rk.fillna(0).join(data.drop_duplicates("Store").set_index("Store")[["LabelStore", "FRA", "Regional"]])
        rk = rk.sort_values(["Gantung", "Belum"], ascending=False).reset_index(drop=True)
    return dict(now=now, periode=periode, regional=regional, fra=fra, store=store, status=status, cari=cari,
                opsi_periode=[SEMUA] + opsi, opsi_reg=reg_list, opsi_fra=fra_list,
                opsi_store=sorted(lbl.items()), opsi_status=STATUS_LIST, data=data, per=per, cair=cair, blm=blm,
                gantung=gantung, store_kunci=store_kunci, det=det, rk=rk, n=per["Status"].value_counts(),
                catatan=data.attrs.get("catatan", ""))


def info_ringkas(h):
    lbl = dict(h["opsi_store"])
    info = (f"Periode: {h['periode']} | Regional: {h['regional'] or 'SEMUA'} | FRA: {h['fra'] or 'SEMUA'} | "
            f"Store: {lbl.get(h['store'], h['store']) or 'SEMUA'} | Status: {h['status'] or 'SEMUA'}"
            f"{' | Pencarian: ' + h['cari'] if h['cari'] else ''} | Dicetak {h['now'].strftime('%d/%m/%Y %H:%M')} WIB")
    n = h["n"]
    ringkas = [("JUMLAH BON", len(h["per"])), ("TOTAL DIAJUKAN", rp(h["per"]["Nominal"].sum())),
               ("TOTAL DICAIRKAN", rp(h["cair"]["Nominal"].sum())), ("BELUM DIREALISASI", rp(h["blm"]["Nominal"].sum())),
               (f"GANTUNG > {BATAS_JAM} JAM", len(h["gantung"])), ("MENUNGGU APPROVAL", int(n.get("Diajukan", 0))),
               ("MENUNGGU PENCAIRAN", int(n.get("Disetujui", 0))), ("MENUNGGU VERIFIKASI", int(n.get("Direalisasi", 0))),
               ("SELESAI", int(n.get("Selesai", 0))), ("STORE TERKUNCI", len(h["store_kunci"]))]
    return info, ringkas


# ------------------------------------------------------------------ bahan tampilan
def konteks(h):
    n, per, gantung = h["n"], h["per"], h["gantung"]
    kpi = [("JUMLAH BON", f"{len(per):,}".replace(",", "."), "periode terpilih", False),
           ("TOTAL DIAJUKAN", rp(per["Nominal"].sum()), "", False),
           ("TOTAL DICAIRKAN", rp(h["cair"]["Nominal"].sum()), f"{len(h['cair'])} bon", False),
           ("BELUM DIREALISASI", rp(h["blm"]["Nominal"].sum()), f"{len(h['blm'])} bon", False),
           (f"GANTUNG > {BATAS_JAM} JAM", str(len(gantung)), "semua periode", len(gantung) > 0)]
    strip = [(t, int(n.get(s, 0))) for t, s in [("MENUNGGU APPROVAL", "Diajukan"), ("MENUNGGU PENCAIRAN", "Disetujui"),
             ("MENUNGGU VERIFIKASI", "Direalisasi"), ("SELESAI", "Selesai"), ("DITOLAK", "Ditolak"), ("KEDALUWARSA", "Kedaluwarsa")]]
    strip += [("REALISASI TERLAMBAT", int(per["Terlambat"].sum())), ("STORE TERKUNCI", len(h["store_kunci"]))]
    t_g = [{"c": [r.LabelStore, r.FRA, r.Regional, r.ID, r.NIP, r.Nama, r.Keperluan, rp(r.Nominal), r.Cashier,
                  fmt_tgl(r.TglCair), f"{r.LamaJam} jam"], "k": "m"} for r in gantung.itertuples()]
    rk = h["rk"]
    t_r = [{"c": [r.LabelStore, r.FRA, r.Regional, int(r.Jumlah), rp(r.Diajukan), rp(r.Dicairkan), rp(r.Belum),
                  int(r.Gantung), int(r.Selesai), "TERKUNCI" if r.Macet > 0 else "Normal"],
            "k": "m" if r.Gantung > 0 else ""} for r in rk.itertuples()]
    det = h["det"]
    t_d = []
    for r in det.head(BATAS_TAMPIL).itertuples():
        k = "m" if r.Gantung else ("h" if r.Status == "Dicairkan" else ("k" if r.Terlambat else ""))
        rr = {"Gantung": r.Gantung, "LamaJam": r.LamaJam, "Status": r.Status, "Terlambat": r.Terlambat,
              "LamaReal": r.LamaReal, "Macet": r.Macet}
        real = pd.notna(r.TglRealisasi)
        t_d.append({"k": k, "c": [r.ID, fmt_tgl(r.TglAjuan), r.LabelStore, r.FRA, r.Regional, r.NIP, r.Nama,
                                  r.Keperluan, rp(r.Nominal), r.Status, tanda(rr), r.Approver, r.Cashier,
                                  fmt_tgl(r.TglCair), fmt_tgl(r.TglRealisasi), rp(r.NominalRealisasi) if real else "",
                                  rp(r.Sisa) if real else "", fmt_tgl(r.TglSelesai),
                                  "Ada" if str(r.FotoID).strip() else "-"]})
    return dict(h=h, kpi=kpi, strip=strip, t_g=t_g, t_r=t_r, t_d=t_d, total_det=len(det), batas_tampil=BATAS_TAMPIL,
                total_gantung=rp(gantung["Nominal"].sum()), batas=BATAS_JAM, blokir_hari=BLOKIR_JAM // 24,
                dimuat=h["now"].strftime("%d/%m/%Y %H:%M:%S"))
