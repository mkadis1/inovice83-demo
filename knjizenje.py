import database
import sqlite3
from fastapi import HTTPException
from datetime import datetime

def _get_znesek(row, field):
    return row[field] if row[field] is not None else 0.0

def knjizi_dokument(dokument_id: int, temeljnica_id: int = None, novi_naziv: str = None):
    conn = database.get_db()
    cursor = conn.cursor()
    
    try:
        # Check if already booked
        cursor.execute("SELECT knjizeno FROM dokumenti WHERE id = ?", (dokument_id,))
        doc = cursor.fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="Dokument ne obstaja.")
        if doc['knjizeno']:
            raise HTTPException(status_code=400, detail="Dokument je že knjižen.")

        # Get document details
        cursor.execute("SELECT * FROM dokumenti WHERE id = ?", (dokument_id,))
        dokument = cursor.fetchone()
        
        tip = dokument['tip']
        stevilka = dokument['stevilka']
        partner_id = dokument['partner_id']
        datum = dokument['datum_izdaje']
        poslovno_leto = dokument['poslovno_leto']
        znesek_brez_ddv = _get_znesek(dokument, 'znesek_brez_ddv')
        znesek_ddv = _get_znesek(dokument, 'znesek_ddv')
        znesek_skupaj = _get_znesek(dokument, 'znesek_skupaj')
        
        # Determine vrsta temeljnice
        vrsta_temeljnice = 'IR' if tip == 'izdani_racuni' else 'PR' if tip == 'prejeti_racuni' else None
        if not vrsta_temeljnice:
            raise HTTPException(status_code=400, detail=f"Tip dokumenta '{tip}' ni podprt za avtomatsko knjiženje.")

        # If no temeljnica provided or -1 (new), create one
        if not temeljnica_id or temeljnica_id == -1:
            # Generate sequential temeljnica number (e.g., 00001-2026)
            cursor.execute("SELECT COUNT(*) as n FROM temeljnice WHERE poslovno_leto = ?", (poslovno_leto,))
            seq = (cursor.fetchone()['n'] or 0) + 1
            tem_stevilka = f"{seq:05d}-{poslovno_leto}"
            
            # If user provided a name, use it as the "opis" (description)
            if novi_naziv:
                opis = novi_naziv
            else:
                opis = f"Avtomatsko knjiženje {stevilka}"

            cursor.execute("""
                INSERT INTO temeljnice (poslovno_leto, vrsta, stevilka, datum, opis, zaklenjeno)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (poslovno_leto, vrsta_temeljnice, tem_stevilka, datum, opis))
            temeljnica_id = cursor.lastrowid
        else:
            # Check if provided temeljnica exists
            cursor.execute("SELECT id FROM temeljnice WHERE id = ?", (temeljnica_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Izbrana temeljnica ne obstaja.")

        # Create postavke
        if tip == 'izdani_racuni':
            # V breme: 120 (Terjatve do kupcev)
            cursor.execute("""
                INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                VALUES (?, '120', ?, ?, ?, 0, ?, 'dokumenti')
            """, (temeljnica_id, partner_id, f"Terjatev {stevilka}", znesek_skupaj, dokument_id))
            
            # V dobro: 760 (Prihodki) in 260 (DDV)
            if znesek_brez_ddv > 0:
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '760', ?, ?, 0, ?, ?, 'dokumenti')
                """, (temeljnica_id, partner_id, f"Prihodek {stevilka}", znesek_brez_ddv, dokument_id))
            if znesek_ddv > 0:
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '260', ?, ?, 0, ?, ?, 'dokumenti')
                """, (temeljnica_id, partner_id, f"DDV {stevilka}", znesek_ddv, dokument_id))

        elif tip == 'prejeti_racuni':
            # V dobro: 220 (Obveznosti do dobaviteljev)
            cursor.execute("""
                INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                VALUES (?, '220', ?, ?, 0, ?, ?, 'dokumenti')
            """, (temeljnica_id, partner_id, f"Obveznost {stevilka}", znesek_skupaj, dokument_id))
            
            # V breme: 410 (Stroški materiala/storitev - default) in 160 (Vstopni DDV)
            if znesek_brez_ddv > 0:
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '410', ?, ?, ?, 0, ?, 'dokumenti')
                """, (temeljnica_id, partner_id, f"Strošek {stevilka}", znesek_brez_ddv, dokument_id))
            if znesek_ddv > 0:
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '160', ?, ?, ?, 0, ?, 'dokumenti')
                """, (temeljnica_id, partner_id, f"Vstopni DDV {stevilka}", znesek_ddv, dokument_id))

        # Update dokument status
        cursor.execute("UPDATE dokumenti SET knjizeno = 1 WHERE id = ?", (dokument_id,))
        
        conn.commit()
        return {"status": "success", "message": "Dokument uspešno knjižen.", "temeljnica_id": temeljnica_id}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def _ustvari_temeljnico_header(cursor, poslovno_leto, vrsta, stevilka_vira, novi_naziv, fallback_prefix):
    # Generate sequential temeljnica number (e.g., 00001-2026)
    cursor.execute("SELECT COUNT(*) as n FROM temeljnice WHERE poslovno_leto = ?", (poslovno_leto,))
    seq = (cursor.fetchone()['n'] or 0) + 1
    tem_stevilka = f"{seq:05d}-{poslovno_leto}"
    
    if novi_naziv:
        opis = novi_naziv
    else:
        opis = f"{fallback_prefix} {stevilka_vira}"
    
    datum = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO temeljnice (poslovno_leto, vrsta, stevilka, datum, opis, zaklenjeno)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (poslovno_leto, vrsta, tem_stevilka, datum, opis))
    return cursor.lastrowid

def knjizi_izpisek(izpisek_id: int, temeljnica_id: int = None, novi_naziv: str = None):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM izpiski_glava WHERE id = ?", (izpisek_id,))
        glava = cursor.fetchone()
        if not glava or glava['knjizeno']: return {"status": "error", "message": "Izpisek že knjižen ali ne obstaja."}
        
        cursor.execute("SELECT * FROM izpiski_postavke WHERE izpisek_id = ?", (izpisek_id,))
        postavke = cursor.fetchall()
        
        leto = int(glava['datum'][:4])
        if not temeljnica_id or temeljnica_id == -1:
            temeljnica_id = _ustvari_temeljnico_header(cursor, leto, 'BA', glava['stevilka_izpiska'], novi_naziv, "Bančni izpisek")
            
        for p in postavke:
            znesek = p['znesek']
            partner_id = p['partner_id']
            namen = p['namen']
            konto_counter = p['konto'] if p['konto'] else ('120' if p['tip_prometa'] == 'dobro' else '220')
            
            if p['tip_prometa'] == 'dobro': # Priliv
                # V breme: 110 (Banka)
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '110', ?, ?, ?, 0, ?, 'izpiski')
                """, (temeljnica_id, partner_id, namen, znesek, izpisek_id))
                # V dobro: Kontra konto
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, ?, ?, ?, 0, ?, ?, 'izpiski')
                """, (temeljnica_id, konto_counter, partner_id, namen, znesek, izpisek_id))
            else: # Odliv
                # V breme: Kontra konto
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, ?, ?, ?, ?, 0, ?, 'izpiski')
                """, (temeljnica_id, konto_counter, partner_id, namen, znesek, izpisek_id))
                # V dobro: 110 (Banka)
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, partner_id, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '110', ?, ?, 0, ?, ?, 'izpiski')
                """, (temeljnica_id, partner_id, namen, znesek, izpisek_id))
                
        cursor.execute("UPDATE izpiski_glava SET knjizeno = 1 WHERE id = ?", (izpisek_id,))
        conn.commit()
        return {"status": "success", "temeljnica_id": temeljnica_id}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def knjizi_potni_nalog(nalog_id: int, temeljnica_id: int = None, novi_naziv: str = None):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM potni_nalogi WHERE id = ?", (nalog_id,))
        pn = cursor.fetchone()
        if not pn or pn['knjizeno']: return {"status": "error", "message": "Potni nalog že knjižen ali ne obstaja."}
        
        leto = int(pn['datum_izdaje'][:4])
        znesek = pn['skupni_znesek']
        stevilka = pn['stevilka_naloga']
        
        if not temeljnica_id or temeljnica_id == -1:
            temeljnica_id = _ustvari_temeljnico_header(cursor, leto, 'PN', stevilka, novi_naziv, "Potni nalog")
            
        # V breme: 415 (Stroski potovanj)
        cursor.execute("""
            INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
            VALUES (?, '415', ?, ?, 0, ?, 'potni_nalogi')
        """, (temeljnica_id, f"Strošek PN {stevilka}", znesek, nalog_id))
        # V dobro: 255 (Obveznosti do zaposlenih)
        cursor.execute("""
            INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
            VALUES (?, '255', ?, 0, ?, ?, 'potni_nalogi')
        """, (temeljnica_id, f"Obveznost PN {stevilka}", znesek, nalog_id))
        
        cursor.execute("UPDATE potni_nalogi SET knjizeno = 1 WHERE id = ?", (nalog_id,))
        conn.commit()
        return {"status": "success", "temeljnica_id": temeljnica_id}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def knjizi_amortizacija(leto: int, temeljnica_id: int = None, novi_naziv: str = None):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM osnovna_sredstva WHERE aktiven = 1")
        sredstva = cursor.fetchall()
        
        if not temeljnica_id or temeljnica_id == -1:
            temeljnica_id = _ustvari_temeljnico_header(cursor, leto, 'AM', str(leto), novi_naziv, "Amortizacija")
            
        for s in sredstva:
            nabavna = s['nabavna_vrednost']
            stopnja = s['stopnja_amortizacije']
            znesek_am = round(nabavna * (stopnja / 100.0), 2)
            
            if znesek_am > 0:
                # V breme: 430 (Amortizacija)
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '430', ?, ?, 0, ?, 'amortizacija')
                """, (temeljnica_id, f"Amortizacija {s['naziv']}", znesek_am, leto))
                # V dobro: 050 (Popravek vrednosti)
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '050', ?, 0, ?, ?, 'amortizacija')
                """, (temeljnica_id, f"Amortizacija {s['naziv']}", znesek_am, leto))
                
        conn.commit()
        return {"status": "success", "temeljnica_id": temeljnica_id}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def knjizi_placa(placa_id: int, temeljnica_id: int = None, novi_naziv: str = None):
    conn = database.get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM place WHERE id = ?", (placa_id,))
        p = cursor.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Obračun plače ne obstaja.")
        if p['knjizeno']:
            return {"status": "error", "message": "Obračun že knjižen."}
        
        leto = p['leto']
        mesec = p['mesec']
        vrsta = p['vrsta_zaposlitve']
        stevilka = f"{mesec}/{leto}"
        
        if not temeljnica_id or temeljnica_id == -1:
            temeljnica_id = _ustvari_temeljnico_header(cursor, leto, 'PLA', stevilka, novi_naziv, "Obračun plač")
            
        if vrsta in ['sp_100', 'sp_50']:
            # S.P. - Samo prispevki
            znesek = p['znesek_skupaj']
            # V breme: 485 (Prispevki nosilca s.p.)
            cursor.execute("""
                INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                VALUES (?, '485', ?, ?, 0, ?, 'place')
            """, (temeljnica_id, f"Prispevki s.p. {stevilka}", znesek, placa_id))
            # V dobro: 254 (Obveznosti za prispevke s.p.)
            cursor.execute("""
                INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                VALUES (?, '254', ?, 0, ?, ?, 'place')
            """, (temeljnica_id, f"Obveznost za prispevke {stevilka}", znesek, placa_id))
        else:
            # Klasična zaposlitev
            bruto = p['bruto_placa']
            dohodnina = p['znesek_akontacija_doh']
            # Prispevki (vsi prispevki skupaj)
            prispevki_skupaj = (p['znesek_piz'] or 0) + (p['znesek_zz'] or 0) + (p['znesek_zap'] or 0) + (p['znesek_starsevsko'] or 0) + (p['znesek_ozp'] or 0) + (p['znesek_do'] or 0)
            
            # Neto = Bruto - (vsi prispevki) - dohodnina
            neto = bruto - prispevki_skupaj - dohodnina
            
            # V breme: 470 (Stroški plač)
            cursor.execute("""
                INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                VALUES (?, '470', ?, ?, 0, ?, 'place')
            """, (temeljnica_id, f"Bruto plača {stevilka}", bruto, placa_id))
            
            # V dobro: 250 (Obveznosti za neto plače)
            cursor.execute("""
                INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                VALUES (?, '250', ?, 0, ?, ?, 'place')
            """, (temeljnica_id, f"Neto plača {stevilka}", neto, placa_id))
            
            # V dobro: 251 (Obveznosti za prispevke iz plač)
            if prispevki_skupaj > 0:
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '251', ?, 0, ?, ?, 'place')
                """, (temeljnica_id, f"Prispevki {stevilka}", prispevki_skupaj, placa_id))
                
            # V dobro: 252 (Obveznosti za dohodnino)
            if dohodnina > 0:
                cursor.execute("""
                    INSERT INTO temeljnice_postavke (temeljnica_id, konto, opis, znesek_v_breme, znesek_v_dobro, dokument_id, dokument_tip)
                    VALUES (?, '252', ?, 0, ?, ?, 'place')
                """, (temeljnica_id, f"Akontacija dohodnine {stevilka}", dohodnina, placa_id))
        
        cursor.execute("UPDATE place SET knjizeno = 1 WHERE id = ?", (placa_id,))
        conn.commit()
        return {"status": "success", "temeljnica_id": temeljnica_id}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def razknjizi_vse(id: int, tip: str, update_table: str):
    conn = database.get_db()
    cursor = conn.cursor()
    
    try:
        # Poišči vse temeljnice, na katerih se nahaja ta dokument
        cursor.execute("SELECT DISTINCT temeljnica_id FROM temeljnice_postavke WHERE dokument_id = ? AND dokument_tip = ?", (id, tip))
        rows = cursor.fetchall()
        t_ids = [r['temeljnica_id'] for r in rows]
        
        # Izbriši postavke
        cursor.execute("DELETE FROM temeljnice_postavke WHERE dokument_id = ? AND dokument_tip = ?", (id, tip))
        
        # Očisti prazne temeljnice
        for tid in t_ids:
            cursor.execute("SELECT COUNT(id) as c FROM temeljnice_postavke WHERE temeljnica_id = ?", (tid,))
            if cursor.fetchone()['c'] == 0:
                cursor.execute("DELETE FROM temeljnice WHERE id = ?", (tid,))
            
        # Update status v izvorni tabeli
        if update_table:
            cursor.execute(f"UPDATE {update_table} SET knjizeno = 0 WHERE id = ?", (id,))
        
        conn.commit()
        return {"status": "success", "message": "Uspešno razknjiženo."}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def razknjizi_dokument(dokument_id: int):
    return razknjizi_vse(dokument_id, 'dokumenti', 'dokumenti')

def razknjizi_izpisek(izpisek_id: int):
    return razknjizi_vse(izpisek_id, 'izpiski', 'izpiski_glava')

def razknjizi_potni_nalog(nalog_id: int):
    return razknjizi_vse(nalog_id, 'potni_nalogi', 'potni_nalogi')

def razknjizi_amortizacija(leto: int):
    # Amortizacija je specificna, ker ni nujno 1:1 z IDjem, ampak z letom
    return razknjizi_vse(leto, 'amortizacija', None)

def razknjizi_placa(placa_id: int):
    return razknjizi_vse(placa_id, 'place', 'place')

