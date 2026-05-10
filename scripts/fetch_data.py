#!/usr/bin/env python3
"""
Sentiment Radar — Data Fetcher v2
Uses requests for APIs/CSV, CamoFox for scraped sources.
Run on demand: python3 scripts/fetch_data.py
"""

import json, os, re, sys, time, warnings
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd

warnings.filterwarnings('ignore')

# ─── Paths ────────────────────────────────────────────────────────
REPO = Path(__file__).parent.parent
DATA_DIR = REPO / 'data'
CONFIG_FILE = REPO / 'config' / 'indicators.yaml'
SESSION_FILE = DATA_DIR / '.update_state.json'
CAMOFOX_BASE = 'http://localhost:9377'

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

CADENCE_MS = {
    'realtime': 3_600_000,
    'hourly':   3_600_000,
    'daily':   86_400_000,
    'weekly': 604_800_000,
    'biweekly':1_209_600_000,
    'monthly':2_592_000_000,
    'quarterly':7_776_000_000
}
MAX_HISTORY = 365


# ─────────────────────────────────────────────────────────────────
# CAMOFOX HELPERS
# ─────────────────────────────────────────────────────────────────

def camofox_tab(url, timeout=10):
    """Open a CamoFox tab, return tabId or None"""
    try:
        r = requests.post(
            f'{CAMOFOX_BASE}/tabs',
            json={'userId': 'sr', 'sessionKey': 'update', 'url': url},
            headers={'Content-Type': 'application/json'},
            timeout=timeout
        )
        data = r.json()
        return data.get('tabId')
    except:
        return None


def camofox_snapshot(tab_id, limit=500):
    """Get page text snapshot from CamoFox tab"""
    try:
        time.sleep(4)  # wait for page load
        url = f'{CAMOFOX_BASE}/tabs/{tab_id}/snapshot?userId=sr&limit={limit}'
        r = requests.get(url, timeout=20)
        data = r.json()
        return data.get('snapshot', '')
    except:
        return ''


def close_tab(tab_id):
    """Close a CamoFox tab"""
    try:
        requests.delete(f'{CAMOFOX_BASE}/tabs/{tab_id}', timeout=5)
    except:
        pass


# ─────────────────────────────────────────────────────────────────
# FETCH FUNCTIONS
# ─────────────────────────────────────────────────────────────────

# — CBOE VIX / SKEW via CamoFox ————————————————————————————————

def fetch_cboe_vix():
    tab_id = camofox_tab('https://www.cboe.com/us/indices/dashboard/vix/')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # Example line: "Last Sale: 17.19 Change: 0.11 (0.64%)"
        m = re.search(r'Last Sale:\s*([\d.]+)', snap)
        ts_m = re.search(r'Last Updated:\s*([\d\-]+)', snap)
        if m:
            val = float(m.group(1))
            date = ts_m.group(1) if ts_m else datetime.now(timezone.utc).strftime('%Y-%m-%d')
            return val, date
    except:
        close_tab(tab_id)
    return None, None


def fetch_cboe_skew():
    tab_id = camofox_tab('https://www.cboe.com/us/indices/dashboard/skew/')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        m = re.search(r'Last Sale:\s*([\d.]+)', snap)
        ts_m = re.search(r'Last Updated:\s*([\d\-]+)', snap)
        if m:
            return float(m.group(1)), (ts_m.group(1) if ts_m else None)
    except:
        close_tab(tab_id)
    return None, None


def fetch_cboe_putcall():
    """CBOE Equity Put/Call Ratio — daily"""
    url = 'https://api.cboe.com/putcallcurrent'
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    pc_list = data.get('put_call', [])
    if not pc_list:
        return None, None
    entry = pc_list[0]
    return float(entry['put_call_ratio']), entry.get('timestamp', '')


# — FRED CSV (works reliably) ————————————————————————————————————

def fetch_fred_csv(series_id):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code != 200:
        return None, None
    lines = r.text.strip().split('\n')
    if len(lines) < 2:
        return None, None
    last = lines[-1].split(',')
    return float(last[1].strip()), last[0].strip()


# — Yahoo Finance (DXY, Gold) ————————————————————————————————————

