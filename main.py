import os
import base64
import tempfile
import zipfile
import xml.etree.ElementTree as ET
import re
import shutil
import uuid
import traceback
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Response, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import database
import uvicorn
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image
import pytesseract
import pdfplumber

# Pot do Tesseracta na Windows
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
from bs4 import BeautifulSoup
import pdf_parser
from pdf_parser import extract_data_from_pdf
import io
from xhtml2pdf import pisa
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import threading
import time

app = FastAPI(title="Invoice83 API")

def get_now_slo():
    """Vrne trenutni čas v Sloveniji (Europe/Ljubljana) v formatu YYYY-MM-DD HH:MM:SS."""
    return datetime.now(ZoneInfo("Europe/Ljubljana")).strftime("%Y-%m-%d %H:%M:%S")



app.mount("/static", StaticFiles(directory="static"), name="static")

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

@app.middleware("http")
async def session_isolation_middleware(request: Request, call_next):
    import shutil
    path = request.url.path
    if not path.startswith("/api") or path in ["/api/heartbeat", "/api/companies", "/api/debug-session"]:
        return await call_next(request)

    session_id = request.headers.get("X-Session-ID")
    if not session_id:
        database.set_active_db("demo.db")
        return await call_next(request)

    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
    session_db_path = SESSIONS_DIR / f"{safe_id}.db"

    if not session_db_path.exists():
        try:
            shutil.copy2("demo.db", session_db_path)
        except Exception as e:
            database.set_active_db("demo.db")
            return await call_next(request)

    database.set_active_db(str(session_db_path))
    return await call_next(request)









@app.on_event("startup")
def startup():
    database.set_active_db("demo.db")
    database.init_db()


@app.get("/api/companies")
def list_companies():
    return {"active_id": "demo", "items": [{"id": "demo", "name": "Primer Tech d.o.o. (DEMO)", "db": "demo.db"}]}

@app.post("/api/companies/switch/{company_id}")
def switch_company(company_id: str):
    return {"status": "success", "company": {"id": "demo", "name": "Primer Tech d.o.o. (DEMO)", "db": "demo.db"}}

@app.post("/api/companies/create")
def create_company(data: dict):
    from fastapi import HTTPException
    raise HTTPException(status_code=403, detail="Ustvarjanje podjetij ni dovoljeno v demo verziji.")


@app.get("/")
def read_root():
    from fastapi.responses import HTMLResponse
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        return HTMLResponse(content=f"<html><body>Napaka pri nalaganju index.html: {str(e)}</body></html>")

@app.get("/api/heartbeat")
def heartbeat():
    """Browser pinguje ta endpoint vsakih 5 sekund. Watchdog se resetira."""
    global _last_heartbeat
    _last_heartbeat = time.time()
    return {"ok": True}

# --- Partnerji ---
class Partner(BaseModel):
    id: Optional[int] = None
    naziv: str
    ulica: Optional[str] = None
    postna_stevilka: Optional[str] = None
    kraj: Optional[str] = None
    drzava: Optional[str] = None
    davcna_stevilka: Optional[str] = None
    zavezanec_za_ddv: Optional[bool] = False
    trr: Optional[str] = None
    telefon: Optional[str] = None
    email: Optional[str] = None
    vrsta: Optional[str] = "oba"

class Nastavitve(BaseModel):
    naziv: Optional[str] = ""
    ulica: Optional[str] = ""
    posta_kraj: Optional[str] = ""
    drzava: Optional[str] = "Slovenija"
    davcna_stevilka: Optional[str] = ""
    zavezanec_za_ddv: Optional[bool] = False
    trr: Optional[str] = ""
    banka: Optional[str] = ""
    email_posiljatelja: Optional[str] = "sim@83.si"
    telefon: Optional[str] = ""
    spletna_stran: Optional[str] = ""
    kratko_ime: Optional[str] = ""
    dvostavno_knjigovodstvo: Optional[bool] = False
    smtp_server: Optional[str] = ""
    smtp_port: Optional[int] = 587
    smtp_username: Optional[str] = ""
    smtp_password: Optional[str] = ""
    smtp_use_tls: Optional[bool] = True
    email_template_racun: Optional[str] = ""
    email_template_ponudba: Optional[str] = ""
    email_template_dobropis: Optional[str] = ""

class PlaciloPovezava(BaseModel):
    dokument_id: int
    znesek: float

class LikvidacijaRequest(BaseModel):
    izpisek_postavka_id: int
    povezave: List[PlaciloPovezava]

class ManualnaLikvidacijaRequest(BaseModel):
    izpisek_postavka_id: int

class EmailRequest(BaseModel):
    priloge_ids: List[int] = []

class Konto(BaseModel):
    id: Optional[int] = None
    stevilka: str
    naziv: str
    opis: Optional[str] = ""

# --- PRILOGE ---
@app.post("/api/upload_priloga")
async def upload_priloga(parent_type: str, parent_id: int, file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    allowed = {'.pdf', '.jpg', '.jpeg', '.png', '.txt', '.xml'}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Format {ext} ni dovoljen. Dovoljeni: {', '.join(allowed)}")
    
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / unique_name
    
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO priloge (parent_type, parent_id, filename, original_name, uploaded_at) VALUES (?, ?, ?, ?, ?)",
        (parent_type, parent_id, unique_name, file.filename, get_now_slo())
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return {"id": new_id, "filename": unique_name, "original_name": file.filename, "url": f"/uploads/{unique_name}"}

@app.get("/api/priloge/{parent_type}/{parent_id}")
def get_priloge(parent_type: str, parent_id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, filename, original_name, uploaded_at FROM priloge WHERE parent_type=? AND parent_id=? ORDER BY uploaded_at",
        (parent_type, parent_id)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{**dict(r), "url": f"/uploads/{r['filename']}"} for r in rows]

@app.delete("/api/priloge/{id}")
def delete_priloga(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM priloge WHERE id=?", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Priloga ni najdena.")
    try:
        (UPLOADS_DIR / row['filename']).unlink(missing_ok=True)
    except Exception:
        pass
    cursor.execute("DELETE FROM priloge WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.get("/api/partnerji")
def get_partnerji():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM partnerji ORDER BY naziv")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/partnerji")
def create_partner(partner: Partner):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO partnerji (naziv, ulica, postna_stevilka, kraj, drzava, davcna_stevilka, zavezanec_za_ddv, trr, telefon, email, vrsta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (partner.naziv, partner.ulica, partner.postna_stevilka, partner.kraj, partner.drzava, partner.davcna_stevilka, partner.zavezanec_za_ddv, partner.trr, partner.telefon, partner.email, partner.vrsta))
    conn.commit()
    conn.close()
    return {"status": "success", "id": cursor.lastrowid}

@app.get("/api/partnerji/detajl/{id}")
def get_partner_detajl(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM partnerji WHERE id = ?", (id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Partner ni najden")
    return dict(row)

@app.put("/api/partnerji/{id}")
def update_partner(id: int, partner: Partner):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE partnerji 
        SET naziv=?, ulica=?, postna_stevilka=?, kraj=?, drzava=?, davcna_stevilka=?, zavezanec_za_ddv=?, trr=?, telefon=?, email=?, vrsta=?
        WHERE id = ?
    """, (partner.naziv, partner.ulica, partner.postna_stevilka, partner.kraj, partner.drzava, partner.davcna_stevilka, partner.zavezanec_za_ddv, partner.trr, partner.telefon, partner.email, partner.vrsta, id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/partnerji/search")
def search_partnerji(q: str):
    conn = database.get_db()
    cursor = conn.cursor()
    # Preiščemo naziv, davčno številko in ulico
    query = "%" + q + "%"
    cursor.execute("""
        SELECT * FROM partnerji 
        WHERE naziv LIKE ? OR davcna_stevilka LIKE ? OR ulica LIKE ?
        ORDER BY naziv LIMIT 20
    """, (query, query, query))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.delete("/api/partnerji/{id}")
def delete_partner(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    
    # Preveri, če ima partner vezane dokumente
    cursor.execute("SELECT COUNT(*) as cnt FROM dokumenti WHERE partner_id = ?", (id,))
    if cursor.fetchone()['cnt'] > 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Partnerja ni mogoče brisati, ker ima vezane dokumente.")
    
    # Preveri, če ima partner vezane bančne izpiske
    cursor.execute("SELECT COUNT(*) as cnt FROM izpiski_postavke WHERE partner_id = ?", (id,))
    if cursor.fetchone()['cnt'] > 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Partnerja ni mogoče brisati, ker ima vezane bančne izpiske.")
        
    cursor.execute("DELETE FROM partnerji WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


# --- Bizi.si pomožne funkcije ---
@app.get("/api/partnerji/search_bizi")
def search_bizi(q: str):
    """Poišče podjetje na Bizi.si in vrne seznam zadetkov."""
    url = f"https://www.bizi.si/iskanje?q={q}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        rows = soup.select(".b-table-row")
        for row in rows:
            name_el = row.select_one(".b-link-company")
            if not name_el: continue
            cols = row.select(".col")
            if len(cols) < 6: continue
            name = name_el.get_text(strip=True)
            href = name_el['href'] if name_el.has_attr('href') else ""
            link = href if href.startswith("http") else "https://www.bizi.si" + href
            naslov_el = row.select_one('a[href*="openMapTis"]') or cols[2].select_one('a')
            naslov = naslov_el.get_text(strip=True) if naslov_el else cols[2].get_text(strip=True)
            posta_kraj = cols[3].get_text(strip=True)
            davcna_raw = cols[5].get_text(strip=True)
            is_zavezanec = "SI" in davcna_raw
            davcna = davcna_raw.replace("SI", "").strip()
            results.append({
                "naziv": name, "naslov": naslov, "posta_kraj": posta_kraj,
                "davcna_stevilka": davcna, "zavezanec_za_ddv": is_zavezanec, "link": link
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Napaka pri iskanju na Bizi.si: {str(e)}")

@app.get("/api/partnerji/bizi_detail")
def bizi_detail(url: str):
    """Pridobi podrobne kontaktne podatke in TRR podjetja z Bizi.si."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        phone_el = soup.select_one("a.i-ostalo-telefon")
        phone = phone_el.get_text(strip=True) if phone_el else ""
        email_el = soup.select_one("#ctl00_ctl00_cphMain_CompanyDetailsTitleBasic1_aMail")
        email = email_el.get_text(strip=True) if email_el else ""
        zavezanec = False
        for label in soup.select(".b-attr-name, .b-attr-label"):
            if "Zavezanec za DDV" in label.get_text(strip=True):
                v = label.find_next_sibling("div", class_="b-attr-value")
                if v and "Da" in v.get_text(): zavezanec = True
                break
        trr = ""
        try:
            r_trr = requests.get(url.rstrip('/') + "/trr-in-blokade/", headers=headers, timeout=5)
            if r_trr.ok:
                soup_trr = BeautifulSoup(r_trr.text, 'html.parser')
                trr_el = soup_trr.select_one("div.b-attr-value-item-trr span.b-attr-value:not(.b-text-line-through)")
                if trr_el: trr = trr_el.get_text(strip=True).replace("IBAN", "").strip()
        except: pass
        return {"telefon": phone, "email": email, "zavezanec_za_ddv": zavezanec, "trr": trr}
    except Exception as e:
        return {"telefon": "", "email": "", "zavezanec_za_ddv": False, "trr": "", "error": str(e)}




# --- Nastavitve ---
@app.get("/api/nastavitve")
def get_nastavitve():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nastavitve WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}

