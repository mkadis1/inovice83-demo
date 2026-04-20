"""
seed_data.py - Napolni demo.db z vzorčnimi podatki za Invoice83 demo verzijo.
Zaženi enkrat po namestitvi: python seed_data.py
"""
import sqlite3
import os

DB_NAME = "demo.db"

# Pobriši staro bazo
if os.path.exists(DB_NAME):
    os.remove(DB_NAME)

conn = sqlite3.connect(DB_NAME)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# ── SCHEMA ────────────────────────────────────────────────────────────────────
c.executescript("""
CREATE TABLE IF NOT EXISTS nastavitve (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    naziv TEXT, ulica TEXT, posta_kraj TEXT, drzava TEXT DEFAULT 'Slovenija',
    davcna_stevilka TEXT, zavezanec_za_ddv BOOLEAN DEFAULT 0, trr TEXT,
    banka TEXT, email_posiljatelja TEXT, telefon TEXT, spletna_stran TEXT,
    kratko_ime TEXT, dvostavno_knjigovodstvo BOOLEAN DEFAULT 0
);
CREATE TABLE IF NOT EXISTS partnerji (
    id INTEGER PRIMARY KEY AUTOINCREMENT, naziv TEXT NOT NULL, ulica TEXT,
    postna_stevilka TEXT, kraj TEXT, drzava TEXT, davcna_stevilka TEXT,
    zavezanec_za_ddv BOOLEAN, trr TEXT, email TEXT, telefon TEXT, vrsta TEXT
);
CREATE TABLE IF NOT EXISTS dokumenti (
    id INTEGER PRIMARY KEY AUTOINCREMENT, poslovno_leto INTEGER NOT NULL,
    tip TEXT NOT NULL, stevilka TEXT NOT NULL, partner_id INTEGER,
    datum_izdaje DATE, datum_zapadlosti DATE, znesek_brez_ddv REAL DEFAULT 0,
    znesek_ddv REAL DEFAULT 0, znesek_skupaj REAL DEFAULT 0,
    status TEXT DEFAULT 'neplačano', datum_placila DATE, nacin_placila TEXT,
    datum_storitve_od DATE, datum_storitve_do DATE,
    zakljucno_besedilo TEXT, noga_dokumenta TEXT, opombe TEXT,
    FOREIGN KEY (partner_id) REFERENCES partnerji(id)
);
CREATE TABLE IF NOT EXISTS dokumenti_postavke (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dokument_id INTEGER, opis TEXT NOT NULL,
    kolicina REAL DEFAULT 1, cena_enote REAL DEFAULT 0, stopnja_ddv REAL DEFAULT 22,
    znesek_skupaj REAL, konto TEXT, popust REAL DEFAULT 0,
    FOREIGN KEY (dokument_id) REFERENCES dokumenti(id)
);
CREATE TABLE IF NOT EXISTS izpiski_glava (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datum DATE, stevilka_izpiska TEXT,
    zacetno_stanje REAL, koncno_stanje REAL, kontrolna_vsota REAL
);
CREATE TABLE IF NOT EXISTS izpiski_postavke (
    id INTEGER PRIMARY KEY AUTOINCREMENT, izpisek_id INTEGER, tip_prometa TEXT,
    partner_id INTEGER, namen TEXT, znesek REAL, koda_namena TEXT, konto TEXT,
    FOREIGN KEY (izpisek_id) REFERENCES izpiski_glava(id),
    FOREIGN KEY (partner_id) REFERENCES partnerji(id)
);
CREATE TABLE IF NOT EXISTS osnovna_sredstva (
    id INTEGER PRIMARY KEY AUTOINCREMENT, naziv TEXT NOT NULL, aktiven BOOLEAN DEFAULT 1,
    amortizacijska_skupina TEXT, inventarna_stevilka TEXT, datum_nabave DATE,
    nabavna_vrednost REAL, stopnja_amortizacije REAL, trenutna_vrednost REAL
);
CREATE TABLE IF NOT EXISTS zaposleni (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ime_priimek TEXT NOT NULL, naslov TEXT,
    davcna_stevilka TEXT, iban TEXT, delovno_mesto TEXT
);
CREATE TABLE IF NOT EXISTS potni_nalogi (
    id INTEGER PRIMARY KEY AUTOINCREMENT, stevilka_naloga TEXT NOT NULL,
    zaposleni_id INTEGER NOT NULL, vozilo TEXT, namen TEXT, datum_izdaje DATE,
    datum_cas_odhoda DATETIME, datum_cas_povratka DATETIME,
    relacija_zacetek TEXT, relacija_cilj TEXT, relacija_konec TEXT,
    razdalja_km REAL, znesek_kilometrine REAL, znesek_dnevnice REAL, skupni_znesek REAL,
    FOREIGN KEY (zaposleni_id) REFERENCES zaposleni(id)
);
CREATE TABLE IF NOT EXISTS place (
    id INTEGER PRIMARY KEY AUTOINCREMENT, zaposleni_id INTEGER, mesec TEXT,
    leto INTEGER, vrsta_zaposlitve TEXT, bruto_placa REAL, neto_izplacilo REAL,
    znesek_piz REAL, znesek_zz REAL, znesek_zap REAL, znesek_starsevsko REAL,
    znesek_ozp REAL, znesek_do REAL DEFAULT 0, znesek_akontacija_doh REAL,
    znesek_skupaj REAL, sklic TEXT, zapadlost DATE, placan BOOLEAN DEFAULT 0,
    FOREIGN KEY (zaposleni_id) REFERENCES zaposleni(id)
);
CREATE TABLE IF NOT EXISTS tarife_potanj (
    id INTEGER PRIMARY KEY CHECK (id = 1), veljavnost_od DATE,
    kilometrina REAL DEFAULT 0.43, dnevnica_polna REAL DEFAULT 27.81,
    dnevnica_polovicna REAL DEFAULT 13.88, dnevnica_znizana REAL DEFAULT 9.69,
    zadnje_preverjanje DATETIME
);
CREATE TABLE IF NOT EXISTS zakljucna_besedila (
    id INTEGER PRIMARY KEY AUTOINCREMENT, naziv TEXT NOT NULL, besedilo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kontni_nacrt (
    id INTEGER PRIMARY KEY AUTOINCREMENT, stevilka TEXT NOT NULL UNIQUE,
    naziv TEXT NOT NULL, opis TEXT
);
CREATE TABLE IF NOT EXISTS priloge (
    id INTEGER PRIMARY KEY AUTOINCREMENT, parent_type TEXT NOT NULL,
    parent_id INTEGER NOT NULL, filename TEXT NOT NULL, original_name TEXT NOT NULL,
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS placila_povezave (
    id INTEGER PRIMARY KEY AUTOINCREMENT, izpisek_postavka_id INTEGER NOT NULL,
    dokument_id INTEGER NOT NULL, znesek REAL NOT NULL,
    FOREIGN KEY (izpisek_postavka_id) REFERENCES izpiski_postavke(id),
    FOREIGN KEY (dokument_id) REFERENCES dokumenti(id)
);
""")