def fetch_yahoo(ticker):
    """Yahoo Finance with fallback to alternate endpoint"""
    for base in ['https://query1.finance.yahoo.com', 'https://query2.finance.yahoo.com']:
        try:
            url = f'{base}/v8/finance/chart/{ticker}'
            params = {'interval': '1d', 'range': '5d'}
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                result = r.json()['chart']['result'][0]
                closes = result['indicators']['quote'][0]['close']
                ts_list = result['timestamp']
                for i in range(len(closes)-1, -1, -1):
                    if closes[i] is not None:
                        dt = datetime.fromtimestamp(ts_list[i], tz=timezone.utc)
                        return round(closes[i], 2), dt.strftime('%Y-%m-%d')
        except:
            pass
    return None, None


# — CNN Fear & Greed via CamoFox —————————————————————————————————

def fetch_fear_greed():
    tab_id = camofox_tab('https://money.cnn.com/fear-and-greed/')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # CNN embeds the value in JS data attributes — look for number 0-100
        m = re.search(r'"fearAndGreed":\s*(\d+)', snap)
        if not m:
            nums = re.findall(r'\b([1-9]\d)\b', snap)
            # Filter to plausible fear/greed range
            for n in nums:
                v = int(n)
                if 0 <= v <= 100:
                    return v, datetime.now(timezone.utc).strftime('%Y-%m-%d')
        else:
            return int(m.group(1)), datetime.now(timezone.utc).strftime('%Y-%m-%d')
    except:
        close_tab(tab_id)
    return None, None


# — AAII Sentiment Survey via CamoFox —————————————————————————————