@app.post("/api/nastavitve")
@app.put("/api/nastavitve")
def save_nastavitve(n: Nastavitve):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE nastavitve SET 
            naziv=?, ulica=?, posta_kraj=?, drzava=?, davcna_stevilka=?, 
            zavezanec_za_ddv=?, trr=?, banka=?, email_posiljatelja=?, telefon=?, spletna_stran=?,
            kratko_ime=?, dvostavno_knjigovodstvo=?, smtp_server=?, smtp_port=?, smtp_username=?,
            smtp_password=?, smtp_use_tls=?, email_template_racun=?, email_template_ponudba=?, email_template_dobropis=?
        WHERE id = 1
    """, (n.naziv, n.ulica, n.posta_kraj, n.drzava, n.davcna_stevilka, 
          n.zavezanec_za_ddv, n.trr, n.banka, n.email_posiljatelja, n.telefon, n.spletna_stran,
          n.kratko_ime, n.dvostavno_knjigovodstvo, n.smtp_server, n.smtp_port, n.smtp_username,
          n.smtp_password, n.smtp_use_tls, n.email_template_racun, n.email_template_ponudba, n.email_template_dobropis))
    conn.commit()
    conn.close()
    
    # Sinhronizacija z registries.json če imamo kratko_ime
    if n.kratko_ime:
        registry = get_companies_registry()
        active_id = registry.get("active_id")
        for item in registry["items"]:
            if item["id"] == active_id:
                item["name"] = n.kratko_ime
                break
        save_companies_registry(registry)
        
    return {"status": "success"}

# --- Kontni načrt ---
@app.get("/api/konti")
def get_konti():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM kontni_nacrt ORDER BY stevilka")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/konti")
def create_konto(k: Konto):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO kontni_nacrt (stevilka, naziv, opis) VALUES (?, ?, ?)", (k.stevilka, k.naziv, k.opis))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Napaka pri dodajanju konta: {str(e)}")
    conn.close()
    return {"status": "success", "id": cursor.lastrowid}

@app.put("/api/konti/{id}")
def update_konto(id: int, k: Konto):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE kontni_nacrt SET stevilka=?, naziv=?, opis=? WHERE id = ?", (k.stevilka, k.naziv, k.opis, id))
    conn.commit()
    conn.close()
    return {"status": "success"}

# --- LIKVIDACIJA (Povezovanje plačil) ---
@app.get("/api/likvidacija/odprte_postavke/{partner_id}")
def get_odprte_postavke(partner_id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    # Poiščemo vse račune (izdane in prejete), ki niso popolnoma plačani
    # Računamo preostanek: znesek_skupaj - vsota vseh povezav v placila_povezave
    cursor.execute("""
        SELECT d.id, d.tip, d.stevilka, d.datum_izdaje, d.datum_zapadlosti, d.znesek_skupaj, d.status,
        IFNULL((SELECT SUM(znesek) FROM placila_povezave WHERE dokument_id = d.id), 0) as placano_znesek
        FROM dokumenti d
        WHERE d.partner_id = ? AND d.tip IN ('izdani_racuni', 'prejeti_racuni', 'dobropisi', 'prejeti_dobropisi')
        AND (d.status != 'plačano' OR d.status IS NULL)
        ORDER BY d.datum_zapadlosti ASC
    """, (partner_id,))
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        d = dict(r)
        d['preostanek'] = round(d['znesek_skupaj'] - d['placano_znesek'], 2)
        if d['preostanek'] > 0:
            result.append(d)
    return result

@app.get("/api/likvidacija/povezave/{izpisek_postavka_id}")
def get_povezave_postavke(izpisek_postavka_id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pp.*, d.stevilka, d.tip, d.datum_izdaje
        FROM placila_povezave pp
        JOIN dokumenti d ON pp.dokument_id = d.id
        WHERE pp.izpisek_postavka_id = ?
    """, (izpisek_postavka_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/likvidacija/povezi")
def povezi_placilo(req: LikvidacijaRequest):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        # 1. Pobrišemo obstoječe povezave za to postavko (če obstajajo)
        # Najprej dobimo id-je dokumentov, ki so bili vključeni, da jim kasneje posodobimo status
        cursor.execute("SELECT dokument_id FROM placila_povezave WHERE izpisek_postavka_id = ?", (req.izpisek_postavka_id,))
        old_doc_ids = [r['dokument_id'] for r in cursor.fetchall()]
        
        cursor.execute("DELETE FROM placila_povezave WHERE izpisek_postavka_id = ?", (req.izpisek_postavka_id,))
        
        # 2. Ob povezavi z računom samodejno umaknemo oznako ročne likvidacije
        cursor.execute("UPDATE izpiski_postavke SET manualna_likvidacija = 0 WHERE id = ?", (req.izpisek_postavka_id,))
        
        # 3. Vstavimo nove povezave
        new_doc_ids = []
        for p in req.povezave:
            if p.znesek > 0:
                cursor.execute("""
                    INSERT INTO placila_povezave (izpisek_postavka_id, dokument_id, znesek)
                    VALUES (?, ?, ?)
                """, (req.izpisek_postavka_id, p.dokument_id, p.znesek))
                new_doc_ids.append(p.dokument_id)
        
        # 3. Posodobitev statusov vseh vpletenih dokumentov (starih in novih)
        all_affected = list(set(old_doc_ids + new_doc_ids))
        for doc_id in all_affected:
            # Izračunamo skupno plačano vrednost za ta dokument
            cursor.execute("SELECT SUM(znesek) as skupaj_placano FROM placila_povezave WHERE dokument_id = ?", (doc_id,))
            placano = cursor.fetchone()['skupaj_placano'] or 0
            
            cursor.execute("SELECT znesek_skupaj FROM dokumenti WHERE id = ?", (doc_id,))
            skupaj = cursor.fetchone()['znesek_skupaj']
            
            status = 'neplačano'
            if placano >= skupaj - 0.001: # Toleranca za decimalke
                status = 'plačano'
            elif placano > 0:
                status = 'delno plačano'
            
            cursor.execute("UPDATE dokumenti SET status = ? WHERE id = ?", (status, doc_id))
            
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        if 'conn' in locals(): conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/likvidacija/manualna")
def manualna_likvidacija(req: ManualnaLikvidacijaRequest):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE izpiski_postavke SET manualna_likvidacija = ? WHERE id = ?", (1 if req.manualna else 0, req.izpisek_postavka_id))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
@app.delete("/api/konti/{id}")
def delete_konto(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM kontni_nacrt WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/nastavitve/logo")
async def upload_logo(file: UploadFile = File(...)):
    # Shranimo logotip v static mapo
    try:
        content = await file.read()
        import os
        if not os.path.exists("static/uploads"):
            os.makedirs("static/uploads")
        
        # Podpiramo samo pogoste formate
        ext = file.filename.split('.')[-1].lower()
        if ext not in ['png', 'jpg', 'jpeg', 'gif']:
            raise HTTPException(status_code=400, detail="Nepodprt format slike.")
            
        file_path = f"static/uploads/logo.{ext}"
        with open(file_path, "wb") as f:
            f.write(content)
            
        return {"status": "success", "path": f"/static/uploads/logo.{ext}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def izracunaj_kontrolno_stevilko(stevilka_str):
    """
    Izračun kontrolne številke po modulu 97 (ISO 7064 MOD 97-10).
    Uporablja se za slovenske sklice SI12 (v nekaterih modulih).
    """
    # Odstrani vse ne-številčne znake
    s = "".join(filter(str.isdigit, stevilka_str))
    if not s:
        return "00"
    
    # Številka mora biti dolga do 13 mest
    s = s[:13]
    # Izračun: 98 - (številka * 100 % 97)
    try:
        n = int(s)
        res = (n * 100) % 97
        k = 98 - res
        return f"{k:02d}"
    except:
        return "00"

def format_money(val):
    if val is None: val = 0.0
    return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"

# --- PDF GENERATOR (fpdf2) ---
def generate_pdf_invoice(invoice_data, company_data, partner_data, items):
    from fpdf import FPDF
    import os
    import tempfile
    import qrcode

    class PDF(FPDF):
        def header(self):
            # Iskanje logotipa - preverimo vse možne končnice
            logo_path = None
            for ext in ['png', 'jpg', 'jpeg', 'PNG', 'JPG', 'JPEG']:
                p = f"static/uploads/logo.{ext}"
                if os.path.exists(p):
                    logo_path = p
                    break
            
            if logo_path:
                # Če logotip obstaja, narišemo samo sliko
                self.image(logo_path, 10, 8, 40)
            else:
                # Če logotipa ni, izrišemo tekstovni logotip
                self.set_font('DejaVu', 'B', 24)
                self.set_text_color(0, 0, 0)
                self.cell(15, 10, 'SIM', ln=0)
                self.set_text_color(230, 0, 0)
                self.cell(15, 10, '83', ln=0)
            
            self.set_draw_color(0, 74, 153)
            self.set_line_width(0.5)
            self.line(10, 30, 200, 30)
            
            # PREPREČEVANJE PREKRIVANJA: premaknemo kazalec pod črto!
            self.set_y(35)


    pdf = PDF()
    # Registracija fonta DejaVu
    pdf.add_font('DejaVu', '', 'DejaVuSans.ttf')
    pdf.add_font('DejaVu', 'B', 'DejaVuSans-Bold.ttf')
    pdf.add_font('DejaVu', 'I', 'DejaVuSans.ttf')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Naslov računa (Desno poravnano, večji font)
    title = "RAČUN št."
    if invoice_data.get('tip') == 'ponudba' or invoice_data.get('tip') == 'ponudbe':
        title = "PONUDBA št."
    elif invoice_data.get('tip') == 'dobropis' or invoice_data.get('tip') == 'dobropisi':
        title = "DOBROPIS št."
        
    pdf.set_font('DejaVu', 'B', 16)
    pdf.set_text_color(25, 42, 86) # Temno modra
    pdf.cell(0, 10, f"{title} {invoice_data.get('stevilka', '')}", ln=1, align='R')
    pdf.set_text_color(0, 0, 0)
    
    def f_date(d):
        if not d: return d
        parts = d.split('-')
        if len(parts) == 3: return f"{parts[2]}.{parts[1]}.{parts[0]}"
        return d


    # Pošiljatelj (Levo)
    pdf.set_xy(10, 45)
    pdf.set_font('DejaVu', 'B', 10)
    pdf.cell(90, 5, 'IZDAJATELJ:', ln=1)
    pdf.set_font('DejaVu', '', 9)
    pdf.cell(90, 4, company_data.get('naziv', ''), ln=1)
    pdf.cell(90, 4, company_data.get('ulica', ''), ln=1)
    pdf.cell(90, 4, company_data.get('posta_kraj', ''), ln=1)
    pdf.cell(90, 4, f"Davčna št.: {company_data.get('davcna_stevilka', '')}", ln=1)
    if company_data.get('trr'):
        pdf.cell(90, 4, f"TRR: {company_data.get('trr', '')}", ln=1)

    # Prejemnik (Desno)
    pdf.set_xy(110, 45)
    pdf.set_font('DejaVu', 'B', 10)
    pdf.cell(90, 5, 'PREJEMNIK:', ln=1)
    pdf.set_font('DejaVu', '', 9)
    pdf.set_xy(110, 50)
    pdf.multi_cell(90, 4, f"{partner_data.get('naziv', '')}\n{partner_data.get('ulica', '')}\n{partner_data.get('postna_stevilka', '')} {partner_data.get('kraj', '')}\n{partner_data.get('drzava', 'Slovenija')}\nDavčna št.: {partner_data.get('davcna_stevilka', '')}")

    # Datumi - pod prejemnikom, desno (ne prekrivajo s prejem. blokom)
    d_od = invoice_data.get('datum_storitve_od', '')
    d_do = invoice_data.get('datum_storitve_do', '')
    dates_y = pdf.get_y() + 2
    pdf.set_font('DejaVu', '', 8)
    pdf.set_text_color(80, 80, 80)
    pdf.set_xy(110, dates_y); pdf.cell(90, 4, f"Datum izdaje: {invoice_data.get('datum_izdaje', '')}", align='R'); dates_y += 4
    pdf.set_xy(110, dates_y); pdf.cell(90, 4, f"Datum zapadlosti: {invoice_data.get('datum_zapadlosti', '')}", align='R'); dates_y += 4
    if d_od and d_do:
        pdf.set_xy(110, dates_y); pdf.cell(90, 4, f"Obdobje storitve: {f_date(d_od)} - {f_date(d_do)}", align='R')
    elif d_od:
        pdf.set_xy(110, dates_y); pdf.cell(90, 4, f"Datum storitve: {f_date(d_od)}", align='R')
    pdf.set_text_color(0, 0, 0)

    pdf.set_y(95)
    
    # Tabela postavk
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font('DejaVu', 'B', 9)
    # Header cells
    # Header cells - Adjusted widths to fit Discount
    pdf.cell(85, 8, 'Opis storitve/izdelka', 1, 0, 'L', True)
    pdf.cell(15, 8, 'Kol.', 1, 0, 'C', True)
    pdf.cell(25, 8, 'Cena/en.', 1, 0, 'R', True)
    pdf.cell(15, 8, 'Pop.%', 1, 0, 'C', True)
    pdf.cell(20, 8, 'DDV%', 1, 0, 'C', True)
    pdf.cell(30, 8, 'Znesek', 1, 1, 'R', True)
    
    pdf.set_font('DejaVu', '', 9)
    for it in items:
        x = pdf.get_x()
        y = pdf.get_y()
        
        desc = it.get('opis', '')
        pdf.multi_cell(85, 6, desc, border=1)
        new_y = pdf.get_y()
        row_h = new_y - y
        
        # Zapolnimo ostala polja v isti vrstici
        pdf.set_xy(x + 85, y)
        pdf.cell(15, row_h, str(it.get('kolicina', 1)), 1, 0, 'C')
        pdf.cell(25, row_h, format_money(it.get('cena_enote', 0)), 1, 0, 'R')
        pdf.cell(15, row_h, f"{it.get('popust', 0)}%", 1, 0, 'C')
        pdf.cell(20, row_h, f"{it.get('stopnja_ddv', 22)}%", 1, 0, 'C')
        pdf.cell(30, row_h, format_money(it.get('znesek_skupaj', 0)), 1, 1, 'R')
        
    pdf.ln(5)
    
    # Celotni znesek (na desni)
    pdf.set_font('DejaVu', '', 10)
    pdf.cell(160, 6, 'Skupaj brez DDV:', 0, 0, 'R')
    pdf.cell(30, 6, format_money(invoice_data.get('znesek_brez_ddv', 0)), 0, 1, 'R')
    pdf.cell(160, 6, 'DDV (22%):', 0, 0, 'R')
    pdf.cell(30, 6, format_money(invoice_data.get('znesek_ddv', 0)), 0, 1, 'R')
    
    pdf.ln(2)
    pdf.set_font('DejaVu', 'B', 12)
    pdf.set_draw_color(25, 42, 86)
    pdf.set_line_width(0.5)
    pdf.cell(140, 10, 'SKUPAJ ZA PLAČILO:', 'T B', 0, 'R')
    pdf.set_text_color(200, 0, 0) # Rdeča za poudarek zneska
    pdf.cell(50, 10, format_money(invoice_data.get('znesek_skupaj', 0)), 'T B', 1, 'R')
    pdf.set_text_color(0, 0, 0)
    
    zb = invoice_data.get('zakljucno_besedilo', '')
    if zb and zb.strip():
        pdf.ln(8)
        pdf.set_font('DejaVu', 'I', 9)
        pdf.multi_cell(0, 5, zb.strip())

    noga = invoice_data.get('noga_dokumenta', '')
    if noga and noga.strip():
        pdf.ln(5)
        pdf.set_font('DejaVu', '', 8)
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(0, 4, noga.strip())

    # Posebna opomba za ne-zavezance (če niso zavezanci za DDV)
    legal_note = "DDV ni obračunan na podlagi 1. odstavka 94. člena ZDDV-1"
    if not company_data.get('zavezanec_za_ddv', False):
        if legal_note not in zb and legal_note not in noga:
            pdf.ln(5)
            pdf.set_font('DejaVu', 'I', 8)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 5, legal_note, ln=1, align='L')

    # Footer
    pdf.ln(10)
    pdf.set_font('DejaVu', '', 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 4, 'Hvala za vaše zaupanje.', ln=1)
    pdf.ln(2)
    
    # Ugotovimo ali vključimo plačilne podatke in QR kodo
    # SQLite shrani bool kot 0/1, .get() pa vrne None če ključa ni (npr. stari zapisi)
    # Ugotovimo ali vključimo plačilne podatke in QR kodo
    v_placilo = True
    if 'vkljuci_placilo' in invoice_data:
        v_raw = invoice_data['vkljuci_placilo']
        # SQLite shrani 0/1, JSON pa True/False. Preverimo vse možnosti.
        if v_raw == 0 or v_raw is False or str(v_raw).lower() == '0' or str(v_raw).lower() == 'false':
            v_placilo = False
    
    odstotek = invoice_data.get('odstotek_placila', 100)
    if odstotek is None: odstotek = 100
    try: odstotek = float(odstotek)
    except: odstotek = 100
    
    if v_placilo:
        pdf.set_font('DejaVu', 'B', 8)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 4, 'Plačilni podatki:', ln=1)
        pdf.set_font('DejaVu', '', 8)
        pdf.cell(0, 4, f"IBAN: {company_data.get('trr', '')}", ln=1)
        pdf.cell(0, 4, f"Banka: {company_data.get('banka', '')}", ln=1)
        
        if odstotek < 100:
            znesek_delni = invoice_data.get('znesek_skupaj', 0) * (odstotek / 100)
            pdf.cell(0, 4, f"Znesek za plačilo ({odstotek}%): {format_money(znesek_delni)}", ln=1)

        # SI00 in QR koda
        raw_num = "".join(filter(str.isdigit, invoice_data.get('stevilka', '')))
        # SI00 model ne zahteva kontrolne številke
        sklic_poln = raw_num
        pdf.cell(0, 4, f"Pri plačilu uporabite referenco plačila: SI00 {sklic_poln}", ln=1)
        
        # UPN-QR generiranje (Uradni slovenski standard ZBS)
        iban = company_data.get('trr', '').replace(' ', '')
        # Upoštevamo odstotek plačila za QR kodo
        znesek_za_qr = invoice_data.get('znesek_skupaj', 0) * (odstotek / 100)
        cents = int(round(znesek_za_qr * 100))
        amount_str = f"{cents:011d}"
        
        # Podatki prejemnika (naše podjetje) - omejimo na 42 znakov
        p_name = company_data.get('naziv', '')[:42]
        p_address = company_data.get('ulica', '')[:42]
        p_city = company_data.get('posta_kraj', '')[:42]
        
        # Podatki plačnika (partner) - omejimo na 42 znakov
        c_name = partner_data.get('naziv', '')[:42]
        c_address = partner_data.get('ulica', '')[:42]
        c_city = f"{partner_data.get('postna_stevilka', '')} {partner_data.get('kraj', '')}"[:42]
        
        # Sestavimo prvih 19 polj po vrstnem redu ZBS standarda
        vsebina_qr = [
            "UPNQR",            # 1. Glava
            "",                 # 2. IBAN plačnika
            "",                 # 3. Polog
            "",                 # 4. Dvig
            "",                 # 5. Referenca plačnika
            c_name,             # 6. Ime plačnika
            c_address,          # 7. Naslov plačnika
            c_city,             # 8. Kraj plačnika
            amount_str,         # 9. Znesek
            "",                 # 10. Datum plačila
            "",                 # 11. Nujno
            "OTHR",             # 12. Koda namena
            f"Placilo racuna {invoice_data.get('stevilka', '')}"[:42], # 13. Namen
            ".".join(invoice_data.get('datum_zapadlosti', '').split('-')[::-1]) if invoice_data.get('datum_zapadlosti') else "", # 14. Rok plačila (DD.MM.YYYY)
            iban,               # 15. IBAN prejemnika
            f"SI00{sklic_poln}", # 16. Referenca prejemnika
            p_name,             # 17. Ime prejemnika
            p_address,          # 18. Naslov prejemnika
            p_city              # 19. Kraj prejemnika
        ]
        
        vsota_dolzin = sum(len(f.encode('iso-8859-2', errors='replace')) for f in vsebina_qr)
        kontrolna_vsota = vsota_dolzin + 19
        vsebina_qr.append(f"{kontrolna_vsota:03d}")
        
        qr_data_string = "\n".join(vsebina_qr)
        qr_data_bytes = qr_data_string.encode('iso-8859-2', errors='replace')
        
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data_bytes)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.save(temp_path)
        
        pdf.ln(5)
        pdf.image(temp_path, x=85, w=40)
        
        try:
            os.remove(temp_path)
        except:
            pass
    
    return pdf.output()

