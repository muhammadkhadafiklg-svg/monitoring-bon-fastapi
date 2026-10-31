"""Pembuat PDF monitoring bon sementara (reportlab)."""
import io
from xml.sax.saxutils import escape

import pandas as pd  # noqa: F401
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from data import BATAS_JAM, fmt_tgl, rp, tanda

NAVY = colors.HexColor("#1e3a8a")

def buat_pdf(info: str, ringkas: list, gantung: pd.DataFrame, rk: pd.DataFrame, det: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=13 * mm, bottomMargin=15 * mm, title="Monitoring Bon Sementara",
                            author="M Khadafi")
    ss = getSampleStyleSheet()
    judul = ParagraphStyle("j", parent=ss["Title"], fontSize=16, alignment=0, textColor=NAVY, spaceAfter=2)
    kecil = ParagraphStyle("k", parent=ss["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#334155"))
    sel = ParagraphStyle("s", parent=ss["Normal"], fontSize=7.5, leading=9)
    selh = ParagraphStyle("h", parent=sel, fontName="Helvetica-Bold", textColor=colors.white)
    bag = ParagraphStyle("b", parent=ss["Heading3"], fontSize=10.5, textColor=NAVY, spaceBefore=10, spaceAfter=4)

    def P(t, stl=sel):
        return Paragraph(escape(str(t)).replace("\n", "<br/>"), stl)

    def tabel(kepala, baris, lebar, warna=None):
        data = [[P(h, selh) for h in kepala]] + [[P(c) for c in r] for r in baris]
        t = Table(data, colWidths=[w * mm for w in lebar], repeatRows=1)
        gaya = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        for i, w in enumerate(warna or [], start=1):
            if w == "merah":
                gaya.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fee2e2")))
            elif w == "kuning":
                gaya.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fef3c7")))
            elif w == "hijau":
                gaya.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#dcfce7")))
        t.setStyle(TableStyle(gaya))
        return t

    def kaki(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#334155"))
        canvas.drawCentredString(landscape(A4)[0] / 2, 8 * mm, "Powered and developed by M Khadafi")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 8 * mm, f"Halaman {d.page}")
        canvas.restoreState()

    el = [Paragraph("MONITORING BON SEMENTARA", judul), Paragraph(escape(info), kecil), Spacer(1, 6),
          tabel([r[0] for r in ringkas], [[r[1] for r in ringkas]], [273 / len(ringkas)] * len(ringkas))]

    if len(gantung):
        el.append(Paragraph(f"BON GANTUNG (BELUM DIREALISASI LEBIH DARI {BATAS_JAM} JAM)", bag))
        rows = [[r.LabelStore + ("\nFRA: " + r.FRA if r.FRA else "") + (" | Reg: " + r.Regional if r.Regional else ""),
                 r.ID, f"{r.NIP} - {r.Nama}", r.Keperluan, rp(r.Nominal), fmt_tgl(r.TglCair), f"{r.LamaJam} jam"]
                for r in gantung.itertuples()]
        el.append(tabel(["Store", "ID Bon", "Staff", "Keperluan", "Nominal", "Waktu Cair", "Lama"], rows,
                        [62, 24, 42, 60, 26, 33, 26], ["merah"] * len(rows)))

    if len(rk):
        el.append(Paragraph("RINGKASAN PER STORE", bag))
        rows = [[r.LabelStore, r.FRA, r.Regional, int(r.Jumlah), rp(r.Diajukan), rp(r.Dicairkan), rp(r.Belum),
                 int(r.Gantung), int(r.Selesai), "TERKUNCI" if r.Macet > 0 else "Normal"] for r in rk.itertuples()]
        el.append(tabel(["Store", "FRA", "Regional", "Jumlah", "Total Diajukan", "Total Dicairkan",
                         "Belum Direalisasi", "Gantung", "Selesai", "Pengajuan"], rows, [56, 30, 30, 16, 29, 29, 29, 16, 16, 22],
                        ["merah" if r.Gantung > 0 else None for r in rk.itertuples()]))

    el.append(Paragraph("DAFTAR BON", bag))
    batas = 1500
    dd = det.head(batas)
    rows = [[r.ID, fmt_tgl(r.TglAjuan), r.LabelStore + ("\nFRA: " + r.FRA if r.FRA else ""), f"{r.NIP} - {r.Nama}",
             r.Keperluan, rp(r.Nominal), r.Status, tanda({"Gantung": r.Gantung, "LamaJam": r.LamaJam, "Status": r.Status, "Terlambat": r.Terlambat, "LamaReal": r.LamaReal, "Macet": r.Macet})]
            for r in dd.itertuples()]
    warna = ["merah" if r.Gantung else ("hijau" if r.Status == "Dicairkan" else ("kuning" if r.Terlambat else None)) for r in dd.itertuples()]
    el.append(tabel(["ID Bon", "Tgl Ajuan", "Store", "Staff", "Keperluan", "Nominal", "Status", "Tanda"], rows,
                    [25, 25, 54, 40, 50, 24, 22, 33], warna))
    if len(det) > batas:
        el.append(Paragraph(f"Hanya {batas} baris pertama yang dicetak dari total {len(det)} baris.", kecil))
    el.append(Spacer(1, 4))
    el.append(Paragraph("Merah = gantung lebih dari 24 jam. Hijau = sudah dicairkan, belum direalisasi (masih dalam 24 jam). Kuning = realisasi selesai lebih dari 24 jam (tidak sesuai WI).", kecil))
    doc.build(el, onFirstPage=kaki, onLaterPages=kaki)
    return buf.getvalue()