# ── NASTAVITVE PODJETJA ────────────────────────────────────────────────────────
c.execute("""INSERT INTO nastavitve (id, naziv, ulica, posta_kraj, drzava,
    davcna_stevilka, zavezanec_za_ddv, trr, banka, email_posiljatelja,
    telefon, spletna_stran, kratko_ime)
VALUES (1, 'Primer, tehnologije d.o.o.', 'Inovacijska ulica 15', '1000 Ljubljana',
    'Slovenija', 'SI12345678', 1,
    'SI56 6100 0001 2345 678', 'Delavska hranilnica',
    'info@primer-tech.si', '+386 1 234 5678', 'www.primer-tech.si', 'Primer Tech')""")

# ── TARIFE ─────────────────────────────────────────────────────────────────────
c.execute("""INSERT INTO tarife_potanj (id, veljavnost_od, kilometrina,
    dnevnica_polna, dnevnica_polovicna, dnevnica_znizana)
VALUES (1, '2026-01-01', 0.43, 27.81, 13.88, 9.69)""")

# ── PARTNERJI ──────────────────────────────────────────────────────────────────
partnerji = [
    ("Kupec Plus d.o.o.", "Tržaška cesta 10", "1000", "Ljubljana", "Slovenija",
     "SI87654321", 1, "SI56 6100 0009 8765 432", "info@kupecplus.si", "+386 41 111 222", "kupec"),
    ("Dobavitelj Pro d.o.o.", "Industrijska 5", "2000", "Maribor", "Slovenija",
     "SI11223344", 1, "SI56 0292 1234 5678 901", "racuni@dobaviteljpro.si", "+386 2 333 444", "dobavitelj"),
    ("Global Tech GmbH", "Hauptstraße 22", "10115", "Berlin", "Nemčija",
     "DE123456789", 1, "DE89 3704 0044 0532 0130 00", "billing@globaltech.de", "+49 30 12345678", "kupec"),
]
c.executemany("""INSERT INTO partnerji (naziv, ulica, postna_stevilka, kraj, drzava,
    davcna_stevilka, zavezanec_za_ddv, trr, email, telefon, vrsta) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", partnerji)
pid1, pid2, pid3 = 1, 2, 3

# ── IZDANI RAČUNI ──────────────────────────────────────────────────────────────
dokumenti_data = [
    # Izdani računi
    (2026,"izdani_racuni","001-2026",pid1,"2026-01-15","2026-02-15",980.0,215.6,1195.6,"plačano","2026-02-10","Bančno nakazilo","2026-01-01","2026-01-31","Hvala za zaupanje!"),
    (2026,"izdani_racuni","002-2026",pid3,"2026-02-20","2026-03-20",2400.0,0,2400.0,"neplačano",None,None,"2026-02-01","2026-02-28",""),
    (2026,"izdani_racuni","003-2026",pid1,"2026-03-10","2026-04-10",450.0,99.0,549.0,"neplačano",None,None,"2026-03-01","2026-03-31",""),
    # Prejeti računi
    (2026,"prejeti_racuni","RD-2026-001",pid2,"2026-01-20","2026-02-20",800.0,176.0,976.0,"plačano","2026-02-18","Bančno nakazilo",None,None,""),
    (2026,"prejeti_racuni","RD-2026-002",pid2,"2026-02-25","2026-03-25",1200.0,264.0,1464.0,"neplačano",None,None,None,None,""),
    (2026,"prejeti_racuni","RD-2026-003",pid3,"2026-03-15","2026-04-15",3500.0,0,3500.0,"neplačano",None,None,None,None,""),
    # Ponudbe
    (2026,"ponudbe","PON-001-2026",pid1,"2026-02-01","2026-03-01",1800.0,396.0,2196.0,"neplačano",None,None,None,None,"Ponudba velja 30 dni."),
    (2026,"ponudbe","PON-002-2026",pid3,"2026-03-05","2026-04-05",5000.0,0,5000.0,"neplačano",None,None,None,None,""),
    (2026,"ponudbe","PON-003-2026",pid2,"2026-03-20","2026-04-20",750.0,165.0,915.0,"neplačano",None,None,None,None,""),
    # Dobropisi
    (2026,"dobropisi","DOB-001-2026",pid1,"2026-02-05",None,-100.0,-22.0,-122.0,"plačano","2026-02-05","Pobot",None,None,"Dobropis za vrnitev blaga"),
    (2026,"dobropisi","DOB-002-2026",pid2,"2026-03-01",None,-200.0,-44.0,-244.0,"plačano","2026-03-01","Pobot",None,None,""),
    (2026,"dobropisi","DOB-003-2026",pid3,"2026-03-25",None,-500.0,0,-500.0,"neplačano",None,None,None,None,""),
]
for d in dokumenti_data:
    c.execute("""INSERT INTO dokumenti (poslovno_leto, tip, stevilka, partner_id,
        datum_izdaje, datum_zapadlosti, znesek_brez_ddv, znesek_ddv, znesek_skupaj,
        status, datum_placila, nacin_placila, datum_storitve_od, datum_storitve_do, zakljucno_besedilo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", d)

# ── POSTAVKE DOKUMENTOV ────────────────────────────────────────────────────────
postavke = [
    # Vydani 001
    (1,"Razvoj spletne aplikacije - januar 2026",1,800.0,22.0,980.0,"760"),
    (1,"Vzdrževanje strežnika",1,180.0,22.0,219.6,"761"),
    # Vydani 002 (tujina, 0% DDV)
    (2,"Software licensing - Q1 2026",1,2400.0,0.0,2400.0,"760"),
    # Vydani 003
    (3,"Svetovanje in tehnična podpora",1,450.0,22.0,549.0,"760"),
    # Prejeti 004
    (4,"Najem poslovnega prostora - januar",1,800.0,22.0,976.0,"470"),
    # Prejeti 005
    (5,"IT oprema - računalniki",2,600.0,22.0,732.0,"040"),
    # Prejeti 006
    (6,"Konzultantske storitve",1,3500.0,0.0,3500.0,"400"),
    # Ponudbe
    (7,"Razvoj mobilne aplikacije",1,1800.0,22.0,2196.0,"760"),
    (8,"Enterprise software licenca",1,5000.0,0.0,5000.0,"760"),
    (9,"Vzdrževalna pogodba - letna",1,750.0,22.0,915.0,"761"),
    # Dobropisi
    (10,"Vrnitev blaga",-1,100.0,22.0,-122.0,"700"),
    (11,"Korekcija cene",-1,200.0,22.0,-244.0,"400"),
    (12,"Popust - projekt",-1,500.0,0.0,-500.0,"760"),
]
c.executemany("""INSERT INTO dokumenti_postavke (dokument_id, opis, kolicina, cena_enote,
    stopnja_ddv, znesek_skupaj, konto) VALUES (?,?,?,?,?,?,?)""", postavke)

# ── BANČNI IZPISKI ─────────────────────────────────────────────────────────────
c.execute("INSERT INTO izpiski_glava (datum, stevilka_izpiska, zacetno_stanje, koncno_stanje, kontrolna_vsota) VALUES ('2026-01-31','1',5000.0,6073.6,1073.6)")
iz1 = c.lastrowid
c.execute("INSERT INTO izpiski_glava (datum, stevilka_izpiska, zacetno_stanje, koncno_stanje, kontrolna_vsota) VALUES ('2026-02-28','2',6073.6,4633.6,-1440.0)")
iz2 = c.lastrowid
c.execute("INSERT INTO izpiski_glava (datum, stevilka_izpiska, zacetno_stanje, koncno_stanje, kontrolna_vsota) VALUES ('2026-03-31','3',4633.6,5133.6,500.0)")
iz3 = c.lastrowid

izp_postavke = [
    (iz1,"dobro",pid1,"Plačilo računa 001-2026 / SKLIC: SI12 001-2026",1195.6,"PMNT","1200"),
    (iz1,"breme",None,"NLB provizija - januar 2026",8.5,"PMNT","419000"),
    (iz1,"breme",None,"Telefonski stroški - Telekom",113.5,"PMNT","420"),
    (iz2,"breme",pid2,"Plačilo obveznosti RD-2026-001",976.0,"PMNT","2200"),
    (iz2,"dobro",None,"Povračilo DDV - Furs januar 2026",464.0,"PMNT","1600"),
    (iz2,"breme",None,"NLB provizija - februar 2026",8.5,"PMNT","419000"),
    (iz3,"dobro",pid1,"Delno plačilo - račun 002",1000.0,"PMNT","1200"),
    (iz3,"breme",None,"Pisarniški material - Office Max",45.0,"PMNT","403"),
    (iz3,"breme",None,"NLB provizija - marec 2026",8.5,"PMNT","419000"),
    (iz3,"breme",pid2,"Avazno plačilo za pričakovano dostavo",450.0,"PMNT","2200"),
]
c.executemany("""INSERT INTO izpiski_postavke (izpisek_id, tip_prometa, partner_id,
    namen, znesek, koda_namena, konto) VALUES (?,?,?,?,?,?,?)""", izp_postavke)

# ── ZAPOSLENI ──────────────────────────────────────────────────────────────────
zaposleni_data = [
    ("Ana Novak", "Cankarjeva 5, 1000 Ljubljana", "12345678", "SI56 6100 0001 1111 111", "Vodja razvoja"),
    ("Marko Kovač", "Prešernova 12, 2000 Maribor", "87654321", "SI56 6100 0002 2222 222", "Razvijalec"),
    ("Petra Zupan", "Titova 20, 3000 Celje", "11223344", "SI56 6100 0003 3333 333", "Računovodja"),
]
c.executemany("INSERT INTO zaposleni (ime_priimek, naslov, davcna_stevilka, iban, delovno_mesto) VALUES (?,?,?,?,?)", zaposleni_data)

# ── PLAČE ──────────────────────────────────────────────────────────────────────
place_data = [
    (1,"januar",2026,"zaposlen",2500.0,1812.5,232.5,125.0,8.75,12.5,39.36,0.0,268.39,1812.5,"SI12 12345-87654321","2026-02-15",1),
    (2,"januar",2026,"zaposlen",2000.0,1450.0,186.0,100.0,7.0,10.0,39.36,0.0,214.71,1450.0,"SI12 12345-87654321","2026-02-15",1),
    (3,"februar",2026,"sp_100",3000.0,2400.0,279.0,150.0,10.5,15.0,39.36,0.0,0.0,2400.0,"SI12 12345-12345678","2026-03-15",0),
]
c.executemany("""INSERT INTO place (zaposleni_id, mesec, leto, vrsta_zaposlitve, bruto_placa,
    neto_izplacilo, znesek_piz, znesek_zz, znesek_zap, znesek_starsevsko,
    znesek_ozp, znesek_do, znesek_akontacija_doh, znesek_skupaj, sklic,
    zapadlost, placan) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", place_data)

# ── POTNI NALOGI ───────────────────────────────────────────────────────────────
potni_nalogi = [
    ("001-2026",1,"Renault Clio (LJ AB-123E)","Sestanek s stranko - Maribor",
     "2026-01-20","2026-01-20 08:00","2026-01-20 18:00","Ljubljana","Maribor","Ljubljana",
     280.0,120.4,0.0,120.4),
    ("002-2026",2,"Ford Focus (MB CD-456F)","Izobraževanje FastAPI - Zagreb",
     "2026-02-15","2026-02-15 07:00","2026-02-15 22:00","Maribor","Zagreb","Maribor",
     320.0,137.6,27.81,165.41),
    ("003-2026",1,"Renault Clio (LJ AB-123E)","Dostava opreme stranki - Celje",
     "2026-03-05","2026-03-05 09:00","2026-03-05 15:00","Ljubljana","Celje","Ljubljana",
     150.0,64.5,0.0,64.5),
]
c.executemany("""INSERT INTO potni_nalogi (stevilka_naloga, zaposleni_id, vozilo, namen,
    datum_izdaje, datum_cas_odhoda, datum_cas_povratka, relacija_zacetek, relacija_cilj,
    relacija_konec, razdalja_km, znesek_kilometrine, znesek_dnevnice, skupni_znesek)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", potni_nalogi)

# ── OSNOVNA SREDSTVA ───────────────────────────────────────────────────────────
os_data = [
    ("Dell Laptop XPS 15",1,"II","001","2025-03-01",1800.0,25.0,1125.0),
    ("iPhone 15 Pro - službeni telefon",1,"III","002","2025-06-15",1200.0,33.3,600.0),
    ("Pisarniški stol Herman Miller",1,"IV","003","2024-11-01",850.0,10.0,680.0),
]
c.executemany("""INSERT INTO osnovna_sredstva (naziv, aktiven, amortizacijska_skupina,
    inventarna_stevilka, datum_nabave, nabavna_vrednost, stopnja_amortizacije, trenutna_vrednost)
    VALUES (?,?,?,?,?,?,?,?)""", os_data)

# ── ZAKLJUČNA BESEDILA ─────────────────────────────────────────────────────────
c.executemany("INSERT INTO zakljucna_besedila (naziv, besedilo) VALUES (?,?)", [
    ("Standardno","Plačilo je možno na naš TRR pri Delavski hranilnici. V primeru zamude zaračunamo zakonske zamudne obresti."),
    ("Tujina","Payment due within 30 days. Bank transfer only. VAT reverse charge applies under Article 196 of VAT Directive."),
    ("Predplačilo","Prosimo za predplačilo v roku 3 dni. Storitev bo opravljena po prejemu plačila."),
])

# ── KONTNI NAČRT (osnoven) ─────────────────────────────────────────────────────
konti = [
    ("002","Kapital"),("040","Nepremičnine"),("050","Oprema"),("060","Drobni inventar"),
    ("120","Terjatve do kupcev - domači"),("121","Terjatve do kupcev - tuji"),
    ("160","Terjatve za DDV"),("165","Odbitni DDV"),
    ("200","Kratkoročne finančne obveznosti"),("220","Obveznosti do domačih dobaviteljev"),
    ("221","Obveznosti do tujih dobaviteljev"),("260","Obveznosti za DDV"),
    ("400","Stroški materiala"),("403","Pisarniški material"),
    ("419000","Bančne provizije"),("420","Telefonski stroški"),
    ("430","Stroški energije"),("470","Najemnine"),
    ("700","Prihodki od prodaje - domači"),("702","Prihodki od prodaje - tuji"),
    ("760","Prihodki od storitev - domači"),("762","Prihodki od storitev - tuji"),
    ("761","Prihodki od vzdrževanja"),
    ("470","Najemnine poslovnih prostorov"),
    ("1100","Žiro račun"),("1200","Terjatve do kupcev"),("2200","Obveznosti do dobaviteljev"),
    ("1600","Terjatve za DDV"),("2600","Obveznosti za DDV"),
]
seen = set()
for k in konti:
    if k[0] not in seen:
        try:
            c.execute("INSERT OR IGNORE INTO kontni_nacrt (stevilka, naziv) VALUES (?,?)", k)
            seen.add(k[0])
        except:
            pass

conn.commit()
conn.close()
print("OK Demo baza uspesno ustvarjena: demo.db")
print("   Podatki: 3 partnerji, 12 dokumentov, 3 izpiski, 3 zaposleni, 3 potni nalogi, 3 OS, 3 place")
