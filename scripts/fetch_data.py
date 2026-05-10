#!/usr/bin/env python3
"""
Sentiment Radar — Data Fetcher
Fetches latest values for all indicators, appends to historical, saves JSON.
Run on demand: python3 scripts/fetch_data.py
"""

import json, os, sys, time, warnings
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

warnings.filterwarnings('ignore')

# ─── Paths ────────────────────────────────────────────────────────
REPO = Path(__file__).parent.parent
DATA_DIR = REPO / 'data'
CONFIG_FILE = REPO / 'config' / 'indicators.yaml'
SESSION_FILE = DATA_DIR / '.update_state.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}

# ─── Cadence thresholds (ms) ───────────────────────────────────────
CADENCE_MS = {
    'realtime': 3_600_000,    # 1 hr
    'hourly':   3_600_000,
    'daily':   86_400_000,    # 24 hr
    'weekly': 604_800_000,    # 7 days
    'biweekly':1_209_600_000, # 14 days
    'monthly':2_592_000_000,  # 30 days
    'quarterly':7_776_000_000 # 90 days
}

MAX_HISTORY_DAYS = 365


# ─────────────────────────────────────────────────────────────────
# FETCH FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def fetch_cboe_vix():
    """CBOE VIX — real-time via vixcurrent endpoint"""
    url = 'https://api.cboe.com/vixcurrent'
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    # Example: {"vix":[{"vvix":22.67,"vix":18.47,"timestamp":"..."}]}
    vix_list = data.get('vix', [])
    if not vix_list:
        return None, None
    entry = vix_list[0]
    return float(entry['vix']), entry.get('timestamp', '')


def fetch_cboe_skew():
    """CBOE SKEW Index — real-time"""
    url = 'https://api.cboe.com/skewcurrent'
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    skew_list = data.get('skew', [])
    if not skew_list:
        return None, None
    entry = skew_list[0]
    return float(entry['skew']), entry.get('timestamp', '')


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


def fetch_fred_csv(series_id):
    """FRED CSV — used for HY spreads, Buffett, etc."""
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    lines = r.text.strip().split('\n')
    if len(lines) < 2:
        return None, None
    last_row = lines[-1]
    parts = last_row.split(',')
    date = parts[0].strip()
    value = float(parts[1].strip())
    return value, date


