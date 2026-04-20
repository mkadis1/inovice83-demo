import re
from datetime import datetime
import pdfplumber

def clean_number(sl_num_str):
    """Converts Slovenian number format (6.557,16) to standard float (6557.16)"""
    if not sl_num_str: return 0.00
    try:
        return float(sl_num_str.replace('.', '').replace(',', '.'))
    except ValueError:
        return 0.00

def parse_date(date_str):
    """Converts DD.MM.YYYY to YYYY-MM-DD"""
    try:
        dt = datetime.strptime(date_str.strip(), "%d.%m.%Y")
        return dt.strftime("%Y-%m-%d"), dt
    except ValueError:
        return "", None

def extract_data_from_pdf(pdf_path):
    text_standard = ""
    text_layout = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text_standard += page.extract_text() + "\n"
            text_layout += page.extract_text(layout=True) + "\n"
            
    try:
        with open("pdf_debug.txt", "w", encoding="utf-8") as f:
            f.write("=== STANDARD ===\n" + text_standard + "\n=== LAYOUT ===\n" + text_layout)
    except:
        pass
        
    data = {"transactions": []}

    # --- Header Metadata ---
    iban_match = re.search(r'IBAN:\s*(SI56[\s\d]+)', text_standard)
    if iban_match: data['iban'] = iban_match.group(1).replace(" ", "").strip()

    lines = text_standard.split('\n')
    for i, line in enumerate(lines):
        if 'IBAN: SI56' in line and i + 1 < len(lines):
            data['owner_name'] = lines[i+1].strip()
            break

    # Statement Number - multiple format variants
    search_text = text_standard + "\n" + text_layout
    stmt_num_match = (
        re.search(r'[SŠsš©¹][Tt]\.?\s*[Ii][Zz][Pp]\.?\s*:?\s*(\d+)', search_text) or
        re.search(r'[SŠsš©¹]ifra\s+izpiska\s*:?\s*(\d+)', search_text, re.IGNORECASE) or
        re.search(r'[Ii]zpis(?:ek)?\s*(?:[šs¹]t\.?|stevilka|številka|¹tevilka|izpisek)?\s*:?\s*(\d+)', search_text, re.IGNORECASE) or
        re.search(r'Zap\.?\s*[šs¹]t\.?(?:\s*izpiska)?\s*:?\s*(\d+)', search_text, re.IGNORECASE) or
        re.search(r'[ŠS©¹]tevilka\s+izpiska\s*:?\s*(\d+)', search_text, re.IGNORECASE) or
        re.search(r'(?:Statement\s+no\.?|Izpisek)\s*[:#]?\s*(\d+)', search_text, re.IGNORECASE)
    )
    data['statement_number'] = stmt_num_match.group(1).strip() if stmt_num_match else "UNKNOWN"

    curr_date_match = re.search(r'PROMET IN STANJE.*?DNE:\s*(\d{1,2}\.\d{1,2}\.\d{4})', text_standard, re.IGNORECASE)
    if curr_date_match:
        data['statement_date'], dt_obj = parse_date(curr_date_match.group(1))
        safe_stmt_num = data.get('statement_number', dt_obj.strftime('%Y%m%d'))
        data['msg_id'] = f"{safe_stmt_num}-{dt_obj.strftime('%Y%m%d')}"
    else:
        data['statement_date'] = datetime.now().strftime("%Y-%m-%d")

    prev_date_match = re.search(r'Datum predhodnega izpiska:\s*(\d{1,2}\.\d{1,2}\.\d{4})', text_standard)
    if prev_date_match:
        data['previous_date'], _ = parse_date(prev_date_match.group(1))

    # --- Balances ---
    flat_text = text_layout.replace('\n', ' ')
    if "STANJE" not in flat_text: flat_text = text_standard.replace('\n', ' ')
        
    open_bal_match = re.search(r'ZA[ČCÈ]ETNO\s+STANJE[\s\.:]*([\d]+(?:[\.,]\d+)*)', flat_text, re.IGNORECASE)
    if open_bal_match: data['opening_balance'] = clean_number(open_bal_match.group(1))
    else: data['opening_balance'] = 0.0

    close_bal_match = re.search(r'KON[ČCÈ]NO\s+STANJE[\s\.:]*([\d]+(?:[\.,]\d+)*)', flat_text, re.IGNORECASE)
    if close_bal_match: data['closing_balance'] = clean_number(close_bal_match.group(1))
    else: data['closing_balance'] = 0.0

    # --- Transactions Buffer ---
    current_mode = "breme"  # DBIT
    raw_transactions = []
    current_tx = None
    
    for line in text_layout.split('\n'):
        clean_line = line.strip()
        if not clean_line: continue
            
        if "PROMET V BREME" in clean_line.upper():
            current_mode = "breme"; continue
        elif "PROMET V DOBRO" in clean_line.upper():
            current_mode = "dobro"; continue

        # Standard transaction check - robust regex for amount with optional thousands separator
        tx_match = re.search(r'^(\d{2}\.\d{2}\.\d{4})\s+(.+?)\s+([\d\.]{1,12},\d{2})$', clean_line)
        if tx_match:
            if current_tx: raw_transactions.append(current_tx)
            current_tx = {
                'amount': clean_number(tx_match.group(3)), 'type': current_mode,
                'code': "PMNT", 'raw_description': tx_match.group(2).strip() + " "
            }
        # Bank fees summary check - ONLY add if not already caught in transaction list
        elif re.search(r'^(NADOMESTIL[OA].*?|OSTALA\s+NADOMESTILA.*?)[\.\s]*:\s*([\d]+(?:[\.,]\d+)*)$', clean_line, re.IGNORECASE):
            fee_match = re.search(r':\s*([\d]+(?:[\.,]\d+)*)$', clean_line)
            if fee_match:
                fee_amt = clean_number(fee_match.group(1))
                # Check if this fee (typically 8.50 or similar) is already in found transactions
                already_exists = any(tx['amount'] == fee_amt and tx['type'] == "breme" for tx in raw_transactions)
                if fee_amt > 0 and not already_exists:
                    raw_transactions.append({
                        'amount': fee_amt, 'type': "breme", 'code': "PMNT",
                        'raw_description': "NADOMESTILA / PROVIZIJA ", 'partner': "NOVA LJUBLJANSKA BANKA D.D."
                    })
        elif current_tx and not re.search(r'^(ZA[ČCÈ]ETNO|KON[ČCÈ]NO|SKUPAJ)', clean_line, re.IGNORECASE):
            current_tx['raw_description'] += clean_line + " "

    if current_tx: raw_transactions.append(current_tx)

    # --- Transaction Details Extraction ---
    for tx in raw_transactions:
        desc = re.sub(r'\s+', ' ', tx['raw_description'])
        
        nam_match = re.search(r'NAM\s*:\s*([^\*]+)', desc)
        base_desc = nam_match.group(1).strip() if nam_match else desc.strip()
        
        refpl_match = re.search(r'REFPL\s*:\s*([^\*]+)', desc)
        refp_match = re.search(r'REFP\s*:\s*([^\*]+)', desc)
        reference = ""
        if refpl_match:
            reference = refpl_match.group(1).strip()
        elif refp_match:
            reference = refp_match.group(1).strip()
            
        if reference:
            tx['description'] = f"{reference} / {base_desc}"
        else:
            tx['description'] = base_desc
            
        nazpl_match = re.search(r'NAZPL\s*:\s*([^\*]+)', desc)
        nazp_match = re.search(r'NAZP\s*:\s*([^\*]+)', desc)
        
        if nazpl_match: 
            tx['partner'] = nazpl_match.group(1).strip()
        elif nazp_match: 
            tx['partner'] = nazp_match.group(1).strip()
        else: 
            tx['partner'] = "Neznan"
            
        data['transactions'].append(tx)

    return data
