# Sentiment Radar

AI market sentiment tracker — monitors 16+ indicators across volatility, options flow, positioning, credit, and valuation.

**Update cycle:** You ask me to update → I run the update skill → new data is pushed to the repo → dashboard reflects the latest.

**Indicators tracked:**
- 🔴 Volatility: VIX, SKEW, VVIX, VIX Term Structure
- 🟡 Options: Put/Call Ratio, S&P 500 Put/Call
- 🟢 Surveys: AAII Sentiment, NAAIM Exposure, BofA Fund Manager Survey
- 🟠 Positioning: CFTC COT, FINRA Margin Debt, Short Interest
- 🔵 Credit: HY Spreads, IG Spreads, TED Spread
- 🟣 Valuation: Buffett Indicator, Top-10 Concentration
- ⚫ Smart Money: Corporate Insider Buying
- ⚪ Cross-Asset: DXY Dollar Index, Gold

## Data Format

Each indicator lives in `data/<indicator>.json`:

```json
{
  "indicator": "vix",
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

## Update Protocol

When you want fresh data:
1. Ask me to run the update
2. I check `config/indicators.yaml` for what's due
3. Fetch new data, append to historical, save JSON
4. Push to GitHub
5. Dashboard reads the updated JSON files