def ustvari_in_pripni_pdf(doc_id: int):
    """
    Interna funkcija, ki generira PDF za podan dokument_id in ga shrani med priloge.
    Prepiše obstoječi PDF, če že obstaja.
    """
    try:
        conn = database.get_db()
        cursor = conn.cursor()
        
        # Pridobivanje podatkov o dokumentu
        cursor.execute("SELECT * FROM dokumenti WHERE id = ?", (doc_id,))
        doc = cursor.fetchone()
        if not doc:
            conn.close()
            return
        
        # Pridobivanje podatkov o podjetju
        cursor.execute("SELECT * FROM nastavitve LIMIT 1")
        company = cursor.fetchone()
        if not company:
            company = {}
        
        # Pridobivanje podatkov o partnerju
        cursor.execute("SELECT * FROM partnerji WHERE id = ?", (doc['partner_id'],))
        partner = cursor.fetchone()
        if not partner:
            partner = {}
            
        # Pridobivanje postavk
        cursor.execute("SELECT * FROM dokumenti_postavke WHERE dokument_id = ?", (doc_id,))
        items = [dict(i) for i in cursor.fetchall()]

        # Generiranje PDF-ja
        pdf_bytes = generate_pdf_invoice(dict(doc), dict(company), dict(partner), items)
        
        # Shranjevanje v datoteko - uporabimo predvidljivo ime za samodejni PDF
        st_safe = str(doc['stevilka']).replace('/', '-').replace('\\', '-').replace(' ', '-')
        
        doc_prefix = "Racun"
        if doc['tip'] == 'ponudbe': doc_prefix = "Ponudba"
        elif doc['tip'] == 'dobropisi': doc_prefix = "Dobropis"
        
        disk_filename = f"{doc_prefix}-{st_safe}.pdf"
        filepath = UPLOADS_DIR / disk_filename
        
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)
            
        # Posodobitev tabele priloge
        # Preverimo po filename, ker je ta unikaten za naš avtomatski PDF tega dokumenta
        cursor.execute("SELECT id FROM priloge WHERE parent_type = 'dokumenti' AND parent_id = ? AND filename = ?", (doc_id, disk_filename))
        existing = cursor.fetchone()
        
        if not existing:
            original_display_name = disk_filename
            
            cursor.execute("""
                INSERT INTO priloge (parent_type, parent_id, filename, original_name)
                VALUES (?, ?, ?, ?)
            """, ('dokumenti', doc_id, disk_filename, original_display_name))
        
        conn.commit()
        conn.close()
    except Exception:
        print("KRITIČNA NAPAKA pri samodejnem generiranju PDF priloge:")
        traceback.print_exc()
        if 'conn' in locals(): conn.close()

@app.get("/api/dokumenti/pdf/{id}")
def get_pdf_invoice(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    
    # Get invoice
    cursor.execute("SELECT * FROM dokumenti WHERE id = ?", (id,))
    inv = cursor.fetchone()
    if not inv:
        conn.close()
        return {"error": "Ni najdeno"}
    
    # Get partner
    cursor.execute("SELECT * FROM partnerji WHERE id = ?", (inv['partner_id'],))
    partner = cursor.fetchone()
    if not partner:
        partner = {"naziv": "Neznan partner", "ulica": "", "posta_kraj": "", "drzava": "", "davcna_stevilka": ""}
    
    # Get items
    cursor.execute("SELECT * FROM dokumenti_postavke WHERE dokument_id = ?", (id,))
    items = [dict(r) for r in cursor.fetchall()]
    
    # Get company settings
    cursor.execute("SELECT * FROM nastavitve WHERE id = 1")
    company = cursor.fetchone()
    conn.close()
    
    pdf_content = generate_pdf_invoice(dict(inv), dict(company), dict(partner), items)
    
    
    doc_title = "Racun"
    if inv['tip'] == 'ponudbe':
        doc_title = "Ponudba"
    elif inv['tip'] == 'dobropisi':
        doc_title = "Dobropis"
        
    return Response(content=pdf_content, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={doc_title}_{inv['stevilka']}.pdf"
    })