def fetch_aaii():
    tab_id = camofox_tab('https://www.aaii.com/sentimentsurvey/sent_results')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # Table format: "May 6 38.3% 28.7% 33.0%"
        # First data row = most recent
        rows = re.findall(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%', snap)
        if rows:
            # Most recent = last match (table is newest-first)
            r = rows[-1]
            month_map = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
                         'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
            month = month_map.get(r[0], '01')
            # Get year from the page context
            year_m = re.search(r'20(2[3-9])', snap)
            year = f'20{year_m.group(1)}' if year_m else '2026'
            date = f'{year}-{month}-{r[0][:2].lower()}??'  # placeholder until we get exact day
            return float(r[1]), None  # return bullish %
        # Fallback: look for any percentage in table context
        m = re.search(r'Bullish.*?(\d+)%', snap)
        if m:
            return float(m.group(1)), None
    except:
        close_tab(tab_id)
    return None, None


# — NAAIM via CamoFox —————————————————————————————————————————————————

def fetch_naaim():
    tab_id = camofox_tab('https://naaim.org/programs/naaim-exposure-index/')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # Page has "This week's NAAIM Exposure Index number is*: 96.67"
        m = re.search(r"(?:week's|week’s|This week).*?(\d+\.?\d*)", snap, re.IGNORECASE)
        if not m:
            m = re.search(r'NAAIM Number[^<\d]*(\d+\.?\d+)', snap)
        if not m:
            nums = re.findall(r'\b(\d{2,3}\.\d{1,2})\b', snap)
            for n in nums:
                v = float(n)
                if 0 <= v <= 200:
                    # posted date from "Posted on Thursday, May 7, 2026"
                    ts_m = re.search(r'Posted on.*?,\s*(\w+)\s+(\d{1,2}),\s*(\d{4})', snap)
                    if ts_m:
                        from calendar import month_name
                        month = list(month_name).index(ts_m.group(1))
                        date = f'{ts_m.group(3)}-{month:02d}-{int(ts_m.group(2)):02d}'
                        return v, date
                    return v, None
        if m:
            ts_m = re.search(r'Posted on.*?,\s*(\w+)\s+(\d{1,2}),\s*(\d{4})', snap)
            date = None
            if ts_m:
                from calendar import month_name
                month = list(month_name).index(ts_m.group(1))
                date = f'{ts_m.group(3)}-{month:02d}-{int(ts_m.group(2)):02d}'
            return float(m.group(1)), date
    except:
        close_tab(tab_id)
    return None, None


# — FRED Margin Debt (monthly, scrape FINRA) ————————————————————————

def fetch_margin_debt():
    tab_id = camofox_tab('https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # Look for "$X,XXX,XXX,XXX" pattern (trillions → convert to billions)
        amounts = re.findall(r'\$?([\d,]+\.?\d*)\s*(?:B|trillion)', snap)
        for a in amounts:
            val = float(a.replace(',', ''))
            if 500 <= val <= 3000:  # in billions
                return val, None
        # Fallback: look for any large number
        bigNums = re.findall(r'\$?([\d,]+\.\d+)', snap)
        for n in bigNums:
            try:
                v = float(n.replace(',',''))
                if 1e11 <= v <= 3e12:  # raw dollars
                    return round(v/1e9, 1), None
            except:
                pass
    except:
        close_tab(tab_id)
    return None, None


# — CFTC COT (weekly) ————————————————————————————————————————————————————

def fetch_cftc_cot():
    tab_id = camofox_tab('https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # CFTC page has a table with report date
        ts_m = re.search(r'As of\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', snap)
        if ts_m:
            try:
                dt = datetime.strptime(ts_m.group(1), '%B %d, %Y')
                date_str = dt.strftime('%Y-%m-%d')
            except:
                date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        else:
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        # Note: actual COT values require PDF/Excel download
        # For now we record the date; actual values need manual entry
        return None, date_str
    except:
        close_tab(tab_id)
    return None, None


# — VIX Term Structure via CamoFox ——————————————————————————————————

def fetch_vix_term_structure():
    tab_id = camofox_tab('https://www.cboe.com/tradable-products/vix/term-structure/')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # Page shows contango/backwardation state
        m = re.search(r'(contango|backwardation)', snap, re.IGNORECASE)
        state = m.group(1).lower() if m else 'unknown'
        # Also capture front-month vs 2nd month spread
        spread_m = re.search(r'[\d.]+\s*/\s*[\d.]+\s*(?:contango|backwardation)', snap, re.IGNORECASE)
        return state, None
    except:
        close_tab(tab_id)
    return None, None


# — Buffett Indicator (FRED) —————————————————————————————————————————

def fetch_buffett():
    # The correct FRED series for Buffett is QTARGET (Market Cap to GDP ratio)
    # Actually: ^NYXADMIN? Let's try TOTALSTOCKMARKETCAP / GDP
    # The most common public FRED series used for Buffett is: T10YIE (not quite)
    # The Wilshire 5000 total market cap / GDP is the canonical one
    # Try series: T5000TT (Wilshire 5000 / GDP ratio) or WILLLR
    for series in ['T5000TT', 'WILL5000PR']:
        val, date = fetch_fred_csv(series)
        if val is not None:
            return val * 100, date  # convert to percentage
    return None, None


# — Top-10 Concentration —————————————————————————————————————————————

def fetch_top10_concentration():
    """S&P 500 top-10 weight. Use S&P global page via CamoFox."""
    tab_id = camofox_tab('https://www.spglobal.com/spdji/en/indices/equity/sp-500')
    if not tab_id:
        return None, None
    try:
        snap = camofox_snapshot(tab_id)
        close_tab(tab_id)
        # Look for concentration metric or top holdings table
        m = re.search(r'(?:Top 10|Concentration).*?(\d+)%', snap)
        if not m:
            m = re.search(r'(?:Magnificent 7|M7).*?(\d+)%', snap)
        if m:
            return float(m.group(1)), datetime.now(timezone.utc).strftime('%Y-%m-%d')
    except:
        close_tab(tab_id)
    return None, None


# ─────────────────────────────────────────────────────────────────
# UPDATE LOGIC
# ─────────────────────────────────────────────────────────────────

def should_update(ind_id, cadence, state):
    if ind_id not in state:
        return True
    last = state[ind_id].get('last_updated')
    if not last:
        return True
    threshold = CADENCE_MS.get(cadence, 86_400_000)
    last_ts = datetime.fromisoformat(last.replace('Z', '+00:00')).timestamp()
    threshold_sec = CADENCE_MS.get(cadence, 86_400_000) / 1000
    return (time.time() - last_ts) >= threshold_sec


def load_state():
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(SESSION_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def load_ind_json(ind_id):
    path = DATA_DIR / f'{ind_id}.json'
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_ind_json(ind_id, data):
    path = DATA_DIR / f'{ind_id}.json'
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_history(historical, date_str, value):
    if date_str is None or value is None:
        return historical
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return historical
    # Avoid duplicates
    if historical and historical[-1].get('date') == date_str:
        historical[-1]['value'] = round(fval, 4)
        return historical
    historical.append({'date': date_str, 'value': round(fval, 4)})
    if len(historical) > MAX_HISTORY:
        historical = historical[-MAX_HISTORY:]
    return historical


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

INDICATORS = {
    'vix':         {'name': 'VIX',              'cadence': 'daily',    'fn': fetch_cboe_vix,        'unit': ''},
    'skew':        {'name': 'SKEW',             'cadence': 'daily',    'fn': fetch_cboe_skew,       'unit': ''},
    'putcall':     {'name': 'Put/Call Ratio',   'cadence': 'daily',    'fn': fetch_cboe_putcall,    'unit': ''},
    'fear_greed':  {'name': 'Fear & Greed',    'cadence': 'daily',    'fn': fetch_fear_greed,     'unit': '/100'},
    'hy_spreads':  {'name': 'HY Spreads',       'cadence': 'daily',    'fn': lambda: fetch_fred_csv('BAMLH0A0HYM2'), 'unit': 'bp'},
    'dxy':         {'name': 'DXY',              'cadence': 'realtime', 'fn': lambda: fetch_yahoo('DX-Y.NYB'), 'unit': ''},
    'gold':        {'name': 'Gold',             'cadence': 'realtime', 'fn': lambda: fetch_yahoo('GC=F'), 'unit': ''},
    'buffett':     {'name': 'Buffett',          'cadence': 'quarterly','fn': fetch_buffett,         'unit': '%'},
    'aaii':        {'name': 'AAII Bullish',     'cadence': 'weekly',   'fn': fetch_aaii,           'unit': '%'},
    'naaim':       {'name': 'NAAIM',            'cadence': 'weekly',   'fn': fetch_naaim,          'unit': ''},
    'margin_debt': {'name': 'Margin Debt',      'cadence': 'monthly',  'fn': fetch_margin_debt,    'unit': 'B'},
    'cftc_cot':    {'name': 'CFTC COT',         'cadence': 'weekly',   'fn': fetch_cftc_cot,        'unit': ''},
    'vix_term':    {'name': 'VIX Term',         'cadence': 'daily',    'fn': fetch_vix_term_structure, 'unit': ''},
    'top10':       {'name': 'Top-10 Conc.',     'cadence': 'daily',    'fn': fetch_top10_concentration, 'unit': '%'},
}


def update_one(ind_id, meta, state):
    cadence = meta['cadence']
    now_str = datetime.now(timezone.utc).isoformat()

    if not should_update(ind_id, cadence, state):
        last = state.get(ind_id, {}).get('last_updated', 'never')
        return f'⏳ {ind_id}: still fresh (updated {last[:10] if last else "never"})', state

    try:
        val, date_str = meta['fn']()
    except Exception as e:
        return f'❌ {ind_id}: {str(e)[:60]}', state

    data = load_ind_json(ind_id)
    historical = data.get('historical', [])

    if val is not None and date_str:
        historical = append_history(historical, date_str, val)
    elif val is not None:
        # No date but have value — use today
        historical = append_history(historical, datetime.now(timezone.utc).strftime('%Y-%m-%d'), val)
    elif date_str:
        # Have date but no value — just update the date, don't corrupt history
        pass

    data['indicator'] = ind_id
    data['name'] = meta['name']
    data['last_updated'] = now_str
    data['update_cadence'] = cadence
    data['current_value'] = val if val is not None else data.get('current_value')
    data['unit'] = meta['unit']
    data['historical'] = historical

    save_ind_json(ind_id, data)
    state[ind_id] = {'last_updated': now_str, 'value': val}
    save_state(state)

    if val is not None:
        return f'✅ {ind_id}: {val} (updated)', state
    else:
        return f'⚠️  {ind_id}: no value captured (date only: {date_str})', state


def main():
    print('📡 Sentiment Radar — Data Fetcher v2\n')
    state = load_state()
    results = []
    updated = skipped = 0

    for ind_id, meta in INDICATORS.items():
        msg, state = update_one(ind_id, meta, state)
        results.append(msg)
        if '✅' in msg:
            updated += 1
        elif '⏳' in msg:
            skipped += 1
        time.sleep(1)

    print('\n'.join(results))
    print(f'\n📊 Updated: {updated} | Skipped: {skipped}')

    # Git push
    try:
        os.chdir(REPO)
        import subprocess
        subprocess.run(['git', 'add', 'data/'], check=True, capture_output=True)
        msg = f'data: update {datetime.now(timezone.utc).strftime("%Y-%m-%d")}'
        r = subprocess.run(['git', 'commit', '-m', msg], capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(['git', 'push'], check=True, capture_output=True)
            print(f'\n🚀 Pushed to GitHub: {msg}')
        elif 'nothing to commit' in r.stderr:
            print('\n📦 No changes to push')
        else:
            print(f'\n⚠️  Git: {r.stderr[:100]}')
    except Exception as e:
        print(f'\n⚠️  Git push: {e}')

    print('\n✅ Done.')


if __name__ == '__main__':
    main()