import sqlite3
import os

DB_NAME = "racunovodstvo.db"

def set_active_db(name):
    global DB_NAME
    DB_NAME = name

def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Tabela: Poslovni partnerji
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS partnerji (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        naziv TEXT NOT NULL,
        ulica TEXT,
        postna_stevilka TEXT,
        kraj TEXT,
        drzava TEXT,
        davcna_stevilka TEXT,
        zavezanec_za_ddv BOOLEAN,
        trr TEXT,
        email TEXT,
        telefon TEXT,
        vrsta TEXT
    );

    -- Enotna tabela za dokumente
    CREATE TABLE IF NOT EXISTS dokumenti (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poslovno_leto INTEGER NOT NULL,
        tip TEXT NOT NULL, -- 'izdani_racuni', 'prejeti_racuni', 'ponudbe', 'dobropisi'
        stevilka TEXT NOT NULL,
        partner_id INTEGER,
        datum_izdaje DATE,
        datum_zapadlosti DATE,
        znesek_brez_ddv REAL DEFAULT 0,
        znesek_ddv REAL DEFAULT 0,
        znesek_skupaj REAL DEFAULT 0,
        status TEXT DEFAULT 'neplačano',
        datum_placila DATE,
        nacin_placila TEXT,
        datum_storitve_od DATE,
        datum_storitve_do DATE,
        zakljucno_besedilo TEXT,
        noga_dokumenta TEXT,
        opombe TEXT,
        FOREIGN KEY (partner_id) REFERENCES partnerji(id)
    );

    CREATE TABLE IF NOT EXISTS dokumenti_postavke (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dokument_id INTEGER,
        opis TEXT NOT NULL,
        kolicina REAL DEFAULT 1,
        cena_enote REAL DEFAULT 0,
        stopnja_ddv REAL DEFAULT 22,
        znesek_skupaj REAL,
        konto TEXT,
        popust REAL DEFAULT 0,
        FOREIGN KEY (dokument_id) REFERENCES dokumenti(id)
    );

    -- Bančni izpiski - Glava
    CREATE TABLE IF NOT EXISTS izpiski_glava (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        datum DATE,
        stevilka_izpiska TEXT,
        zacetno_stanje REAL,
        koncno_stanje REAL,
        kontrolna_vsota REAL
    );

    -- Bančni izpiski - Postavke
    CREATE TABLE IF NOT EXISTS izpiski_postavke (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        izpisek_id INTEGER,
        tip_prometa TEXT, -- 'dobro', 'breme'
        partner_id INTEGER,
        namen TEXT,
        znesek REAL,
        koda_namena TEXT,
        konto TEXT,
        manualna_likvidacija BOOLEAN DEFAULT 0,
        FOREIGN KEY (izpisek_id) REFERENCES izpiski_glava(id),
        FOREIGN KEY (partner_id) REFERENCES partnerji(id)
    );

    -- Osnovna sredstva
    CREATE TABLE IF NOT EXISTS osnovna_sredstva (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        naziv TEXT NOT NULL,
        aktiven BOOLEAN DEFAULT 1,
        amortizacijska_skupina TEXT,
        inventarna_stevilka TEXT,
        datum_nabave DATE,
        nabavna_vrednost REAL,
        stopnja_amortizacije REAL,
        trenutna_vrednost REAL
    );

    -- Zaposleni (za potne naloge in ostalo)
    CREATE TABLE IF NOT EXISTS zaposleni (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ime_priimek TEXT NOT NULL,
        naslov TEXT,
        davcna_stevilka TEXT,
        iban TEXT,
        delovno_mesto TEXT,
        datum_rojstva DATE,
        stevilo_otrok INTEGER DEFAULT 0,
        invalid_ali_nega BOOLEAN DEFAULT 0,
        delovna_doba_leta INTEGER DEFAULT 0,
        dopust_odmerjen INTEGER DEFAULT 20,
        dopust_rocni_popravek INTEGER DEFAULT 0
    );

    -- Potni nalogi
    CREATE TABLE IF NOT EXISTS potni_nalogi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stevilka_naloga TEXT NOT NULL,
        zaposleni_id INTEGER NOT NULL,
        vozilo TEXT,
        namen TEXT,
        datum_izdaje DATE,
        datum_cas_odhoda DATETIME,
        datum_cas_povratka DATETIME,
        relacija_zacetek TEXT,
        relacija_cilj TEXT,
        relacija_konec TEXT,
        razdalja_km REAL,
        znesek_kilometrine REAL,
        znesek_dnevnice REAL,
        skupni_znesek REAL,
        FOREIGN KEY (zaposleni_id) REFERENCES zaposleni(id)
    );

    -- Tarife za potne naloge
    CREATE TABLE IF NOT EXISTS tarife_potanj (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        veljavnost_od DATE,
        kilometrina REAL DEFAULT 0.43,
        dnevnica_polna REAL DEFAULT 27.81,
        dnevnica_polovicna REAL DEFAULT 13.88,
        dnevnica_znizana REAL DEFAULT 9.69,
        zadnje_preverjanje DATETIME
    );

    -- Plače in prispevki
    CREATE TABLE IF NOT EXISTS place (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zaposleni_id INTEGER,
        mesec TEXT,
        leto INTEGER,
        vrsta_zaposlitve TEXT,
        bruto_placa REAL,
        neto_izplacilo REAL,
        znesek_piz REAL,
        znesek_zz REAL,
        znesek_zap REAL,
        znesek_starsevsko REAL,
        znesek_ozp REAL,
        znesek_do REAL DEFAULT 0,
        znesek_akontacija_doh REAL,
        znesek_skupaj REAL,
        sklic TEXT,
        zapadlost DATE,
        placan BOOLEAN DEFAULT 0,
        FOREIGN KEY (zaposleni_id) REFERENCES zaposleni(id)
    );

    -- Nastavitve podjetja
    CREATE TABLE IF NOT EXISTS nastavitve (
        id INTEGER PRIMARY KEY CHECK (id = 1), -- Samo ena vrstica nastavitev
        naziv TEXT,
        ulica TEXT,
        posta_kraj TEXT,
        drzava TEXT DEFAULT 'Slovenija',
        davcna_stevilka TEXT,
        zavezanec_za_ddv BOOLEAN DEFAULT 0,
        trr TEXT,
        banka TEXT,
        email_posiljatelja TEXT DEFAULT 'sim@83.si',
        telefon TEXT,
        spletna_stran TEXT,
        kratko_ime TEXT,
        dvostavno_knjigovodstvo BOOLEAN DEFAULT 0,
        smtp_server TEXT,
        smtp_port INTEGER,
        smtp_username TEXT,
        smtp_password TEXT,
        smtp_use_tls BOOLEAN DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS zakljucna_besedila (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        naziv TEXT NOT NULL,
        besedilo TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS kontni_nacrt (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stevilka TEXT NOT NULL UNIQUE,
        naziv TEXT NOT NULL,
        opis TEXT
    );

    CREATE TABLE IF NOT EXISTS priloge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_type TEXT NOT NULL,
        parent_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS placila_povezave (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        izpisek_postavka_id INTEGER NOT NULL,
        dokument_id INTEGER NOT NULL,
        znesek REAL NOT NULL,
        FOREIGN KEY (izpisek_postavka_id) REFERENCES izpiski_postavke(id),
        FOREIGN KEY (dokument_id) REFERENCES dokumenti(id)
    );

    CREATE TABLE IF NOT EXISTS email_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dokument_id INTEGER,
        tip_dokumenta TEXT,
        stevilka_dokumenta TEXT,
        prejemnik TEXT,
        zadeva TEXT,
        poslano_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'success',
        napaka TEXT
    );
    
    -- Glavna knjiga (Temeljnice)
    CREATE TABLE IF NOT EXISTS temeljnice (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poslovno_leto INTEGER NOT NULL,
        vrsta TEXT NOT NULL, -- 'IR' (izdani racuni), 'PR' (prejeti), 'IZP' (izpiski), 'PLA' (place), 'ROC' (rocne)
        stevilka TEXT NOT NULL,
        datum DATE NOT NULL,
        opis TEXT,
        dokument_id INTEGER, -- opcijska povezava na izvorni dokument
        zaklenjeno BOOLEAN DEFAULT 0,
        ustvarjeno_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS temeljnice_postavke (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        temeljnica_id INTEGER NOT NULL,
        konto TEXT NOT NULL,
        partner_id INTEGER,
        opis TEXT,
        datum_zapadlosti DATE,
        znesek_v_breme REAL DEFAULT 0,
        znesek_v_dobro REAL DEFAULT 0,
        zaprto BOOLEAN DEFAULT 0, -- za vodenje odprtih postavk
        FOREIGN KEY (temeljnica_id) REFERENCES temeljnice(id),
        FOREIGN KEY (partner_id) REFERENCES partnerji(id)
    );

    -- Povezava AJPES sheme s konti (za pripravo izkazov)
    CREATE TABLE IF NOT EXISTS ajpes_shema (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        oznaka_aop TEXT NOT NULL,
        naziv TEXT NOT NULL,
        vrsta_izkaza TEXT NOT NULL, -- 'bilanca_stanja', 'izkaz_poslovnega_izida'
        formula TEXT -- npr. '+020+021-029' (kateri konti se sestejejo ali odstejejo)
    );
    """)
    # Migracija: Dodaj kratko_ime v nastavitve, če ne obstaja
    try:
        cursor.execute("ALTER TABLE nastavitve ADD COLUMN kratko_ime TEXT")
    except:
        pass

    # Migracije: Dodaj SMTP nastavitve
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN smtp_server TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN smtp_port INTEGER")
    except: pass
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN smtp_username TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN smtp_password TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN smtp_use_tls BOOLEAN DEFAULT 1")
    except: pass

    # Migracija: Dodaj vkljuci_placilo in odstotek_placila v dokumenti
    try: cursor.execute("ALTER TABLE dokumenti ADD COLUMN vkljuci_placilo BOOLEAN DEFAULT 1")
    except: pass
    try: cursor.execute("ALTER TABLE dokumenti ADD COLUMN odstotek_placila REAL DEFAULT 100")
    except: pass
    
    # Migracija: Enota mere
    try: cursor.execute("ALTER TABLE dokumenti_postavke ADD COLUMN enota_mere TEXT DEFAULT 'kos'")
    except: pass

    # Migracija: Email predloge
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN email_template_racun TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN email_template_ponudba TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE nastavitve ADD COLUMN email_template_dobropis TEXT")
    except: pass

    # Migracija: Dodaj znesek_do (dolgotrajna oskrba) v place
    try: cursor.execute("ALTER TABLE place ADD COLUMN znesek_do REAL DEFAULT 0")
    except: pass

    # Migracije: Dodaj knjizeno status
    try: cursor.execute("ALTER TABLE dokumenti ADD COLUMN knjizeno BOOLEAN DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE izpiski_glava ADD COLUMN knjizeno BOOLEAN DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE place ADD COLUMN knjizeno BOOLEAN DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE potni_nalogi ADD COLUMN knjizeno BOOLEAN DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE temeljnice_postavke ADD COLUMN dokument_id INTEGER")
    except: pass
    try: cursor.execute("ALTER TABLE temeljnice_postavke ADD COLUMN dokument_tip TEXT")
    except: pass

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Podatkovna baza uspešno posodobljena!")