@app.post("/api/nastavitve/test_smtp")
def test_smtp(n: Nastavitve):
    if not n.smtp_server or not n.smtp_port or not n.smtp_username or not n.smtp_password:
        raise HTTPException(status_code=400, detail="Vnesite vse obvezne podatke za SMTP (strežnik, vrata, up. ime, geslo).")
    try:
        if n.smtp_use_tls:
            server = smtplib.SMTP(n.smtp_server, n.smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(n.smtp_username, n.smtp_password)
            server.quit()
        else:
            server = smtplib.SMTP_SSL(n.smtp_server, n.smtp_port, timeout=10)
            server.login(n.smtp_username, n.smtp_password)
            server.quit()
        return {"status": "success", "message": "Povezava s SMTP strežnikom je uspešna!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Napaka pri povezavi: {str(e)}")

@app.get("/api/email_log")
def get_email_log():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM email_log ORDER BY poslano_at DESC LIMIT 100")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/dokumenti/send_email/{id}")
def send_email_invoice(id: int, request: EmailRequest = None):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dokumenti WHERE id = ?", (id,))
    inv = cursor.fetchone()
    if not inv:
        conn.close()
        raise HTTPException(status_code=404, detail="Dokument ne obstaja")
    
    cursor.execute("SELECT * FROM partnerji WHERE id = ?", (inv['partner_id'],))
    partner_row = cursor.fetchone()
    if not partner_row or not partner_row['email']:
        conn.close()
        raise HTTPException(status_code=400, detail="Partner nima vnesenega e-naslova.")
        
    cursor.execute("SELECT * FROM dokumenti_postavke WHERE dokument_id = ?", (id,))
    items = [dict(r) for r in cursor.fetchall()]
    
    cursor.execute("SELECT * FROM nastavitve WHERE id = 1")
    company_row = cursor.fetchone()
    conn.close()
    
    if not company_row:
        raise HTTPException(status_code=500, detail="Nastavitve niso najdene v bazi.")
    
    company = dict(company_row)
    partner = dict(partner_row)
    
    if not company.get('smtp_server') or not company.get('smtp_port') or not company.get('smtp_username') or not company.get('smtp_password'):
        raise HTTPException(status_code=400, detail="SMTP nastavitve niso izpolnjene v Nastavitvah.")
        
    try:
        pdf_content = generate_pdf_invoice(dict(inv), company, partner, items)
        
        doc_title = "Racun"
        if inv['tip'] == 'ponudbe':
            doc_title = "Ponudba"
        elif inv['tip'] == 'dobropisi':
            doc_title = "Dobropis"
            
        filename = f"{doc_title}_{inv['stevilka']}.pdf"
        
        msg = MIMEMultipart()
        msg['From'] = company.get('email_posiljatelja') or company['smtp_username']
        msg['To'] = partner['email']
        msg['Subject'] = f"{doc_title} št. {inv['stevilka']} - {company.get('naziv', '')}"
        
        # Uporaba predloge besedila
        template = ""
        if inv['tip'] == 'izdani_racuni':
            template = company.get('email_template_racun')
        elif inv['tip'] == 'ponudbe':
            template = company.get('email_template_ponudba')
        elif inv['tip'] == 'dobropisi':
            template = company.get('email_template_dobropis')
            
        if not template:
            body = f"Spoštovani,\n\nv priponki vam pošiljamo dokument {doc_title} št. {inv['stevilka']}.\n\nLep pozdrav,\n{company.get('naziv', '')}"
        else:
            # Osnovna zamenjava placeholderjev
            body = template.replace("{stevilka}", str(inv['stevilka']))
            body = body.replace("{tip}", doc_title)
            body = body.replace("{podjetje}", company.get('naziv', ''))
            
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        part = MIMEApplication(pdf_content, Name=filename)
        part['Content-Disposition'] = f'attachment; filename="{filename}"'
        msg.attach(part)
        
        # Dodajanje izbranih prilog
        if request and request.priloge_ids:
            conn = database.get_db()
            cursor = conn.cursor()
            for p_id in request.priloge_ids:
                cursor.execute("SELECT * FROM priloge WHERE id = ?", (p_id,))
                p_row = cursor.fetchone()
                if p_row:
                    p_path = UPLOADS_DIR / p_row['filename']
                    if p_path.exists():
                        with open(p_path, "rb") as f:
                            p_part = MIMEApplication(f.read(), Name=p_row['original_name'])
                            p_part['Content-Disposition'] = f'attachment; filename="{p_row["original_name"]}"'
                            msg.attach(p_part)
            conn.close()
        
        if company['smtp_use_tls']:
            server = smtplib.SMTP(company['smtp_server'], int(company['smtp_port']), timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(company['smtp_username'], company['smtp_password'])
            server.send_message(msg)
            server.quit()
        else:
            server = smtplib.SMTP_SSL(company['smtp_server'], int(company['smtp_port']), timeout=10)
            server.login(company['smtp_username'], company['smtp_password'])
            server.send_message(msg)
            server.quit()
            
        # Logiranje uspešnega pošiljanja
        conn = database.get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO email_log (dokument_id, tip_dokumenta, stevilka_dokumenta, prejemnik, zadeva, status, poslano_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (id, inv['tip'], inv['stevilka'], partner['email'], msg['Subject'], 'success', get_now_slo()))
        conn.commit()
        conn.close()
            
        return {"status": "success", "message": "E-pošta je bila uspešno poslana."}
    except Exception as e:
        # Logiranje napake
        try:
            conn = database.get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO email_log (dokument_id, tip_dokumenta, stevilka_dokumenta, prejemnik, zadeva, status, napaka, poslano_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (id, inv['tip'], inv['stevilka'], partner['email'], f"{doc_title} št. {inv['stevilka']}", 'error', str(e), get_now_slo()))
            conn.commit()
            conn.close()
        except: pass
        raise HTTPException(status_code=500, detail=f"Napaka pri pošiljanju: {str(e)}")

def parse_eslog_xml(xml_data):
    """
    Parsira e-SLOG XML na način, ki ignorira namespace (Namespace-Agnostic).
    Podpira UBL, e-Slog 1.6 in EDI-XML (Telemach).
    """
    if xml_data.startswith(b'\xef\xbb\xbf'):
        xml_data = xml_data[3:]
        
    root = ET.fromstring(xml_data)
    
    def get_tag(el):
        return el.tag.split('}')[-1]

    def find_all(tag_name, root_el=root):
        """Poišče vse elemente z določenim imenom taga, ne glede na namespace."""
        res = []
        for el in root_el.iter():
            if get_tag(el) == tag_name:
                res.append(el)
        return res

    def find_one(tag_name, root_el=root):
        """Poišče prvi element z določenim imenom taga."""
        for el in root_el.iter():
            if get_tag(el) == tag_name:
                return el
        return None

    def find_path_val(path_list, root_el=root):
        """Preišče več možnih imen tagov in vrne vrednost prvega najdenega."""
        for name in path_list:
            el = find_one(name, root_el)
            if el is not None and el.text:
                return el.text.strip()
        return ""

    def find_edi_val(segment_tag, qual_tag, qual_val, value_tag, root_el=root):
        """Poišče EDIFACT segment (npr. S_DTM), preveri kvalifikator in vrne vrednost."""
        segments = find_all(segment_tag, root_el)
        for seg in segments:
            q_el = find_one(qual_tag, seg)
            if q_el is not None and q_el.text == qual_val:
                v_el = find_one(value_tag, seg)
                if v_el is not None and v_el.text:
                    return v_el.text.strip()
        return ""

    # 1. Številka računa
    stevilka = find_edi_val('S_BGM', 'D_1001', '380', 'D_1004')
    if not stevilka:
        # Bolj robusten fallback za EDIFACT (poišči D_1004 v kateremkoli S_BGM)
        for bgm in find_all('S_BGM'):
            v = find_one('D_1004', bgm)
            if v is not None and v.text:
                stevilka = v.text.strip()
                break
    if not stevilka:
        stevilka = find_path_val(['ID', 'ŠtevilkaRačuna', 'StevilkaRacuna', 'IdRačuna', 'InvoiceNumber', 'DocumentNumber'])
    


    # 2. Datumi
    datum_izdaje = find_edi_val('S_DTM', 'D_2005', '137', 'D_2380') or find_path_val(['IssueDate', 'DatumRačuna', 'DatumRacuna', 'DatumIzdaje', 'Datum'])
    if datum_izdaje and 'T' in datum_izdaje: datum_izdaje = datum_izdaje.split('T')[0]
    
    datum_zapadlosti = find_edi_val('S_DTM', 'D_2005', '13', 'D_2380') or find_edi_val('S_DTM', 'D_2005', '209', 'D_2380') or find_edi_val('S_DTM', 'D_2005', '35', 'D_2380')
    if not datum_zapadlosti:
        datum_zapadlosti = find_path_val(['DueDate', 'DatumValute', 'DatumZapadlosti'])
    if datum_zapadlosti and 'T' in datum_zapadlosti: datum_zapadlosti = datum_zapadlosti.split('T')[0]

    ds_od = find_edi_val('S_DTM', 'D_2005', '167', 'D_2380')
    ds_do = find_edi_val('S_DTM', 'D_2005', '168', 'D_2380')
    datum_storitve = find_path_val(['DatumOpravljeneStoritve', 'DatumStoritve', 'DatumDobave'])
    if not datum_storitve: datum_storitve = ds_do or datum_izdaje

    # 3. Partner (Dobavitelj)
    seller_naziv = ""
    seller_davcna = ""
    seller_ulica = ""
    seller_postna = ""
    seller_kraj = ""
    seller_trr = ""
    seller_telefon = ""
    seller_email = ""
    
    # Iščemo NAD segment s SE (Seller)
    g_sg2_nodes = find_all('G_SG2')
    for g2 in g_sg2_nodes:
        nad = find_one('S_NAD', g2)
        if nad is not None:
            q = find_one('D_3035', nad)
            if q is not None and q.text in ['SE', 'II', 'SU', 'PR']:
                v_naziv = find_one('D_3036', nad)
                if v_naziv is not None: seller_naziv = v_naziv.text.strip()
                
                # Ulica, Kraj, Pošta v EDIFACT
                v_ulica = find_one('D_3042', nad)
                if v_ulica is not None: seller_ulica = v_ulica.text.strip()
                v_kraj = find_one('D_3164', nad)
                if v_kraj is not None: seller_kraj = v_kraj.text.strip()
                v_postna = find_one('D_3251', nad)
                if v_postna is not None: seller_postna = v_postna.text.strip()
                
                # Davčna je v G2 nivoju (navadno v G_SG3/S_RFF)
                seller_davcna = find_edi_val('S_RFF', 'D_1153', 'VA', 'D_1154', g2) or find_edi_val('S_RFF', 'D_1153', 'AHP', 'D_1154', g2) or find_edi_val('S_RFF', 'D_1153', 'CR', 'D_1154', g2)
                
                # Telefon in Email (S_COM)
                seller_email = find_edi_val('S_COM', 'D_3155', 'EM', 'D_3148', g2)
                seller_telefon = find_edi_val('S_COM', 'D_3155', 'TE', 'D_3148', g2)
                
                # IBAN (S_FII)
                fii = find_one('S_FII', g2)
                if fii is not None:
                    v_iban = find_one('D_3194', fii)
                    if v_iban is not None: seller_trr = v_iban.text.strip()
                break
            
    # Iskanje končano. Če davčne nismo našli znotraj SE vozlišča, ne smemo
    # iskati globalno, ker bi lahko dobili davčno od kupca (uporabnika).
        
    if not seller_davcna or not seller_naziv:
        # Fallback na standardne UBL/1.6 nivoje
        seller_node = find_one('AccountingSupplierParty') or find_one('Izdajatelj') or find_one('Prodajalec')
        if seller_node is not None:
            seller_naziv = seller_naziv or find_path_val(['Name', 'RegistrationName', 'Naziv', 'PartyName/Name'], seller_node)
            seller_davcna = seller_davcna or find_path_val(['CompanyID', 'DavčnaŠtevilka', 'DavcnaStevilka', 'PartyTaxScheme/CompanyID'], seller_node)
            seller_ulica = seller_ulica or find_path_val(['PostalAddress/StreetName', 'Naslov'], seller_node)
            seller_postna = seller_postna or find_path_val(['PostalAddress/PostalZone', 'PoštnaŠtevilka'], seller_node)
            seller_kraj = seller_kraj or find_path_val(['PostalAddress/CityName', 'Kraj'], seller_node)
            seller_trr = seller_trr or find_path_val(['FinancialAccount/ID'], seller_node)
            seller_email = seller_email or find_path_val(['Contact/ElectronicMail', 'Email'], seller_node)
            seller_telefon = seller_telefon or find_path_val(['Contact/Telephone', 'Telefon'], seller_node)

    # VAT Payee detection
    is_zavezanec = seller_davcna and ('SI' in seller_davcna.upper())
    seller_davcna_cisto = re.sub(r'[^0-9]', '', seller_davcna).strip() if seller_davcna else ""

    # 4. Zneski (ostaja isto)
    # ...
    z_skupaj_s = find_edi_val('S_MOA', 'D_5025', '9', 'D_5004') or find_path_val(['PayableAmount', 'ZnesekZaPlačilo', 'ZnesekSkupaj'])
    znesek_skupaj = float((z_skupaj_s or "0").replace(',', '.'))
    
    z_neto_s = find_edi_val('S_MOA', 'D_5025', '79', 'D_5004') or find_path_val(['TaxExclusiveAmount', 'ZnesekBrezDDV', 'NetoZnesek'])
    znesek_brez_ddv = float((z_neto_s or "0").replace(',', '.'))
    
    znesek_ddv = znesek_skupaj - znesek_brez_ddv

        # 5. Postavke
    postavke = []
    lines = find_all('G_SG26') or find_all('InvoiceLine') or find_all('Postavka')
    
    for line in lines:
        opis = find_path_val(['D_7008', 'Name', 'Opis', 'NazivArtikla'], line)
        if not opis: continue
        
        kol_s = find_path_val(['D_6060', 'InvoicedQuantity', 'Količina', 'Kolicina'], line)
        kolicina = float((kol_s or "1").replace(',', '.'))
        
        # Stopnja DDV (Percent v UBL, D_5278 v EDIFACT)
        ddv_s = find_path_val(['Percent', 'D_5278'], line)
        ddv_rate = float((ddv_s or "22.0").replace(',', '.'))
        
        # Neto znesek postavke
        skupaj_l_neto_s = find_edi_val('S_MOA', 'D_5025', '203', 'D_5004', line) or find_path_val(['LineExtensionAmount', 'ZnesekPostavke', 'Znesek'], line)
        skupaj_l_neto = float((skupaj_l_neto_s or "0").replace(',', '.'))
        
        # Znesek skupaj (bruto, z DDV)
        skupaj_l_bruto = skupaj_l_neto * (1 + ddv_rate / 100)

        # Cena na enoto: vedno izračunamo iz skupaj / kolicina za konsistentnost
        # (PriceAmount v XML se ne ujema vedno s formulo kolicina × cena)
        if kolicina != 0:
            cena_enote = skupaj_l_bruto / kolicina
        else:
            # Fallback na PriceAmount iz XML
            cena_s = find_path_val(['D_5118', 'PriceAmount', 'Cena'], line)
            cena_neto = float((cena_s or "0").replace(',', '.'))
            cena_enote = cena_neto * (1 + ddv_rate / 100) if cena_neto > 0 else skupaj_l_bruto

        postavke.append({
            "opis": opis,
            "kolicina": kolicina,
            "cena_enote": round(cena_enote, 6),
            "znesek_skupaj": round(skupaj_l_bruto, 2),
            "stopnja_ddv": ddv_rate
        })

    return {
        "stevilka": stevilka or "NEZNANA",
        "datum_izdaje": datum_izdaje or "2026-01-01",
        "datum_zapadlosti": datum_zapadlosti or datum_izdaje,
        "datum_storitve": datum_storitve or datum_izdaje,
        "datum_storitve_od": ds_od or datum_izdaje,
        "datum_storitve_do": ds_do or datum_izdaje,
        "partner": {
            "naziv": seller_naziv or "Neznan dobavitelj",
            "davcna_stevilka": seller_davcna_cisto,
            "ulica": seller_ulica or "",
            "postna_stevilka": seller_postna or "",
            "kraj": seller_kraj or "",
            "drzava": "Slovenija",
            "trr": seller_trr or "",
            "telefon": seller_telefon or "",
            "email": seller_email or "",
            "zavezanec_za_ddv": is_zavezanec
        },
        "znesek_skupaj": znesek_skupaj,
        "znesek_brez_ddv": znesek_brez_ddv,
        "znesek_ddv": znesek_ddv,
        "postavke": postavke
    }

@app.post("/api/dokumenti/import_eslog_pregled")
async def import_eslog_pregled(file: UploadFile = File(...)):
    try:
        content = await file.read()
        results = []
        
        if file.filename.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    if name.lower().endswith('.xml'):
                        xml_content = z.read(name)
                        try:
                            parsed = parse_eslog_xml(xml_content)
                            enriched = _enrich_eslog_data(parsed)
                            results.append(enriched)
                        except Exception as e:
                            print(f"Error parsing {name}: {e}")
                    elif name.lower().endswith('.png') or name.lower().endswith('.pdf'):
                        # AliExpress ali Temu?
                        f_content = z.read(name)
                        try:
                            parsed = None
                            if name.lower().endswith('.png'):
                                parsed = extract_aliexpress_png(f_content)
                            else:
                                parsed = extract_temu_pdf(f_content)
                            
                            if parsed:
                                enriched = _enrich_eslog_data(parsed)
                                enriched['file_data'] = base64.b64encode(f_content).decode('utf-8')
                                enriched['file_name'] = name
                                results.append(enriched)
                        except Exception as e:
                            print(f"Error parsing Ali/Temu {name}: {e}")
        else:
            if file.filename.lower().endswith('.xml'):
                parsed = parse_eslog_xml(content)
                enriched = _enrich_eslog_data(parsed)
                results.append(enriched)
            elif file.filename.lower().endswith('.png'):
                parsed = extract_aliexpress_png(content)
                enriched = _enrich_eslog_data(parsed)
                enriched['file_data'] = base64.b64encode(content).decode('utf-8')
                enriched['file_name'] = file.filename
                results.append(enriched)
            elif file.filename.lower().endswith('.pdf'):
                parsed = extract_temu_pdf(content)
                enriched = _enrich_eslog_data(parsed)
                enriched['file_data'] = base64.b64encode(content).decode('utf-8')
                enriched['file_name'] = file.filename
                results.append(enriched)

        return {"items": results, "count": len(results)}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def _enrich_eslog_data(data):
    # Preveri če partner obstaja
    davcna = (data['partner'].get('davcna_stevilka') or "").strip()
    naziv = (data['partner'].get('naziv') or "").strip()
    
    conn = database.get_db()
    cursor = conn.cursor()
    row = None
    
    if davcna:
        # 1. Poskusi ujemanje po davčni številki (samo če ni prazna)
        cursor.execute("SELECT id, naziv FROM partnerji WHERE davcna_stevilka = ?", (davcna,))
        row = cursor.fetchone()
    
    if not row and naziv:
        # 2. Poskusi ujemanje po točnem nazivu
        cursor.execute("SELECT id, naziv FROM partnerji WHERE UPPER(naziv) = UPPER(?)", (naziv,))
        row = cursor.fetchone()
        
    if not row and naziv:
        # 3. Posebna pravila za tuje platforme
        if naziv.lower() == 'aliexpress':
            cursor.execute("SELECT id, naziv FROM partnerji WHERE UPPER(naziv) LIKE '%ALIBABA%' OR UPPER(naziv) = 'ALIEXPRESS'")
            row = cursor.fetchone()
        elif naziv.lower() == 'temu':
            cursor.execute("SELECT id, naziv FROM partnerji WHERE UPPER(naziv) LIKE '%WHALECO%' OR UPPER(naziv) = 'TEMU'")
            row = cursor.fetchone()

    conn.close()
    
    data['partner_obstaja'] = row is not None
    data['bizi_enriched'] = False
    data['bizi_enriched'] = False

    if row:
        data['partner']['id'] = row['id']
        data['partner']['naziv'] = row['naziv']
    else:
        # Novi partner — obogatimo podatke z Bizi.si
        naziv_za_iskanje = data['partner'].get('naziv', '')
        if naziv_za_iskanje:
            try:
                bizi_results = _bizi_search(naziv_za_iskanje)
                if bizi_results:
                    best = bizi_results[0]
                    posta_kraj_split = best.get('posta_kraj', '').split(' ', 1)
                    postna = posta_kraj_split[0] if len(posta_kraj_split) > 0 else ''
                    kraj = posta_kraj_split[1] if len(posta_kraj_split) > 1 else ''
                    
                    detail = _bizi_detail_full(best['link'])
                    data['partner']['naziv'] = best['naziv'] or data['partner']['naziv']
                    data['partner']['ulica'] = best.get('naslov') or data['partner'].get('ulica', '')
                    data['partner']['postna_stevilka'] = postna or data['partner'].get('postna_stevilka', '')
                    data['partner']['kraj'] = kraj or data['partner'].get('kraj', '')
                    data['partner']['telefon'] = detail.get('telefon') or data['partner'].get('telefon', '')
                    data['partner']['email'] = detail.get('email') or data['partner'].get('email', '')
                    data['partner']['trr'] = detail.get('trr') or data['partner'].get('trr', '')
                    data['partner']['zavezanec_za_ddv'] = detail.get('zavezanec_za_ddv', best.get('zavezanec_za_ddv', False))
                    data['bizi_enriched'] = True
            except Exception as bizi_err:
                print(f"Bizi.si enrichment failed: {bizi_err}")
    return data

@app.post("/api/dokumenti/import_eslog_bulk_potrdi")
async def import_eslog_bulk_potrdi(request_data: dict):
    items = request_data.get("items", [])
    results = []
    for data in items:
        try:
            doc_id = await _save_imported_eslog(data)
            results.append(doc_id)
        except Exception as e:
            print(f"Bulk save error: {e}")
    return {"status": "success", "count": len(results), "ids": results}

async def _save_imported_eslog(data):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        # 1. Partner
        partner_id = data['partner'].get('id')
        if not partner_id:
            p = data['partner']
            cursor.execute("""
                INSERT INTO partnerji (naziv, ulica, postna_stevilka, kraj, drzava, davcna_stevilka, zavezanec_za_ddv, trr, telefon, email, vrsta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'dobavitelj')
            """, (
                p.get('naziv', ''), p.get('ulica', ''), p.get('postna_stevilka', ''),
                p.get('kraj', ''), p.get('drzava', 'Slovenija'), p.get('davcna_stevilka', ''),
                1 if p.get('zavezanec_za_ddv') else 0, p.get('trr', ''), p.get('telefon', ''), p.get('email', '')
            ))
            partner_id = cursor.lastrowid
        
        # 2. Dokument
        poslovno_leto = int(data['datum_izdaje'].split('-')[0]) if '-' in data['datum_izdaje'] else 2026
        status = 'neplačano'
        datum_placila = None
        nacin_placila = None
        
        if data.get('placan'):
            status = 'plačano'
            datum_placila = data['datum_izdaje']
            nacin_placila = 'Poslovna kartica'

        cursor.execute("""
            INSERT INTO dokumenti (poslovno_leto, tip, stevilka, partner_id, datum_izdaje, datum_zapadlosti, 
                                   datum_storitve_od, datum_storitve_do, znesek_brez_ddv, znesek_ddv, 
                                   znesek_skupaj, valuta, tecaj, znesek_v_valuti, status, datum_placila, nacin_placila)
            VALUES (?, 'prejeti_racuni', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (poslovno_leto, data['stevilka'], partner_id, data['datum_izdaje'], data['datum_zapadlosti'], 
              data['datum_storitve_od'], data['datum_storitve_do'], data['znesek_brez_ddv'], 
              data['znesek_ddv'], data['znesek_skupaj'], data.get('valuta', 'EUR'), 
              data.get('tecaj', 1.0), data.get('znesek_v_valuti', data['znesek_skupaj']),
              status, datum_placila, nacin_placila))
        
        doc_id = cursor.lastrowid

        # 3. Priloga (v isti transakciji!)
        if 'file_data' in data and 'file_name' in data:
            file_content = base64.b64decode(data['file_data'])
            ext = Path(data['file_name']).suffix.lower()
            unique_name = f"{uuid.uuid4().hex}{ext}"
            dest = UPLOADS_DIR / unique_name
            with open(dest, "wb") as f:
                f.write(file_content)
            
            cursor.execute(
                "INSERT INTO priloge (parent_type, parent_id, filename, original_name, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                ('dokumenti', doc_id, unique_name, data['file_name'], get_now_slo())
            )
        
        # 4. Postavke
        for it in data['postavke']:
            cursor.execute("""
                INSERT INTO dokumenti_postavke (dokument_id, opis, kolicina, cena_enote, stopnja_ddv, znesek_skupaj, konto)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, it['opis'], it['kolicina'], it['cena_enote'], it['stopnja_ddv'], it['znesek_skupaj'], it.get('konto')))
        
        conn.commit()
        return doc_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

@app.post("/api/dokumenti/import_eslog_potrdi")
async def import_eslog_potrdi(data: dict):
    try:
        doc_id = await _save_imported_eslog(data)
        return {"status": "success", "id": doc_id}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class ZakljucnoBesedilo(BaseModel):
    id: Optional[int] = None
    naziv: str
    besedilo: str