def fetch_yahoo_finance(ticker):
    """Yahoo Finance chart API — DXY, Gold, etc."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
    params = {'interval': '1d', 'range': '5d'}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    result = r.json()['chart']['result'][0]
    timestamps = result['timestamp']
    closes = result['indicators']['quote'][0]['close']
    # Find last non-null close
    for i in range(len(closes)-1, -1, -1):
        if closes[i] is not None:
            dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
            return closes[i], dt.strftime('%Y-%m-%d')
    return None, None


def fetch_cnn_fear_greed():
    """CNN Fear & Greed Index — HTML scrape of their JS embed"""
    url = 'https://money.cnn.com/fear-and-greed/'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    # The value is injected via JS into a span or div with specific class
    # Try to find it in script tags or data attributes
    import re, json as _json

    # Look for FearGreed data embedded in page
    scripts = soup.find_all('script')
    for script in scripts:
        if not script.string:
            continue
        # CNN embeds a JSON blob with current value
        matches = re.findall(r'"fearAndGreed":\s*(\d+)', script.string)
        if matches:
            return int(matches[0]), datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Fallback: look for the numeric display
    for tag in soup.find_all(['span', 'div', 'p']):
        txt = tag.get_text(strip=True)
        if txt.isdigit() and 1 <= int(txt) <= 100:
            return int(txt), datetime.now(timezone.utc).strftime('%Y-%m-%d')

    return None, None


def fetch_aaii():
    """AAII Sentiment Survey — scrape the history table"""
    url = 'https://www.aaii.com/sentiments/sentiments_history'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    table = soup.find('table')
    if not table:
        return None, None, None

    rows = table.find_all('tr')
    # Skip header row, get most recent data row
    # Format: Date, Bullish %, Bearish %, Neutral %
    for row in rows[1:4]:  # check first 3 data rows
        cols = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cols) >= 4 and cols[0]:
            date_str = cols[0]
            try:
                # Parse date like "05/08/2026"
                dt = datetime.strptime(date_str, '%m/%d/%Y')
                date_str = dt.strftime('%Y-%m-%d')
            except:
                pass
            bullish = float(cols[1]) if cols[1].replace('.','').isdigit() else None
            return bullish, date_str, None  # bullish is the key indicator
    return None, None, None


def fetch_naaim():
    """NAAIM Exposure Index — StockCharts scrape"""
    url = 'https://stockcharts.com/freechart/symbols.html'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    # Look for NAAIM value in the page
    import re
    scripts = soup.find_all('script')
    for script in scripts:
        if not script.string:
            continue
        matches = re.findall(r'NAAIM[^0-9]*([0-9]{2,3}(?:\.[0-9]+)?)', script.string)
        if matches:
            return float(matches[0]), datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Fallback: search the full page text for a number near "NAAIM"
    text = soup.get_text()
    import re
    matches = re.findall(r'NAAIM.*?(\d{2,3}\.\d+)', text)
    if matches:
        return float(matches[0][:5]), datetime.now(timezone.utc).strftime('%Y-%m-%d')

    return None, None


def fetch_margin_debt():
    """FINRA Margin Debt — scrape monthly statistics"""
    url = 'https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics'
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    # The data is usually in a CSV download link or a table
    # Try to find a table with margin debt figures
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all(['td','th'])]
            if len(cols) >= 2:
                # Look for dollar amount patterns like "$1,221,000,000,000"
                for col in cols:
                    col_clean = col.replace('$','').replace(',','')
                    try:
                        val = float(col_clean)
                        if 1e12 <= val <= 3e12:  # margin debt in trillions → billions
                            # Convert to billions
                            val_b = val / 1e9
                            # Get date from adjacent cell
                            return val_b, cols[0]
                    except:
                        pass
    return None, None


def fetch_buffett():
    """Buffett Indicator — FRED total market cap / GDP"""
    # Wilshire 5000 / GDP ratio is at TREM
    val, date = fetch_fred_csv('TCEMO')
    return val, date


def fetch_hy_spreads():
    """HY Credit Spreads — ICE BofA via FRED"""
    val, date = fetch_fred_csv('BAMLH0A0HYM2')
    return val, date


def fetch_ig_spreads():
    """IG Credit Spreads — ICE BofA via FRED"""
    val, date = fetch_fred_csv('BAMLH0A0HYM2SPREAD')
    return val, date


def fetch_cftc_cot():
    """CFTC COT — scrape the main page for latest release date + values"""
    url = 'https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    # Look for the most recent report date
    # CFTC publishes data every Friday for the prior Tuesday's close
    import re
    text = soup.get_text()
    date_match = re.search(r'(?:As of|All data as of|Report Date)[:\s]+([A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4})', text)
    if date_match:
        try:
            dt = datetime.strptime(date_match.group(1), '%B %d, %Y')
            date_str = dt.strftime('%Y-%m-%d')
        except:
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    else:
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    return None, date_str  # COT requires PDF parsing for actual values — flag as data unavailable


# ─────────────────────────────────────────────────────────────────
# UPDATE LOGIC
# ─────────────────────────────────────────────────────────────────

def should_update(indicator_id, cadence, state):
    """Check if indicator is due for update based on last_updated"""
    if indicator_id not in state:
        return True  # never updated
    last = state[indicator_id].get('last_updated')
    if not last:
        return True
    threshold = CADENCE_MS.get(cadence, 86_400_000)
    last_ts = datetime.fromisoformat(last.replace('Z','+00:00')).timestamp()
    return (time.time() - last_ts) >= threshold


def load_state():
    """Load last update timestamps"""
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(SESSION_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def load_indicator_json(indicator_id):
    path = DATA_DIR / f'{indicator_id}.json'
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_indicator_json(indicator_id, data):
    path = DATA_DIR / f'{indicator_id}.json'
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_history(historical, date_str, value):
    """Append new data point, trim to MAX_HISTORY_DAYS"""
    # Avoid duplicates
    if historical and historical[-1].get('date') == date_str:
        historical[-1]['value'] = value
        return historical
    historical.append({'date': date_str, 'value': value})
    # Keep last MAX_HISTORY_DAYS
    if len(historical) > MAX_HISTORY_DAYS:
        historical = historical[-MAX_HISTORY_DAYS:]
    return historical


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def update_indicator(ind_id, ind_meta, state):
    """Update a single indicator, return status string"""
    cadence = ind_meta.get('cadence', 'daily')
    method = ind_meta.get('method', '')
    now_str = datetime.now(timezone.utc).isoformat()

    try:
        if ind_id == 'vix':
            val, ts = fetch_cboe_vix()
        elif ind_id == 'skew':
            val, ts = fetch_cboe_skew()
        elif ind_id == 'putcall':
            val, ts = fetch_cboe_putcall()
        elif ind_id == 'hy_spreads':
            val, ts = fetch_hy_spreads()
        elif ind_id == 'ig_spreads':
            val, ts = fetch_ig_spreads()
        elif ind_id == 'buffett':
            val, ts = fetch_buffett()
        elif ind_id == 'dxy':
            val, ts = fetch_yahoo_finance('DX-Y.NYB')
        elif ind_id == 'gold':
            val, ts = fetch_yahoo_finance('GC=F')
        elif ind_id == 'fear_greed':
            val, ts = fetch_cnn_fear_greed()
        elif ind_id == 'aaii':
            val, date_str, _ = fetch_aaii()
            ts = date_str
        elif ind_id == 'naaim':
            val, ts = fetch_naaim()
        elif ind_id == 'margin_debt':
            val, ts = fetch_margin_debt()
        elif ind_id == 'cftc_cot':
            val, ts = fetch_cftc_cot()
        else:
            return f"⏭️  {ind_id}: unknown indicator", state

        if val is None:
            # Could be no new data or fetch failed
            # Check if it's a cadence issue or actual failure
            return f"⚠️  {ind_id}: fetch returned no data (source may be unavailable)", state

        # Load existing data
        data = load_indicator_json(ind_id)
        historical = data.get('historical', [])

        # Parse date
        date_str = ts if ts else datetime.now(timezone.utc).strftime('%Y-%m-%d')

        # Append to history
        historical = append_history(historical, date_str, val)

        # Save
        data['indicator'] = ind_id
        data['name'] = ind_meta.get('name', ind_id)
        data['last_updated'] = now_str
        data['update_cadence'] = cadence
        data['current_value'] = val
        data['unit'] = ind_meta.get('unit', '')
        data['historical'] = historical

        save_indicator_json(ind_id, data)

        # Update state
        state[ind_id] = {'last_updated': now_str, 'value': val}
        save_state(state)

        return f"✅ {ind_id}: {val} (updated)", state

    except Exception as e:
        return f"❌ {ind_id}: {str(e)[:60]}", state


def main():
    print("📡 Sentiment Radar — Data Fetcher\n")

    state = load_state()
    results = []

    # Define indicators to fetch (id → meta)
    indicators = {
        'vix':          {'name': 'VIX',           'cadence': 'realtime',  'method': 'api',   'unit': ''},
        'skew':         {'name': 'SKEW',           'cadence': 'realtime',  'method': 'api',   'unit': ''},
        'putcall':      {'name': 'Put/Call Ratio', 'cadence': 'daily',    'method': 'api',   'unit': ''},
        'fear_greed':   {'name': 'Fear & Greed',  'cadence': 'daily',    'method': 'scrape', 'unit': '/100'},
        'hy_spreads':   {'name': 'HY Spreads',     'cadence': 'daily',    'method': 'csv',    'unit': 'bp'},
        'ig_spreads':   {'name': 'IG Spreads',     'cadence': 'daily',    'method': 'csv',    'unit': 'bp'},
        'dxy':          {'name': 'DXY',            'cadence': 'realtime', 'method': 'api',   'unit': ''},
        'gold':         {'name': 'Gold',           'cadence': 'realtime', 'method': 'api',   'unit': ''},
        'buffett':      {'name': 'Buffett',        'cadence': 'quarterly','method': 'csv',   'unit': '%'},
        'aaii':         {'name': 'AAII Bullish',   'cadence': 'weekly',   'method': 'scrape', 'unit': '%'},
        'naaim':        {'name': 'NAAIM',          'cadence': 'weekly',   'method': 'scrape', 'unit': ''},
        'margin_debt':  {'name': 'Margin Debt',   'cadence': 'monthly',  'method': 'scrape', 'unit': 'B'},
        'cftc_cot':     {'name': 'CFTC COT',       'cadence': 'weekly',   'method': 'scrape', 'unit': ''},
    }

    updated_count = 0
    skipped_count = 0

    for ind_id, ind_meta in indicators.items():
        cadence = ind_meta['cadence']
        if not should_update(ind_id, cadence, state):
            next_update = state.get(ind_id, {}).get('last_updated', 'never')
            results.append(f"⏳ {ind_id}: still fresh (updated {next_update[:10]})")
            skipped_count += 1
            continue

        msg, state = update_indicator(ind_id, ind_meta, state)
        results.append(msg)
        if '✅' in msg:
            updated_count += 1

        time.sleep(1)  # be polite to data sources

    print('\n'.join(results))
    print(f"\n📊 Updated: {updated_count} | Skipped: {skipped_count}")

    # GitHub push
    try:
        os.chdir(REPO)
        import subprocess
        subprocess.run(['git', 'add', 'data/'], check=True, capture_output=True)
        subprocess.run(['git', 'add', 'data/.update_state.json'], check=True, capture_output=True)
        msg = f"data: update {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        result = subprocess.run(['git', 'commit', '-m', msg], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"\n🚀 Pushed to GitHub: {msg}")
        else:
            if 'nothing to commit' in result.stderr:
                print("\n📦 No changes to push (data already current)")
            else:
                print(f"\n⚠️  Git commit failed: {result.stderr}")
    except Exception as e:
        print(f"\n⚠️  Git push failed: {e}")

    print("\n✅ Done.")


if __name__ == '__main__':
    main()