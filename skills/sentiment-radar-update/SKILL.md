---
name: sentiment-radar-update
version: 1.0.0
description: "Update sentiment-radar indicator data. Run on request — checks cadence per indicator, fetches new data, appends to historical, pushes to GitHub."
author: Latent Spaceman
---

# Sentiment Radar — Update Skill

Updates indicator data in the `sentiment-radar` repo. Run when the user asks to refresh data.

## Workflow

```
1. Read config/indicators.yaml — get indicator list + source URLs
2. For each indicator:
   a. Read data/<indicator>.json — check last_updated
   b. If stale (past cadence threshold), fetch new data
   c. Append to historical array
   d. Save data/<indicator>.json
3. Commit and push to GitHub
```

## Cadence Thresholds

| Cadence | Threshold |
|---|---|
| realtime | 1 hour |
| daily | 24 hours |
| weekly | 7 days |
| biweekly | 14 days |
| monthly | 30 days |
| quarterly | 90 days |

## Data Format

Each `data/<indicator>.json`:
```json
{
  "indicator": "vix",
  "name": "VIX",
  "last_updated": "2026-05-10T18:30:00Z",
  "update_cadence": "realtime",
  "current_value": 18.47,
  "unit": "index",
  "historical": [
    {"date": "2026-05-10", "value": 18.47},
    {"date": "2026-05-09", "value": 19.23}
  ]
}
```

## Indicator Fetch Methods

### API (CBOE)
- VIX: `GET https://api.cboe.com/vixcurrent` → parse JSON
- SKEW: `GET https://api.cboe.com/skewcurrent` → parse JSON
- Put/Call: `GET https://api.cboe.com/putcallcurrent` → parse JSON

### CSV (FRED)
- HY Spreads: `GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2`
- Buffett: `GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=TCEMO`
- Parse: take last row's date + value

### Yahoo Finance API
- DXY: `GET https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=5d`
- Gold: `GET https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?interval=1d&range=5d`
- Extract `chart.result[0].indicators.quote[0].close` last entry

### Scrape
- Fear & Greed: CNN HTML → parse current value
- AAII: aaii.com sentiment history table
- NAAIM: StockCharts NAAIM page
- Margin Debt: FINRA margin statistics page
- CFTC COT: CFTC website report
- Insider: SEC Form 4 RSS feed

### Notes
- Always take the most recent available value as `current_value`
- Append to historical with date in `YYYY-MM-DD` format
- Keep last 365 days of history for daily indicators, last 52 weeks for weekly
- Do NOT overwrite historical — only append new data points
- If fetch fails, log error and skip indicator (don't corrupt existing data)
- Report status per indicator: updated, skipped (fresh), or failed

## GitHub Push

After all indicators processed:
```bash
cd ~/sentiment-radar
git add data/
git commit -m "data: $(date +%Y-%m-%d) update"
git push
```

## User Output

Report a simple status per indicator:
```
VIX ✅ updated → 18.47
SKEW ✅ updated → 124.3
AAII ⏳ still fresh (3 days until due)
HY Spreads ✅ updated → 312 bp
...
```

Also mention any that failed and why.

## Skills Reference

- `fetch_data.py` (scripts/) — Python fetcher with all source handlers
- `config/indicators.yaml` — full indicator list with URLs and methods