@app.get("/api/zakljucna_besedila")
def get_zakljucna_besedila():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM zakljucna_besedila")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/zakljucna_besedila")
def create_zakljucno_besedilo(zb: ZakljucnoBesedilo):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO zakljucna_besedila (naziv, besedilo) VALUES (?, ?)", (zb.naziv, zb.besedilo))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/zakljucna_besedila/{id}")
def update_zakljucno_besedilo(id: int, zb: ZakljucnoBesedilo):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE zakljucna_besedila SET naziv=?, besedilo=? WHERE id = ?", (zb.naziv, zb.besedilo, id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/zakljucna_besedila/{id}")
def delete_zakljucno_besedilo(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM zakljucna_besedila WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/tecaj")
def get_tecaj(valuta: str, datum: Optional[str] = "latest"):
    try:
        # Frankfurter API včasih zavrača klice iz brskalnika (CORS), zato to naredimo na strežniku
        url = f"https://api.frankfurter.app/{datum}?from={valuta}&to=EUR"
        response = requests.get(url, timeout=5)
        
        if response.status_code != 200 and datum != "latest":
            # Poskusimo z 'latest' če datum ne obstaja
            response = requests.get(f"https://api.frankfurter.app/latest?from={valuta}&to=EUR", timeout=5)
            
        if response.status_code == 200:
            return response.json()
        else:
            return {"rates": {"EUR": 1.0}, "error": "API response error"}
    except Exception as e:
        return {"rates": {"EUR": 1.0}, "error": str(e)}

def _add_attachment(parent_type, parent_id, filename, content):
    ext = Path(filename).suffix.lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / unique_name
    
    with open(dest, "wb") as f:
        f.write(content)
    
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO priloge (parent_type, parent_id, filename, original_name, uploaded_at) VALUES (?, ?, ?, ?, ?)",
        (parent_type, parent_id, unique_name, filename, get_now_slo())
    )
    conn.commit()
    conn.close()

def extract_aliexpress_png(content):
    try:
        img = Image.open(io.BytesIO(content))
        # Izboljšava slike za OCR
        img = img.convert('L') # Grayscale
        ocr_text = pytesseract.image_to_string(img, lang='eng')
    except Exception as e:
        print(f"OCR Error: {e}")
        ocr_text = ""

    m_map = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06','Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
    order_date = datetime.now().strftime("%Y-%m-%d")
    
    pats = [
        r'(?:Order time|Paid on|Date)[^\w]*([A-Za-z]{3})\s+(\d{1,2})[,\.\s]+(\d{4})',
        r'([A-Za-z]{3})\s+(\d{1,2})[,\.\s]+(\d{4})', 
        r'(\d{1,2})\s+([A-Za-z]{3})[,\.\s]+(\d{4})',
        r'([A-Za-z]{3,10})\s+(\d{1,2})[,\.\s]+(\d{4})', # Celotna imena mesecev
        r'(\d{4})-(\d{2})-(\d{2})'
    ]
    for pat in pats:
        m = re.search(pat, ocr_text, re.IGNORECASE)
        if m:
            try:
                g = m.groups()
                # Če imamo 4 grupe (npr. z "Order time" prefixom), zamaknemo indekse? 
                # Ne, (?:...) je non-capturing, tako da so grupe še vedno 3.
                if '([A-Za-z]' in pat:
                    mm, d, y = g[0][:3].capitalize(), g[1].zfill(2), g[2]
                    if mm in m_map: order_date = f"{y}-{m_map[mm]}-{d}"
                elif '(\\d{1,2})\\s+([A-Za-z]' in pat:
                    d, mm, y = g[0].zfill(2), g[1][:3].capitalize(), g[2]
                    if mm in m_map: order_date = f"{y}-{m_map[mm]}-{d}"
                else:
                    order_date = f"{g[0]}-{g[1]}-{g[2]}"
                break
            except: continue
    
    order_id_match = re.search(r'\b(\d{15,18})\b', ocr_text)
    order_id = order_id_match.group(1) if order_id_match else "Neznano"
        
    total_val = 0.00
    # Iskanje "Total" zneska IZKLJUČNO v isti vrstici (preprečimo prehod v vrstico z VAT)
    total_match = re.search(r'Total[^\d\n\r]*([\d]+[\.,][\d]{2})', ocr_text, re.IGNORECASE)
    eur_match = re.search(r'EUR[^\d\n\r]*([\d]+[\.,][\d]{2})', ocr_text, re.IGNORECASE)
    
    # Najdi vse zneske, ki niso "VAT included"
    all_potential = []
    # Regex, ki najde zneske in preveri okolico (negativni lookahead za VAT ni podprt v re, zato bomo filtrirali ročno)
    for m in re.finditer(r'([\d]+[\.,][\d]{2})', ocr_text):
        val = float(m.group(1).replace(',', '.'))
        # Preveri če je v bližini beseda VAT
        start = max(0, m.start() - 20)
        end = min(len(ocr_text), m.end() + 20)
        context = ocr_text[start:end].lower()
        if 'vat' not in context:
            all_potential.append(val)
    
    if total_match: total_val = float(total_match.group(1).replace(',', '.'))
    elif eur_match: total_val = float(eur_match.group(1).replace(',', '.'))
    elif all_potential: total_val = max(all_potential)

    base_val = total_val / 1.22
    vat_val = total_val - base_val
    
    return {
        "stevilka": order_id,
        "datum_izdaje": order_date,
        "datum_zapadlosti": order_date,
        "datum_storitve_od": order_date,
        "datum_storitve_do": order_date,
        "partner": {
            "naziv": "Aliexpress",
            "davcna_stevilka": "",
            "ulica": "", "postna_stevilka": "", "kraj": "", "drzava": "Kitajska",
            "zavezanec_za_ddv": False
        },
        "znesek_skupaj": total_val,
        "znesek_brez_ddv": round(base_val, 2),
        "znesek_ddv": round(vat_val, 2),
        "valuta": "EUR",
        "tecaj": 1.0,
        "postavke": [{
            "opis": f"Nakup Aliexpress {order_id}",
            "kolicina": 1,
            "cena_enote": total_val,
            "stopnja_ddv": 22,
            "znesek_skupaj": total_val
        }]
    }

def extract_generic_pdf(content):
    """Generični parser PDF računov - deluje z večino SLO in tujih dobaviteljev."""
    pdf_text = ""
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text()
                if txt: pdf_text += txt + "\n"
    except Exception as e:
        print(f"PDF Error: {e}")

    if not pdf_text.strip():
        return None  # Skenirani/slikovni PDF - ni besedila
        
    # Globalno čiščenje problematičnih znakov (Artlist em-dash sredi besed, itd.)
    pdf_text = pdf_text.replace('\u2014', '').replace('\u2013', '-')

    def cn(s):
        """Pretvori evropski format številke v float."""
        if not s: return 0.0
        s = s.strip().rstrip('€').strip()
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
        elif ',' in s:
            s = s.replace(',', '.')
        try: return round(float(s), 2)
        except: return 0.0

    def pd(raw):
        """Pretvori niz datuma v YYYY-MM-DD."""
        if not raw: return datetime.now().strftime("%Y-%m-%d")
        raw = raw.strip()
        mm = {'jan':'01','feb':'02','mar':'03','apr':'04','maj':'05','may':'05',
              'jun':'06','jul':'07','avg':'08','aug':'08','sep':'09',
              'okt':'10','oct':'10','nov':'11','dec':'12'}
        m = re.search(r'(\d{1,2})[.\s]+(\d{1,2})[.\s]+(\d{4})', raw)
        if m: return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
        m = re.search(r'(\d{1,2})[.\s]+([a-z]{3})[.\s]+(\d{4})', raw, re.I)
        if m and m.group(2).lower() in mm: return f"{m.group(3)}-{mm[m.group(2).lower()]}-{m.group(1).zfill(2)}"
        m = re.search(r'([A-Za-z]{3,9})\s+(\d{1,2})[,.]?\s+(\d{4})', raw)
        if m and m.group(1).lower()[:3] in mm: return f"{m.group(3)}-{mm[m.group(1).lower()[:3]]}-{m.group(2).zfill(2)}"
        return datetime.now().strftime("%Y-%m-%d")

    def first_match(patterns, text):
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                v = m.group(1).strip()
                if v: return v
        return ""

    # ---- ŠTEVILKA RAČUNA ----
    stevilka = first_match([
        r'(?:Številka\s+ra[cč]una\s*:\s*|Številka\s+dokumenta\s*:\s*)([A-Z0-9][A-Z0-9\-_/\.]{1,30})',
        r'(?:Invoice\s+#\s*|Invoice\s+No\.?\s*:?\s*)([A-Z0-9][\w\-]{1,30})',
        r'(?:Interna\s+številka\s+dok\.\s*:\s*)([A-Z0-9][\w\-]{1,20})',
        r'(?:RA[CČ]UN\s+ŠT\.|Ra[cč]un\s+(?:št\.|#))\s*:?\s*([A-Z0-9][\w\-/\.]{1,30})',
        r'(?<![a-zA-Z])(?:Številka|Stevilka)\s*:\s*([A-Z0-9][\w/\-\.]{1,20})',
        r'RAČUN\s+([0-9]+/[0-9]+)',  # BM Racun
        r'RA\s*UN[^\d]+([1-9][A-Z0-9\-_/\.]{5,20})',  # Tuli
        r'Interna\s+številka\s+([\w\-]{3,25})',
        r'(?:Vaša\s+oznaka.*?Št\.\s+računa.*?)(\d[\w\-/\.]{3,20})(?:\s+\d)',  # Conrad
    ], pdf_text)
    if not stevilka or len(stevilka) < 2: stevilka = "Neznano"

    # ---- DATUM RAČUNA ----
    datum_raw = first_match([
        r'(?:Datum\s+(?:in\s+ura\s+)?ra[cč]una|Datum\s+dokumenta)\s*[:\s,]+\s*(\d{1,2}[.\s]+\d{1,2}[.\s]+\d{4})',
        r'(?:Datum\s+izdaje)\s*[:\s]+\n?(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})',
        r'(?:Invoice\s+Date)\s+[A-Z]?\u2014?([A-Za-z]{2,9}\s+\d{1,2}[,.]?\s+\d{4})',  # Artlist
        r'(?:Datum:\s*)(\d{1,2}\.\d{1,2}\.\d{4})',
        # Google oblika: "31. jan. 2026"
        r'(\d{1,2}\.\s+[a-z]{3}\.\s+\d{4})',
        # Tuli: "Datum ra una: 12.01.2026 ob"
        r'Datum\s+ra\s*una:\s*(\d{1,2}\.\d{2}\.\d{4})',
        # IKEA/BM fallback: DD. M. YYYY ali DD.MM.YYYY v dokumentu
        r'(\d{2}\.\d{2}\.\d{4})\s+(?:\d{2}:\d{2})',
        r',\s*(\d{1,2}\.\d{1,2}\.\d{4})',  # BM: Celje, 26.01.2026
    ], pdf_text)
    datum_izdaje = pd(datum_raw)

    # ---- DATUM ZAPADLOSTI ----
    zap_raw = first_match([
        r'(?:Zapadlost\s+ra[cč]una|Zapadlost|Datum\s+valute|VALUTA)\s*[:\s]+(\d{1,2}[.\s]+\d{1,2}[.\s]+\d{4})',
        r'(?:Zapadlost:)\s*(\d{1,2}\.\d{1,2}\.\d{4})',
    ], pdf_text)
    datum_zapadlosti = pd(zap_raw) if zap_raw else datum_izdaje

    # ---- SKUPNI ZNESEK ----
    total_val = 0.0
    for pat in [
        r'(?:Za\s+pla[cč]ilo\s+EUR\s*:|ZA\s+PLA[CČ]ILO[:\s]*EUR)[:\s€]*([0-9]+[,\.][0-9]{2})',
        r'(?:Za\s+pla[cč]ilo\s+EUR\s*:?)\s+([0-9]+[,\.][0-9]{2})',
        r'Skupaj\s+za\s+pla[cč]ilo\s+EUR\s+([0-9]+[,\.][0-9]{2})',
        r'(?:Skupaj\s+za\s+pla[cč]ilo)[:\s€]*([0-9]+[,\.][0-9]{2})',
        r'(?:Skupni\s+znesek(?:\s+v\s+valuti\s+EUR)?)[:\s€]*([0-9]+[,\.][0-9]{2})',
        r'(?:Pla[cč]ano|PLA.ANO)[:\s€]*([0-9]+[,\.][0-9]{2})\s+EUR',  # Temu/Tuli
        r'SKUPAJ\s+RA[^\s]*\s*UN\s+EUR\s+([0-9]+[,\.][0-9]{2})',  # Tuli encoding
        r'N\s+ZA\s+PLA[^\s]*\s*ILO\s+EUR\s+([0-9]+[,\.][0-9]{2})',  # Tuli encoding
        r'(?:Invoice\s+Amou[n\u2014\-]*t)[:\s€]*([0-9]+[,\.][0-9]{2})',
        r'(?:Total)[:\s]*([0-9]+[,\.][0-9]{2})\s*€',
        r'([0-9]+[,\.][0-9]{2})\s+Za\s+pla[cč]ilo\s+EUR',  # GMT
    ]:
        m = re.search(pat, pdf_text, re.IGNORECASE)
        if m:
            v = cn(m.group(1))
            if v > 0: total_val = v; break

    # Fallback: DDV rekapitulacija
    if total_val == 0:
        tbl = re.search(r'(?:Skupaj|Total)\s+[0-9,\.]+\s+[0-9,\.]+\s+([0-9]+[,\.][0-9]{2})', pdf_text, re.IGNORECASE)
        if tbl: total_val = cn(tbl.group(1))
    # Fallback2: max znesek z EUR simbolom v dokumentu
    if total_val == 0:
        all_eur = re.findall(r'([0-9]+[,\.][0-9]{2})\s*(?:EUR|€)', pdf_text)
        if all_eur:
            total_val = max(cn(x) for x in all_eur)

    # ---- OSNOVA (BREZ DDV) ----
    net_val = 0.0
    for pat in [
        r'(?:Neto\s+znesek|Znesek\s+brez\s+DDV|VREDNOST\s+brez\s+DDV|Skupaj\s+brez\s+DDV)\s*[:\s€]*([0-9]+[,\.][0-9]{2})',
        r'DDV\s+22[,\.]0+%\s+od\s+osnove\s+([0-9]+[,\.][0-9]{2})',
        r'(?:Osnova\s+za\s+DDV|Osnova\s+DDV)\s*[:\s]*([0-9]+[,\.][0-9]{2})',
        r'Stopnja\s+22[,\.]?0?\s*%\s+([0-9]+[,\.][0-9]{2})',
        r'(?:D[01]\s+-[^0-9]+(?:22|0)[,\.]?0*%)\s+([0-9]+[,\.][0-9]{2})',
        # Shopster: DDV rekapitulacija tabela "22 % 36,60 8,05 44,65"
        r'22\s*%\s+([0-9]+[,\.][0-9]{2})\s+[0-9]+[,\.][0-9]{2}\s+[0-9]+[,\.][0-9]{2}',
        # GMT: Osnova DDV v tabeli DAVČNE STOPNJE
        r'22%\s+([0-9]+[,\.][0-9]{2})\s+[0-9]+[,\.][0-9]{2}\s+[0-9]+[,\.][0-9]{2}',
    ]:
        m = re.search(pat, pdf_text, re.IGNORECASE)
        if m:
            v = cn(m.group(1))
            if v > 0: net_val = v; break

    # ---- DDV ZNESEK ----
    vat_val = 0.0
    for pat in [
        r'DDV\s+\(?22[,\.]0?%?\)?\s+([0-9]+[,\.][0-9]{2})',
        r'DDV\s+22[,\.]\d+%?\s+(?:od\s+osnove\s+[0-9,\.]+\s+EUR\s+)?([0-9]+[,\.][0-9]{2})',
        r'(?:Znesek\s+davka|Znesek\s+DDV)\s+([0-9]+[,\.][0-9]{2})',
        r'22\s*%\s+[0-9,\.]+\s+([0-9]+[,\.][0-9]{2})',
    ]:
        m = re.search(pat, pdf_text, re.IGNORECASE)
        if m:
            v = cn(m.group(1))
            if v > 0: vat_val = v; break

    # Posebni primeri: 0% DDV (reverse charge - Google Ads, Artlist)
    explicit_zero_vat = bool(re.search(r'DDV\s*\(0%\)|0%.*reverse\s+charge|VAT\s+Exemption|self.account.*VAT', pdf_text, re.I))

    # Izračun manjkajočih vrednosti
    if total_val > 0 and net_val == 0 and vat_val == 0:
        if explicit_zero_vat:
            net_val = total_val  # 0% DDV
        else:
            net_val = round(total_val / 1.22, 2); vat_val = round(total_val - net_val, 2)
    elif total_val > 0 and net_val > 0 and vat_val == 0 and not explicit_zero_vat:
        vat_val = round(total_val - net_val, 2)
    elif total_val == 0 and net_val > 0:
        total_val = round(net_val + vat_val, 2)

    # ---- DOBAVITELJ (PARTNER) ----
    # Poišči ID za DDV dobavitelja (izključi kupčevo SI11648236)
    partner_naziv = "Neznan dobavitelj"
    partner_davcna = ""
    partner_drzava = "Slovenija"

    # Znani tuji dobavitelji
    if re.search(r'google\s+ireland|google\s+ads', pdf_text, re.I):
        partner_naziv = "Google Ireland Limited"; partner_davcna = "IE6388047V"; partner_drzava = "Irska"
    elif re.search(r'artlist', pdf_text, re.I):
        partner_naziv = "Artlist UK Ltd"; partner_davcna = "GB770403942"; partner_drzava = "Združeno kraljestvo"
    elif re.search(r'PO-\d{3}-\d+|temu\.com', pdf_text, re.I):
        partner_naziv = "Temu"; partner_drzava = "Kitajska"
    elif re.search(r'aliexpress', pdf_text, re.I):
        partner_naziv = "Aliexpress"; partner_drzava = "Kitajska"
    else:
        # SLO dobavitelji: ID za DDV iz prve SI... številke ki ni kupčeva
        vat_matches = re.findall(r'(?:ID\s+(?:za|za:)\s+DDV|ID\s+DDV|Za\s+DDV)[:\s]+SI(\d{8})', pdf_text, re.I)
        for v in vat_matches:
            if v != '11648236':  # Izključi kupčevo DAV
                partner_davcna = v; break

        # Posebni primeri dobaviteljev na podlagi celotnega besedila
        tl = pdf_text.lower()
        if 'mimovrste' in tl: partner_naziv = "Mimovrste d.o.o."
        elif 'shoppster' in tl: partner_naziv = "Shoppster d.o.o."
        elif 'ikea' in tl: partner_naziv = "IKEA Slovenija d.o.o."
        elif 'bauhaus' in tl: partner_naziv = "BAUHAUS d.o.o."
        elif 'conrad' in tl: partner_naziv = "Conrad Electronic d.o.o."
        elif 'b&m' in tl or 'eloksiranje' in tl: partner_naziv = "ELOKSIRANJE B&M d.o.o."
        elif 'gmt' in tl: partner_naziv = "GMT d.o.o."
        
        if partner_naziv == "Neznan dobavitelj":
            # Ime dobavitelja: prve vrstice dokumenta (pred kupcem)
            lines = [l.strip() for l in pdf_text.split('\n') if l.strip()]
            buyer_keywords = ['miha kadiš', 'sim 83', 'simulatorji', 'dobja vas 253', 'kadiš s.p', 'kupec',
                              'stran 1', 'stran 2', 'stran\xa0', 'st.kopije', 'št.kopije', 'račun za gosta',
                              'dobavitelj:', 'račun', 'sklicna', 'konstantni', 'ra\u010dun', 's.p. celje', 'ravne na koro']
            company_keywords = ['d.o.o', 'd.d.', 's.p.', 'k.d.', 'ltd', 'limited', 'gmbh', 'inc']
            skip_starts = ('Tel', 'Fax', 'E-po', 'info@', 'www.', 'Stran', 'ZOI', 'EOR', 'IBAN', 'Mat', 'ID', 'Kontaktni', 'Datum', 'Valuta')
            for line in lines[:25]:
                ll = line.lower()
                if any(k in ll for k in buyer_keywords): continue
                if len(line) < 4: continue
                if re.match(r'^[\d\s\.\-:/]+$', line): continue
                if line.startswith(skip_starts): continue
                
                if any(k in ll for k in company_keywords):
                    partner_naziv = line
                    break
                if partner_davcna and len(line) > 5 and not any(k in ll for k in ['slovenija', 'ljubljana', 'celje']):
                    partner_naziv = line
                    break

    stopnja_ddv = 0 if vat_val == 0 else 22
    
    # Hevristični poizkus branja postavk računa (Količina * Cena = Skupaj)
    postavke = []
    try:
        for line in pdf_text.split('\n'):
            line = line.strip()
            if len(line) < 10: continue
            # Preskoči vrstice ki vsebujejo ključne besede za rekapitulacijo
            if re.search(r'(skupaj|ddv|pla[cč]ilo|znesek|osnova|popust|zapadlost|valuta|stran|iban)', line, re.I): continue
            m = re.search(r'^(.+?)\s+(\d+[,\.]?\d*)\s*(?:kos|kg|m|kom|ur|h|uro|ura|kosa|kosi|kosov|lit|l|par|kpl|x|)\s+(\d+[,\.]\d{2,4})\s+(\d+[,\.]\d{2,4})\s*$', line, re.I)
            if m:
                opis = m.group(1).strip()
                kol = cn(m.group(2))
                cena = cn(m.group(3))
                sk = cn(m.group(4))
                
                # Sanity check: Količina * Cena enote = Skupaj (+- 5 centov)
                if kol > 0 and cena > 0 and abs((kol * cena) - sk) <= 0.05:
                    postavke.append({
                        "opis": opis,
                        "kolicina": kol,
                        "cena_enote": cena,
                        "stopnja_ddv": stopnja_ddv,
                        "znesek_skupaj": sk
                    })
    except:
        pass

    # Prilagoditev: Če so izluščene postavke brez DDV (njihova vsota je blizu net_val), 
    # jim dodamo DDV, saj naša aplikacija pričakuje cene z DDV!
    if postavke and stopnja_ddv > 0:
        sum_skupaj = sum(p["znesek_skupaj"] for p in postavke)
        if abs(sum_skupaj - net_val) < abs(sum_skupaj - total_val):
            for p in postavke:
                p["cena_enote"] = round(p["cena_enote"] * (1 + stopnja_ddv / 100), 4)
                p["znesek_skupaj"] = round(p["znesek_skupaj"] * (1 + stopnja_ddv / 100), 2)

    # Fallback na generično postavko, če heuristika ni našla nič pametnega
    if not postavke:
        postavke = [{
            "opis": f"Uvoz računa {stevilka}",
            "kolicina": 1,
            "cena_enote": net_val,
            "stopnja_ddv": stopnja_ddv,
            "znesek_skupaj": total_val,
        }]

    return {
        "stevilka": stevilka,
        "datum_izdaje": datum_izdaje,
        "datum_zapadlosti": datum_zapadlosti,
        "datum_storitve_od": datum_izdaje,
        "datum_storitve_do": datum_izdaje,
        "partner": {
            "naziv": partner_naziv,
            "davcna_stevilka": partner_davcna,
            "ulica": "", "postna_stevilka": "", "kraj": "",
            "drzava": partner_drzava,
            "zavezanec_za_ddv": bool(partner_davcna),
        },
        "znesek_skupaj": total_val,
        "znesek_brez_ddv": net_val,
        "znesek_ddv": vat_val,
        "valuta": "EUR",
        "tecaj": 1.0,
        "placan": False,
        "postavke": postavke
    }

