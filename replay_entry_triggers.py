"""Replay exploratorio do motor de entradas, sem look-ahead em 1h/2h.

Usa historico curto do snapshot atual para verificar frequencia dos sinais.
NAO e backtest de rentabilidade nem incorpora dados de book historico.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from entry_trigger import analyze_both
from experimental_scan import features

DURATIONS = {"15m": 900000, "1h": 3600000, "2h": 7200000}


def aligned_history(history, close_ms, index):
    result = {"15m": history["15m"][:index+1]}
    for tf in ("1h", "2h"):
        duration = DURATIONS[tf]
        result[tf] = [r for r in history[tf]
                      if int(r[0]) + duration <= close_ms]
    if any(len(v) < 35 for v in result.values()):
        return None
    return result


def replay(market):
    report = {
        "status": "EXPERIMENTAL_REPLAY", "source_collected_utc": market["collected_utc"],
        "timeframe": "15m_closed_only", "no_lookahead": True,
        "historical_tick_size_assumed_from_current_metadata": True,
        "historical_spread_assumed_from_current_snapshot": True,
        "not_a_profitability_backtest": True, "asset_count": len(market["universe"]),
        "samples_checked": 0, "confirmed": 0, "conditional": 0, "blocked": 0,
        "unconfirmed": 0, "missing_tick_sz": 0, "reason_counts": {},
        "confirmed_examples": [], "per_asset": [],
    }
    reason_counts = Counter()
    for ticker in market["universe"]:
        symbol = ticker["symbol"]
        rows = market["candles"].get(symbol, {})
        tf15 = rows.get("15m", [])
        local = Counter()
        for i in range(34, len(tf15)):
            close_ms = int(tf15[i][0]) + DURATIONS["15m"]
            hist = aligned_history(rows, close_ms, i)
            if hist is None:
                continue
            try:
                info = features(hist)
            except (ValueError, IndexError):
                continue
            as_of = datetime.fromtimestamp((close_ms + 60000)/1000,
                                           tz=timezone.utc)
            historical_ticker = dict(ticker)
            historical_ticker["price"] = float(hist["15m"][-1][4])
            for side in ("LONG", "SHORT"):
                for method in analyze_both(hist, info, historical_ticker,
                                           side, as_of, False):
                    report["samples_checked"] += 1
                    if method["status"] != "CONFIRMADO":
                        report["unconfirmed"] += 1
                        continue
                    report["confirmed"] += 1
                    local[side + " " + method["method"]] += 1
                    if method["decision"] == "GATILHO_CONDICIONAL":
                        report["conditional"] += 1
                    else:
                        report["blocked"] += 1
                    for reason in method["reasons"]:
                        reason_counts[reason] += 1
                    if method["entry_trigger_price"] is None:
                        report["missing_tick_sz"] += 1
                    report["confirmed_examples"].append({
                        "symbol": symbol, "side": side, "method": method["method"],
                        "candle_close_utc": method["candle_close_utc"],
                        "decision": method["decision"],
                        "entry": method["entry_trigger_price"],
                        "stop": method["stop_loss"],
                        "tp1": method["tp1_projected"],
                        "rr": method["estimated_net_rr_tp1"],
                        "reasons": method["reasons"],
                    })
        report["per_asset"].append({"symbol": symbol, "confirmed": dict(local)})
    report["reason_counts"] = dict(reason_counts.most_common())
    report["confirmed_examples"].sort(key=lambda x: x["candle_close_utc"])
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market-data", default="market_data.json")
    p.add_argument("--output", default="experimental_replay.json")
    args = p.parse_args()
    data = json.loads(Path(args.market_data).read_text(encoding="utf-8"))
    r = replay(data)
    Path(args.output).write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print("REPLAY:", r["asset_count"], "assets,", r["samples_checked"],
          "evaluations,", r["confirmed"], "confirmations,",
          r["conditional"], "conditional,",
          r["blocked"], "blocked")


if __name__ == "__main__":
    main()
