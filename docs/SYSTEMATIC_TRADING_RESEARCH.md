# Experimental branch: systematic-trading research

**Branch:** `test/systematic-trading-research`, branched from `master` on 2026-11-21.

## Purpose

Evaluate useful methods referenced by [awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading) without replacing the existing OKX collector, changing live publications, or implying that any strategy is profitable.

## What is implemented now (P0)

- `research/market_history_audit.py`: takes one or more archived `market_data*.json` snapshots, validates their closed-candle OHLCV and timestamps, removes exact duplicates, **excludes conflicting candle versions** instead of silently selecting one, and detects gaps per symbol/timeframe.
- Emits a machine-readable integrity report and optionally normalized JSONL. Each candle records `first_seen_utc` so availability at observation time remains traceable.
- `tests/test_market_history_audit.py`: deterministic synthetic tests for the integrity rules.
- Isolated GitHub Actions workflow that runs only these tests. It does not collect market data, publish anything, or execute orders.

**This is data-integrity infrastructure, not a profitability backtest.** Even an integrity-passing output may lack sufficient historical depth, historic funding, spread, order-book data or robust sample coverage.

## Reconstructing historical snapshots locally

From a local clone, fetch the market-data branch without changing its contents:

```bash
git fetch origin market-data
mkdir -p snapshots
for sha in $(git rev-list --max-count=300 origin/market-data); do
  git show "$sha:market_data.json" > "snapshots/market_data_$sha.json" \
    || rm -f "snapshots/market_data_$sha.json"
done
python -m research.market_history_audit snapshots \
  --report reports/data_integrity.json \
  --normalized reports/normalized_candles.jsonl \
  --strict
```

`--strict` returns exit status 2 when the reconstructed history is incomplete or inconsistent. Gaps between publication commits do not necessarily imply candle gaps because snapshots overlap. Gaps are counted from actual candle timestamps, not commit times.

## Libraries to evaluate, not install indiscriminately

| Order | Reference | Proposed experiment | Acceptance criterion |
|---|---|---|---|
| 1 | DuckDB / Parquet | Faster storage/query of normalized candle history | Same records and audit outputs, measured runtime/size advantage |
| 2 | Freqtrade | Independent reference for long/short strategy backtests | Execution rules matched and discrepancies reconciled |
| 3 | PyBroker | Walk-forward validation and bootstrap tests | Chronological split without leakage and stable evaluation |
| 4 | QuantStats | Standard performance reports | Metrics validated against independent unit calculations |
| 5 | vectorbt | Vectorized candidate screening | Confirmed candidates equivalent to baseline on frozen fixture |
| 6 | NautilusTrader | More detailed event/order simulation | Evidence that fidelity gain justifies integration complexity |

No new dependency has been added in this cycle. Each external project requires individual license, version, data-model and future-leakage checks before use.

## Next experiments

1. Reconstruct as many historical OKX candles as possible, with timestamp-keyed duplicates and conflict resolution.
2. Build a realistic backtester (execution after closed candle; fee, slippage, spread, funding, leverage/margin, liquidation approximations and ambiguous intrabar handling).
3. Compare breakout vs retest independently, first without filters, then by individual ablation; preserve rejected candidates for a fair comparison.
4. Use chronological train/validation/test and walk-forward, including multiple symbols and volatility regimes. Report sample size, expectancy per trade, drawdown and cost sensitivity. Do not select a strategy merely by ROI.

**Production safety:** `master` and `market-data` are not modified; no secrets, exchange orders or real financial operations.