def extract_temu_pdf(content):
    """Ohranjena za nazaj-kompatibilnost — kliče generični parser."""
    return extract_generic_pdf(content)

# --- DOKUMENTI ---
class DokumentPostavka(BaseModel):
    opis: str
    kolicina: float
    cena_enote: float
    stopnja_ddv: float = 22
    znesek_skupaj: float
    konto: Optional[str] = None
    popust: float = 0.0

class Dokument(BaseModel):
    id: Optional[int] = None
    poslovno_leto: int
    tip: str
    stevilka: Optional[str] = ""
    partner_id: int
    datum_izdaje: str
    datum_zapadlosti: str
    znesek_brez_ddv: float
    znesek_ddv: float
    znesek_skupaj: float
    datum_storitve_od: Optional[str] = ""
    datum_storitve_do: Optional[str] = ""
    status: Optional[str] = "neplačano"
    datum_placila: Optional[str] = ""
    nacin_placila: Optional[str] = ""
    zakljucno_besedilo: Optional[str] = ""
    noga_dokumenta: Optional[str] = ""
    opombe: Optional[str] = ""
    valuta: Optional[str] = "EUR"
    tecaj: Optional[float] = 1.0
    znesek_v_valuti: Optional[float] = 0.0
    vkljuci_placilo: Optional[bool] = True
    odstotek_placila: Optional[float] = 100.0
    postavke: List[DokumentPostavka]

@app.delete("/api/dokumenti/{id}")
def delete_dokument(id: int):
    try:
        conn = database.get_db()
        cursor = conn.cursor()
        # Izbriši postavke
        cursor.execute("DELETE FROM dokumenti_postavke WHERE dokument_id = ?", (id,))
        # Izbriši dokument
        cursor.execute("DELETE FROM dokumenti WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        if 'conn' in locals(): conn.close()
        print(f"Napaka pri brisanju dokumenta {id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Napaka pri brisanju: {str(e)}")

@app.get("/api/dokumenti/{tip}")
def get_dokumenti(tip: str):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.*, p.naziv as partner_naziv,
        (SELECT EXISTS(SELECT 1 FROM priloge WHERE parent_type = 'dokumenti' AND parent_id = d.id)) as ima_prilogo,
        (SELECT MAX(poslano_at) FROM email_log WHERE dokument_id = d.id AND status = 'success') as zadnje_poslano,
        IFNULL((SELECT SUM(znesek) FROM placila_povezave WHERE dokument_id = d.id), 0) as placano_znesek
        FROM dokumenti d
        LEFT JOIN partnerji p ON d.partner_id = p.id
        WHERE d.tip = ? ORDER BY d.id DESC
    """, (tip,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/dokumenti/detajl/{id}")
def get_dokument_detajl(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dokumenti WHERE id = ?", (id,))
    doc = cursor.fetchone()
    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Dokument ni najden")
    
    cursor.execute("SELECT * FROM dokumenti_postavke WHERE dokument_id = ?", (id,))
    items = cursor.fetchall()
    
    cursor.execute("SELECT MAX(poslano_at) FROM email_log WHERE dokument_id = ? AND status = 'success'", (id,))
    sent_row = cursor.fetchone()
    conn.close()
    
    res = dict(doc)
    res['postavke'] = [dict(i) for i in items]
    res['zadnje_poslano'] = sent_row[0] if sent_row else None
    return res

@app.post("/api/dokumenti")
def create_dokument(doc: Dokument):
    conn = database.get_db()
    cursor = conn.cursor()
    
    # Samodejno oštevilčevanje, če številka ni podana
    stevilka = doc.stevilka
    if not stevilka or stevilka == "":
        cursor.execute("SELECT stevilka FROM dokumenti WHERE tip = ? AND poslovno_leto = ? AND stevilka LIKE '%-%'", (doc.tip, doc.poslovno_leto))
        rows = cursor.fetchall()
        
        max_num = 0
        for r in rows:
            st = r['stevilka']
            try:
                # Handle both '001-2026' and '2026-001' historically just in case
                parts = st.split('-')
                if len(parts) == 2:
                    if parts[0].isdigit() and len(parts[0]) <= 4 and int(parts[0]) != doc.poslovno_leto:
                        # Format NNN-YYYY
                        num = int(parts[0])
                    else:
                        # Format YYYY-NNN
                        num = int(parts[1])
                    if num > max_num:
                        max_num = num
            except:
                pass
                
        next_num = max_num + 1
        stevilka = f"{next_num:03d}-{doc.poslovno_leto}"
    
    cursor.execute("""
        INSERT INTO dokumenti (poslovno_leto, tip, stevilka, partner_id, datum_izdaje, datum_zapadlosti, znesek_brez_ddv, znesek_ddv, znesek_skupaj, datum_storitve_od, datum_storitve_do, status, datum_placila, nacin_placila, zakljucno_besedilo, noga_dokumenta, opombe, valuta, tecaj, znesek_v_valuti, vkljuci_placilo, odstotek_placila)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (doc.poslovno_leto, doc.tip, stevilka, doc.partner_id, doc.datum_izdaje, doc.datum_zapadlosti, doc.znesek_brez_ddv, doc.znesek_ddv, doc.znesek_skupaj, doc.datum_storitve_od, doc.datum_storitve_do, doc.status, doc.datum_placila, doc.nacin_placila, doc.zakljucno_besedilo, doc.noga_dokumenta, doc.opombe, doc.valuta, doc.tecaj, doc.znesek_v_valuti, 1 if doc.vkljuci_placilo else 0, doc.odstotek_placila))
    
    doc_id = cursor.lastrowid
    for p in doc.postavke:
        cursor.execute("""
            INSERT INTO dokumenti_postavke (dokument_id, opis, kolicina, cena_enote, stopnja_ddv, znesek_skupaj, konto, popust)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, p.opis, p.kolicina, p.cena_enote, p.stopnja_ddv, p.znesek_skupaj, p.konto, p.popust))
    
    conn.commit()
    
    # Samodejno generiranje PDF za izdane dokumente
    if doc.tip in ['izdani_racuni', 'ponudbe', 'dobropisi']:
        try:
            ustvari_in_pripni_pdf(doc_id)
        except Exception as e:
            print(f"Napaka pri generiranju PDF: {e}")

    conn.close()
    return {"status": "success", "id": doc_id, "stevilka": stevilka}

@app.put("/api/dokumenti/{id}")
def update_dokument(id: int, doc: Dokument):
    conn = database.get_db()
    cursor = conn.cursor()
    # Brisanje starih postavk
    cursor.execute("DELETE FROM dokumenti_postavke WHERE dokument_id = ?", (id,))
    
    if not doc.stevilka:
        cursor.execute("SELECT stevilka FROM dokumenti WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row: doc.stevilka = row['stevilka']
    
    # Posodobitev glave
    cursor.execute("""
        UPDATE dokumenti SET 
            poslovno_leto=?, tip=?, stevilka=?, partner_id=?, datum_izdaje=?, datum_zapadlosti=?, 
            znesek_brez_ddv=?, znesek_ddv=?, znesek_skupaj=?, datum_storitve_od=?, datum_storitve_do=?, 
            status=?, datum_placila=?, nacin_placila=?, zakljucno_besedilo=?, noga_dokumenta=?, opombe=?,
            valuta=?, tecaj=?, znesek_v_valuti=?, vkljuci_placilo=?, odstotek_placila=?
        WHERE id = ?
    """, (doc.poslovno_leto, doc.tip, doc.stevilka, doc.partner_id, doc.datum_izdaje, doc.datum_zapadlosti, 
          doc.znesek_brez_ddv, doc.znesek_ddv, doc.znesek_skupaj, doc.datum_storitve_od, doc.datum_storitve_do, 
          doc.status, doc.datum_placila, doc.nacin_placila, doc.zakljucno_besedilo, doc.noga_dokumenta, doc.opombe,
          doc.valuta, doc.tecaj, doc.znesek_v_valuti, 1 if doc.vkljuci_placilo else 0, doc.odstotek_placila, id))
    
    # Vstavljanje novih postavk
    for p in doc.postavke:
        cursor.execute("""
            INSERT INTO dokumenti_postavke (dokument_id, opis, kolicina, cena_enote, stopnja_ddv, znesek_skupaj, konto, popust)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id, p.opis, p.kolicina, p.cena_enote, p.stopnja_ddv, p.znesek_skupaj, p.konto, p.popust))
    
    conn.commit()
    
    # Samodejno generiranje PDF za izdane dokumente
    if doc.tip in ['izdani_racuni', 'ponudbe', 'dobropisi']:
        try:
            ustvari_in_pripni_pdf(id)
        except Exception as e:
            print(f"Napaka pri generiranju PDF: {e}")

    conn.close()
    return {"status": "success", "id": id}

