"""Dashboard Monitoring Bon Sementara (FastAPI). Jalankan: uvicorn main:app --host 0.0.0.0 --port $PORT"""
import os
import secrets
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import data
import pdfgen

app = FastAPI(title="Monitoring Bon Sementara", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
basic = HTTPBasic(auto_error=False)


def cek_login(cred: HTTPBasicCredentials = Depends(basic)):
    """Login opsional: aktif bila DASH_USER dan DASH_PASS diisi di Environment."""
    user, pw = data.env("DASH_USER"), data.env("DASH_PASS")
    if not user or not pw:
        return
    ok = cred is not None and secrets.compare_digest(cred.username, user) and secrets.compare_digest(cred.password, pw)
    if not ok:
        raise HTTPException(status_code=401, detail="Login diperlukan", headers={"WWW-Authenticate": "Basic"})


def filter_param(periode: str = "", regional: str = "", fra: str = "", store: str = "", status: str = "", cari: str = ""):
    return dict(periode=periode, regional=regional, fra=fra, store=store, status=status, cari=cari)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(cek_login)])
def beranda(request: Request, f: dict = Depends(filter_param)):
    try:
        h = data.saring(**f)
        ctx = data.konteks(h)
    except Exception as e:  # noqa: BLE001
        return templates.TemplateResponse(request, "galat.html", {"pesan": str(e)}, status_code=503)
    ctx["qs"] = urlencode({k: h[k] for k in ("periode", "regional", "fra", "store", "status", "cari")})
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/refresh", dependencies=[Depends(cek_login)])
def refresh(f: dict = Depends(filter_param)):
    data.kosongkan_cache()
    return RedirectResponse("/?" + urlencode({k: v for k, v in f.items() if v}), status_code=303)


@app.get("/pdf", dependencies=[Depends(cek_login)])
def unduh_pdf(f: dict = Depends(filter_param)):
    h = data.saring(**f)
    info, ringkas = data.info_ringkas(h)
    isi = data.cached("pdf:" + urlencode(sorted(f.items())), 60,
                      lambda: pdfgen.buat_pdf(info, ringkas, h["gantung"], h["rk"], h["det"]))
    nama = f"monitoring_bon_{h['periode']}_{h['now'].strftime('%Y%m%d_%H%M')}.pdf"
    return Response(isi, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{nama}"'})


@app.get("/csv", dependencies=[Depends(cek_login)])
def unduh_csv(f: dict = Depends(filter_param)):
    h = data.saring(**f)
    isi = h["det"].drop(columns=["FotoID"]).to_csv(index=False).encode("utf-8-sig")
    nama = f"monitoring_bon_{h['periode']}_{h['now'].strftime('%Y%m%d_%H%M')}.csv"
    return Response(isi, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{nama}"'})