# --- BANČNI IZPISKI ---
class IzpisekPostavka(BaseModel):
    id: Optional[int] = None
    tip_prometa: str
    partner_id: Optional[int] = None
    namen: str
    znesek: float
    koda_namena: Optional[str] = ""
    konto: Optional[str] = ""
    manualna_likvidacija: bool = False

class Izpisek(BaseModel):
    id: Optional[int] = None
    datum: str
    stevilka_izpiska: str
    zacetno_stanje: float
    koncno_stanje: float
    kontrolna_vsota: float
    postavke: List[IzpisekPostavka]

@app.get("/api/izpiski")
def get_izpiski():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ig.*, 
        (SELECT SUM(znesek) FROM izpiski_postavke WHERE izpisek_id = ig.id AND tip_prometa = 'dobro') as vsota_prilivov,
        (SELECT SUM(znesek) FROM izpiski_postavke WHERE izpisek_id = ig.id AND tip_prometa = 'breme') as vsota_odlivov,
        (SELECT EXISTS(SELECT 1 FROM priloge WHERE parent_type = 'izpiski' AND parent_id = ig.id)) as ima_prilogo
        FROM izpiski_glava ig ORDER BY datum DESC, CAST(stevilka_izpiska AS INTEGER) DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/izpiski/detajl/{id}")
def get_izpisek_detajl(id: int):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM izpiski_glava WHERE id = ?", (id,))
    glava = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.*, part.naziv as partner_naziv,
               (SELECT COUNT(*) FROM placila_povezave WHERE izpisek_postavka_id = p.id) as st_povezav
        FROM izpiski_postavke p 
        LEFT JOIN partnerji part ON p.partner_id = part.id
        WHERE p.izpisek_id = ?
    """, (id,))
    postavke = cursor.fetchall()
    conn.close()
    
    res = dict(glava)
    res['postavke'] = [dict(p) for p in postavke]
    return res

@app.delete("/api/izpiski/{id}")
def delete_izpisek(id: int):
    try:
        conn = database.get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM izpiski_postavke WHERE izpisek_id = ?", (id,))
        cursor.execute("DELETE FROM izpiski_glava WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        if 'conn' in locals(): conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/izpiski")
def create_izpisek(izpisek: Izpisek):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO izpiski_glava (datum, stevilka_izpiska, zacetno_stanje, koncno_stanje, kontrolna_vsota)
        VALUES (?, ?, ?, ?, ?)
    """, (izpisek.datum, izpisek.stevilka_izpiska, izpisek.zacetno_stanje, izpisek.koncno_stanje, izpisek.kontrolna_vsota))
    
    izp_id = cursor.lastrowid
    for p in izpisek.postavke:
        cursor.execute("""
            INSERT INTO izpiski_postavke (izpisek_id, tip_prometa, partner_id, namen, znesek, koda_namena, konto, manualna_likvidacija)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (izp_id, p.tip_prometa, p.partner_id, p.namen, p.znesek, p.koda_namena, p.konto, 1 if p.manualna_likvidacija else 0))
    
    conn.commit()
    conn.close()
    return {"status": "success", "id": izp_id}

@app.put("/api/izpiski/{id}")
def update_izpisek(id: int, izpisek: Izpisek):
    conn = database.get_db()
    cursor = conn.cursor()
    
    # 1. Pridobimo trenutne ID-je postavk v bazi
    cursor.execute("SELECT id FROM izpiski_postavke WHERE izpisek_id = ?", (id,))
    existing_ids = {row['id'] for row in cursor.fetchall()}
    
    # 2. Seznam ID-jev, ki jih želimo ohraniti (so v payloadu)
    new_items_with_ids = [p for p in izpisek.postavke if p.id is not None]
    new_ids = {p.id for p in new_items_with_ids}
    
    # 3. Izbrišemo tiste, ki jih ni več v payloadu
    to_delete = existing_ids - new_ids
    for del_id in to_delete:
        cursor.execute("DELETE FROM izpiski_postavke WHERE id = ?", (del_id,))
        # Opcijsko: izbrišemo tudi likvidacije za te postavke
        cursor.execute("DELETE FROM placila_povezave WHERE izpisek_postavka_id = ?", (del_id,))

    # 4. Posodobimo glavo
    cursor.execute("""
        UPDATE izpiski_glava SET datum=?, stevilka_izpiska=?, zacetno_stanje=?, koncno_stanje=?, kontrolna_vsota=?
        WHERE id = ?
    """, (izpisek.datum, izpisek.stevilka_izpiska, izpisek.zacetno_stanje, izpisek.koncno_stanje, izpisek.kontrolna_vsota, id))
    
    # 5. Posodobimo obstoječe ali vstavimo nove postavke
    result_ids = []
    for p in izpisek.postavke:
        if p.id and p.id in existing_ids:
            # UPDATE
            cursor.execute("""
                UPDATE izpiski_postavke SET 
                    tip_prometa=?, partner_id=?, namen=?, znesek=?, koda_namena=?, konto=?, manualna_likvidacija=?
                WHERE id = ?
            """, (p.tip_prometa, p.partner_id, p.namen, p.znesek, p.koda_namena, p.konto, 1 if p.manualna_likvidacija else 0, p.id))
            result_ids.append(p.id)
        else:
            # INSERT
            cursor.execute("""
                INSERT INTO izpiski_postavke (izpisek_id, tip_prometa, partner_id, namen, znesek, koda_namena, konto, manualna_likvidacija)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (id, p.tip_prometa, p.partner_id, p.namen, p.znesek, p.koda_namena, p.konto, 1 if p.manualna_likvidacija else 0))
            result_ids.append(cursor.lastrowid)
    
    conn.commit()
    conn.close()
    return {"status": "success", "postavke_ids": result_ids}

def extract_data_from_sepa_xml(content, filename=None):
    """Parses ISO 20022 Camt.053 SEPA XML bank statement"""
    root = ET.fromstring(content)
    # Handle namespaces dynamically
    ns_match = re.match(r'\{(.*)\}', root.tag)
    ns_uri = ns_match.group(1) if ns_match else ""
    ns = {'n': ns_uri} if ns_uri else {}
    
    def q(path):
        """Helper to prefix path with namespace if present"""
        if not ns_uri: return path.replace('n:', '')
        return path
        
    def find_val(el, path):
        found = el.find(q(path), ns)
        return found.text if found is not None else ""

    data = {"transactions": []}
    
    stmt = root.find(".//" + q("n:Stmt"), ns)
    if stmt is None: return None # Not a valid statement
    
    # Statement Metadata
    stmt_id = find_val(stmt, "n:Id")
    seq_nb = find_val(stmt, "n:LglSeqNb") or find_val(stmt, "n:ElctrncSeqNb")
    
    final_num = seq_nb if seq_nb else stmt_id
    
    # Fallback to filename if ID is too long/technical or unknown
    if filename and (not final_num or len(final_num) > 10 or final_num == "UNKNOWN"):
        num_match = re.search(r'^(\d+)', filename)
        if num_match: final_num = num_match.group(1)
        
    data['statement_number'] = final_num
    
    # Balances and Date
    data['statement_date'] = ""
    for bal in stmt.findall(q("n:Bal"), ns):
        tp_node = bal.find(q("n:Tp/n:CdOrPrtry/n:Cd"), ns)
        amt = find_val(bal, "n:Amt")
        dt_val = find_val(bal, "n:Dt/n:Dt") or find_val(bal, "n:Dt/n:DtTm")
        
        if tp_node is not None and amt:
            tp = tp_node.text
            if tp == 'OPBD': data['opening_balance'] = float(amt)
            if tp == 'CLBD': 
                data['closing_balance'] = float(amt)
                if dt_val: data['statement_date'] = dt_val[:10]

    if not data['statement_date']:
        cre_date = find_val(stmt, "n:CreDtTm")
        data['statement_date'] = cre_date[:10] if cre_date else ""
            
    # Transactions (Entries)
    for ntry in stmt.findall(q("n:Ntry"), ns):
        val = find_val(ntry, "n:Amt")
        if not val: continue
        amt = float(val)
        if amt == 0: continue
        
        ind = find_val(ntry, "n:CdtDbtInd")
        tp = "dobro" if ind == "CRDT" else "breme"
        
        # Details
        tx_dtls = ntry.find(q("n:NtryDtls/n:TxDtls"), ns)
        desc = ""
        partner = "Neznan"
        
        if tx_dtls is not None:
            desc = find_val(tx_dtls, "n:RmtInf/n:Ustrd")
            # Partner depends on direction
            rltd = tx_dtls.find(q("n:RltdPties"), ns)
            if rltd is not None:
                p_path = q("n:Dbtr/n:Nm") if ind == "CRDT" else q("n:Cdtr/n:Nm")
                p_node = rltd.find(p_path, ns)
                if p_node is not None: partner = p_node.text

        data['transactions'].append({
            'amount': amt,
            'type': tp,
            'code': "PMNT",
            'description': desc,
            'partner': partner,
            'raw_description': desc
        })
        
    return data

def _enrich_izpisek_data(raw_data):
    """Common logic for partner matching and enrichment for all statement formats"""
    postavke = []
    partner_names = []
    
    conn = database.get_db()
    cursor = conn.cursor()
    
    def get_core_name(name):
        if not name: return ""
        cleaned = name.upper().replace(",", " ").replace(".", " ").replace("-", " ")
        suffixes = ["D O O", "S P", "D D", "D N O", "K D", "Z O O", "V D"]
        for s in suffixes:
            cleaned = cleaned.replace(f" {s} ", " ")
            if cleaned.endswith(f" {s}"): cleaned = cleaned[:-len(s)-1]
            if cleaned.startswith(f"{s} "): cleaned = cleaned[len(s)+1:]
        parts = [p.strip() for p in cleaned.split() if len(p.strip()) > 2]
        return parts[0] if parts else cleaned.strip()

    cursor.execute("SELECT id, naziv FROM partnerji")
    all_partners = [{"id": r["id"], "naziv": r["naziv"], "core": get_core_name(r["naziv"])} for r in cursor.fetchall()]
    
    for tx in raw_data['transactions']:
        partner_id = None
        search_text = f"{(tx.get('partner') or '').upper()} {(tx.get('description') or '').upper()}"
        for p in all_partners:
            if p["core"] and p["core"] in search_text:
                partner_id = p["id"]
                break
        
        # Predlagaj konto glede na tip
        konto = "1100" # Privzeto
        if tx['type'] == 'breme':
            if "PROVIZIJA" in search_text or "NADOMESTILO" in search_text:
                konto = "4150"
        
        postavke.append({
            "tip_prometa": tx['type'],
            "partner_id": partner_id,
            "partner_naziv": tx.get('partner', 'Neznan'),
            "namen": tx.get('description', ''),
            "znesek": tx['amount'],
            "koda_namena": tx.get('code', 'PMNT'),
            "konto": konto
        })
        partner_names.append(tx.get('partner', 'Neznan'))
    
    conn.close()
    
    return {
        "datum": raw_data['statement_date'],
        "stevilka_izpiska": raw_data['statement_number'],
        "zacetno_stanje": raw_data.get('opening_balance', 0),
        "koncno_stanje": raw_data.get('closing_balance', 0),
        "kontrolna_vsota": sum([p['znesek'] * (1 if p['tip_prometa']=='dobro' else -1) for p in postavke]),
        "vsota_prilivov": sum([p['znesek'] for p in postavke if p['tip_prometa']=='dobro']),
        "vsota_odlivov": sum([p['znesek'] for p in postavke if p['tip_prometa']=='breme']),
        "postavke": postavke,
        "partner_names": partner_names
    }

@app.post("/api/izpiski/parse")
async def parse_izpisek(file: UploadFile = File(...)):
    try:
        content = await file.read()
        results = []
        
        files_to_process = []
        if file.filename.lower().endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    if name.lower().endswith(('.pdf', '.xml')):
                        files_to_process.append((name, z.read(name)))
        else:
            files_to_process.append((file.filename, content))
            
        for name, data in files_to_process:
            try:
                raw_data = None
                if name.lower().endswith('.pdf'):
                    # Temporary PDF file for parser
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(data)
                        tmp_path = tmp.name
                    raw_data = extract_data_from_pdf(tmp_path)
                    os.unlink(tmp_path)
                elif name.lower().endswith('.xml'):
                    raw_data = extract_data_from_sepa_xml(data, name)
                
                if raw_data:
                    # Shranimo v uploads za kasnejšo priponko
                    ext = name.split('.')[-1]
                    disk_filename = f"izpisek_{uuid.uuid4().hex}.{ext}"
                    with open(UPLOADS_DIR / disk_filename, "wb") as f:
                        f.write(data)
                        
                    enriched = _enrich_izpisek_data(raw_data)
                    # Add source filename for UI
                    enriched['source_file'] = name
                    enriched['disk_file'] = disk_filename
                    results.append(enriched)
            except Exception as e:
                print(f"Napaka pri obdelavi {name}: {e}")
                # Ne prekinemo celotnega uvoza, če ena datoteka spodleti

        return {"items": results, "count": len(results)}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def _parse_single_pdf_content(content):
    # Ta funkcija se zdaj ne uporablja več direktno v parse_izpisek, 
    # ampak jo pustimo za nazaj ali spremenimo v klic refaktorirane logike če je treba.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        raw_data = extract_data_from_pdf(tmp_path)
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

@app.post("/api/izpiski/bulk_potrdi")
async def izpiski_bulk_potrdi(request_data: dict):
    items = request_data.get("items", [])
    results = []
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        for data in items:
            cursor.execute("""
                INSERT INTO izpiski_glava (datum, stevilka_izpiska, zacetno_stanje, koncno_stanje, kontrolna_vsota)
                VALUES (?, ?, ?, ?, ?)
            """, (data['datum'], data['stevilka_izpiska'], data['zacetno_stanje'], data['koncno_stanje'], 0))
            
            izp_id = cursor.lastrowid
            
            # Če imamo shranjeno datoteko, jo dodamo kot prilogo
            if data.get('disk_file'):
                cursor.execute("""
                    INSERT INTO priloge (parent_type, parent_id, filename, original_name)
                    VALUES (?, ?, ?, ?)
                """, ('izpiski', izp_id, data['disk_file'], data['source_file']))

            for p in data['postavke']:
                cursor.execute("""
                    INSERT INTO izpiski_postavke (izpisek_id, tip_prometa, partner_id, namen, znesek, koda_namena, konto)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (izp_id, p['tip_prometa'], p['partner_id'], p['namen'], p['znesek'], p['koda_namena'], p['konto']))
            results.append(izp_id)
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Bulk save error: {str(e)}")
    finally:
        conn.close()
    return {"status": "success", "count": len(results), "ids": results}

# --- PLAČE IN PRISPEVKI ---
class Placa(BaseModel):
    id: Optional[int] = None
    zaposleni_id: int
    mesec: str
    leto: int
    vrsta_zaposlitve: str # sp_100 (100%), sp_50 (50%), zaposlen
    bruto_placa: float
    neto_izplacilo: float = 0.0
    znesek_piz: float = 0.0
    znesek_zz: float = 0.0
    znesek_zap: float = 0.0
    znesek_starsevsko: float = 0.0
    znesek_ozp: float = 35.0
    znesek_do: float = 0.0
    znesek_akontacija_doh: float = 0.0
    znesek_skupaj: float = 0.0
    sklic: Optional[str] = ""
    zapadlost: Optional[str] = ""
    placan: bool = False

@app.get("/api/place")
def get_place():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.*, z.ime_priimek as zaposleni_ime 
        FROM place p 
        LEFT JOIN zaposleni z ON p.zaposleni_id = z.id
        ORDER BY p.leto DESC, 
        CASE p.mesec 
            WHEN 'Januar' THEN 1 WHEN 'Februar' THEN 2 WHEN 'Marec' THEN 3 WHEN 'April' THEN 4 
            WHEN 'Maj' THEN 5 WHEN 'Junij' THEN 6 WHEN 'Julij' THEN 7 WHEN 'Avgust' THEN 8 
            WHEN 'September' THEN 9 WHEN 'Oktober' THEN 10 WHEN 'November' THEN 11 WHEN 'December' THEN 12
            ELSE 0 
        END DESC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/place")
def create_placa(p: Placa):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO place (zaposleni_id, mesec, leto, vrsta_zaposlitve, bruto_placa, neto_izplacilo, 
        znesek_piz, znesek_zz, znesek_zap, znesek_starsevsko, znesek_ozp, znesek_do, znesek_akontacija_doh, 
        znesek_skupaj, sklic, zapadlost, placan)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (p.zaposleni_id, p.mesec, p.leto, p.vrsta_zaposlitve, p.bruto_placa, p.neto_izplacilo, 
          p.znesek_piz, p.znesek_zz, p.znesek_zap, p.znesek_starsevsko, p.znesek_ozp, p.znesek_do, p.znesek_akontacija_doh, 
          p.znesek_skupaj, p.sklic, p.zapadlost, p.placan))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/place/{id}")
def update_placa(id: int, p: Placa):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE place SET zaposleni_id=?, mesec=?, leto=?, vrsta_zaposlitve=?, bruto_placa=?, neto_izplacilo=?, 
        znesek_piz=?, znesek_zz=?, znesek_zap=?, znesek_starsevsko=?, znesek_ozp=?, znesek_do=?, znesek_akontacija_doh=?, 
        znesek_skupaj=?, sklic=?, zapadlost=?, placan=?
        WHERE id=?
    """, (p.zaposleni_id, p.mesec, p.leto, p.vrsta_zaposlitve, p.bruto_placa, p.neto_izplacilo, 
          p.znesek_piz, p.znesek_zz, p.znesek_zap, p.znesek_starsevsko, p.znesek_ozp, p.znesek_do, p.znesek_akontacija_doh, 
          p.znesek_skupaj, p.sklic, p.zapadlost, p.placan, id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/place/{id}")
def delete_placa(id: int):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("DELETE FROM place WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

# --- OSNOVNA SREDSTVA ---
class OsnovnoSredstvo(BaseModel):
    id: Optional[int] = None
    naziv: str
    aktiven: Optional[bool] = True
    amortizacijska_skupina: Optional[str] = ""
    inventarna_stevilka: str
    datum_nabave: str
    nabavna_vrednost: float
    stopnja_amortizacije: float
    trenutna_vrednost: Optional[float] = 0.0

@app.get("/api/osnovna_sredstva/next_inv")
def next_inv():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT inventarna_stevilka FROM osnovna_sredstva ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row and row['inventarna_stevilka']:
        try:
            num = int(row['inventarna_stevilka'])
            return {"stevilka": f"{num+1:03d}"}
        except ValueError:
            pass
    return {"stevilka": "001"}

@app.get("/api/osnovna_sredstva")
def get_osnovna_sredstva():
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM osnovna_sredstva")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/osnovna_sredstva")
def create_osnovno_sredstvo(os_obj: OsnovnoSredstvo):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO osnovna_sredstva (naziv, aktiven, amortizacijska_skupina, inventarna_stevilka, datum_nabave, nabavna_vrednost, stopnja_amortizacije, trenutna_vrednost)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (os_obj.naziv, os_obj.aktiven, os_obj.amortizacijska_skupina, os_obj.inventarna_stevilka, os_obj.datum_nabave, os_obj.nabavna_vrednost, os_obj.stopnja_amortizacije, os_obj.trenutna_vrednost))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/osnovna_sredstva/{id}")
def update_osnovno_sredstvo(id: int, os_obj: OsnovnoSredstvo):
    conn = database.get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE osnovna_sredstva SET naziv=?, aktiven=?, amortizacijska_skupina=?, inventarna_stevilka=?, datum_nabave=?, nabavna_vrednost=?, stopnja_amortizacije=?, trenutna_vrednost=?
        WHERE id = ?
    """, (os_obj.naziv, os_obj.aktiven, os_obj.amortizacijska_skupina, os_obj.inventarna_stevilka, os_obj.datum_nabave, os_obj.nabavna_vrednost, os_obj.stopnja_amortizacije, os_obj.trenutna_vrednost, id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/osnovna_sredstva/{id}")
def delete_osnovno_sredstvo(id: int):
    try:
        conn = database.get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM osnovna_sredstva WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        if 'conn' in locals(): conn.close()
        raise HTTPException(status_code=500, detail=str(e))
# --- ZAPOSLENI ---
class Zaposleni(BaseModel):
    id: Optional[int] = None
    ime_priimek: str
    naslov: Optional[str] = None
    davcna_stevilka: Optional[str] = None
    iban: Optional[str] = None
    delovno_mesto: Optional[str] = None
    datum_rojstva: Optional[str] = None
    stevilo_otrok: Optional[int] = 0
    invalid_ali_nega: Optional[bool] = False
    delovna_doba_leta: Optional[int] = 0
    dopust_odmerjen: Optional[int] = 20
    dopust_rocni_popravek: Optional[int] = 0

@app.get("/api/zaposleni")
def get_zaposleni():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM zaposleni ORDER BY ime_priimek")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/zaposleni")
def create_zaposleni(z: Zaposleni):
    conn = database.get_db()
    c = conn.cursor()

    c.execute("""
        INSERT INTO zaposleni (
            ime_priimek, naslov, davcna_stevilka, iban, delovno_mesto, 
            datum_rojstva, stevilo_otrok, invalid_ali_nega, delovna_doba_leta, 
            dopust_odmerjen, dopust_rocni_popravek
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        z.ime_priimek, z.naslov, z.davcna_stevilka, z.iban, z.delovno_mesto,
        z.datum_rojstva, z.stevilo_otrok, z.invalid_ali_nega, z.delovna_doba_leta,
        z.dopust_odmerjen, z.dopust_rocni_popravek
    ))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/zaposleni/{id}")
def update_zaposleni(id: int, z: Zaposleni):
    conn = database.get_db()
    c = conn.cursor()

    c.execute("""
        UPDATE zaposleni SET 
            ime_priimek=?, naslov=?, davcna_stevilka=?, iban=?, delovno_mesto=?, 
            datum_rojstva=?, stevilo_otrok=?, invalid_ali_nega=?, delovna_doba_leta=?, 
            dopust_odmerjen=?, dopust_rocni_popravek=? 
        WHERE id=?
    """, (
        z.ime_priimek, z.naslov, z.davcna_stevilka, z.iban, z.delovno_mesto,
        z.datum_rojstva, z.stevilo_otrok, z.invalid_ali_nega, z.delovna_doba_leta,
        z.dopust_odmerjen, z.dopust_rocni_popravek, id
    ))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/zaposleni/{id}")
def delete_zaposleni(id: int):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("DELETE FROM zaposleni WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

# --- POTNI NALOGI ---
class PotniNalog(BaseModel):
    id: Optional[int] = None
    stevilka_naloga: str
    zaposleni_id: int
    vozilo: Optional[str] = None
    namen: Optional[str] = None
    datum_izdaje: Optional[str] = None
    datum_cas_odhoda: Optional[str] = None
    datum_cas_povratka: Optional[str] = None
    relacija_zacetek: Optional[str] = None
    relacija_cilj: Optional[str] = None
    relacija_konec: Optional[str] = None
    razdalja_km: Optional[float] = 0.0
    znesek_kilometrine: Optional[float] = 0.0
    znesek_dnevnice: Optional[float] = 0.0
    skupni_znesek: Optional[float] = 0.0

@app.get("/api/potni_nalogi")
def get_potni_nalogi():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.*, z.ime_priimek as zaposleni_ime 
        FROM potni_nalogi p 
        LEFT JOIN zaposleni z ON p.zaposleni_id = z.id
        ORDER BY p.id DESC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/potni_nalogi/next_stevilka")
def next_pn_stevilka(leto: str):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("SELECT stevilka_naloga FROM potni_nalogi WHERE stevilka_naloga LIKE ? ORDER BY id DESC LIMIT 1", (f"%{leto}",))
    row = c.fetchone()
    conn.close()
    if row and row['stevilka_naloga']:
        try:
            num = int(row['stevilka_naloga'].split('-')[0])
            return {"stevilka": f"{num+1:03d}-{leto}"}
        except:
            pass
    return {"stevilka": f"001-{leto}"}

@app.post("/api/potni_nalogi")
def create_potni_nalog(p: PotniNalog):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO potni_nalogi (stevilka_naloga, zaposleni_id, vozilo, namen, datum_izdaje, 
        datum_cas_odhoda, datum_cas_povratka, relacija_zacetek, relacija_cilj, relacija_konec, 
        razdalja_km, znesek_kilometrine, znesek_dnevnice, skupni_znesek)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (p.stevilka_naloga, p.zaposleni_id, p.vozilo, p.namen, p.datum_izdaje, p.datum_cas_odhoda, 
          p.datum_cas_povratka, p.relacija_zacetek, p.relacija_cilj, p.relacija_konec, p.razdalja_km, 
          p.znesek_kilometrine, p.znesek_dnevnice, p.skupni_znesek))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/potni_nalogi/{id}")
def update_potni_nalog(id: int, p: PotniNalog):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE potni_nalogi SET stevilka_naloga=?, zaposleni_id=?, vozilo=?, namen=?, datum_izdaje=?, 
        datum_cas_odhoda=?, datum_cas_povratka=?, relacija_zacetek=?, relacija_cilj=?, relacija_konec=?, 
        razdalja_km=?, znesek_kilometrine=?, znesek_dnevnice=?, skupni_znesek=?
        WHERE id=?
    """, (p.stevilka_naloga, p.zaposleni_id, p.vozilo, p.namen, p.datum_izdaje, p.datum_cas_odhoda, 
          p.datum_cas_povratka, p.relacija_zacetek, p.relacija_cilj, p.relacija_konec, p.razdalja_km, 
          p.znesek_kilometrine, p.znesek_dnevnice, p.skupni_znesek, id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/potni_nalogi/{id}")
def delete_potni_nalog(id: int):
    conn = database.get_db()
    c = conn.cursor()
    c.execute("DELETE FROM potni_nalogi WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/vozila")
def get_vozila():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT vozilo FROM potni_nalogi WHERE vozilo IS NOT NULL AND vozilo != ''")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

# --- TARIFE POTANJ (Scraping limitov na 1. v mesecu) ---

@app.get("/api/tarife")
def get_tarife():
    conn = database.get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tarife_potanj WHERE id = 1")
    row = c.fetchone()
    
    # Preveri, če je danes 1. v mesecu in ali smo ta mesec že preverili
    danes = datetime.now()
    if row and danes.day == 1:
        zadnje = row['zadnje_preverjanje']
        if not zadnje or not zadnje.startswith(danes.strftime("%Y-%m")):
            # Scrapanje - za primer zanesljivosti poskusimo dobiti z urlja, sicer uporabimo statične iz baze
            try:
                # Tu bi šla koda za strganje: requests.get(...)
                pass
                
                # Zapišemo, da smo preverili
                c.execute("UPDATE tarife_potanj SET zadnje_preverjanje=? WHERE id=1", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
                conn.commit()
            except Exception as e:
                print("Napaka pri avtomatskem preverjanju tarif:", e)
                
    # Osvežen row
    c.execute("SELECT * FROM tarife_potanj WHERE id = 1")
    row = c.fetchone()
    conn.close()
    
    if not row:
        return {"kilometrina": 0.43, "dnevnica_polna": 27.81, "dnevnica_polovicna": 13.88, "dnevnica_znizana": 9.69}
    return dict(row)
@app.post("/api/bulk-delete")
async def bulk_delete(request_data: dict):
    module = request_data.get("module")
    ids = request_data.get("ids", [])
    if not ids: return {"status": "success", "count": 0}
    
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        # Mapiranje tipov dokumentov na skupni modul 'dokumenti'
        if module in ['izdani_racuni', 'prejeti_racuni', 'ponudbe', 'dobropisi']:
            module = 'dokumenti'

        if module == 'partnerji':
            for id in ids:
                cursor.execute("SELECT COUNT(*) as cnt FROM dokumenti WHERE partner_id = ?", (id,))
                if cursor.fetchone()['cnt'] > 0:
                    raise HTTPException(status_code=400, detail=f"Partnerja {id} ni mogoče brisati, ker ima vezane dokumente.")
            cursor.execute(f"DELETE FROM partnerji WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'dokumenti':
            cursor.execute(f"DELETE FROM dokumenti_postavke WHERE dokument_id IN ({','.join(['?']*len(ids))})", ids)
            cursor.execute(f"DELETE FROM dokumenti WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'izpiski':
            cursor.execute(f"DELETE FROM izpiski_postavke WHERE izpisek_id IN ({','.join(['?']*len(ids))})", ids)
            cursor.execute(f"DELETE FROM izpiski_glava WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'zaposleni':
            cursor.execute(f"DELETE FROM zaposleni WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'potni_nalogi':
            cursor.execute(f"DELETE FROM potni_nalogi WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'osnovna_sredstva':
            cursor.execute(f"DELETE FROM osnovna_sredstva WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'place':
            cursor.execute(f"DELETE FROM place WHERE id IN ({','.join(['?']*len(ids))})", ids)
        elif module == 'konti':
            cursor.execute(f"DELETE FROM kontni_nacrt WHERE id IN ({','.join(['?']*len(ids))})", ids)
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
    return {"status": "success", "count": len(ids)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
