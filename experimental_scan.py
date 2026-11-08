#!/usr/bin/env python3
"""Scanner V2 experimental: classifica todos os ativos ja coletados.

Nao coleta dados, nao gera ordens nem recomenda entradas.
Nao filtra mercados por indicadores. Nao altera o pipeline existente.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

TIMEFRAMES = ("2h", "1h", "15m")


def mean(values):
    return statistics.mean(values)


def trend_sma(rows):
    if len(rows) < 35:
        return "indisponivel"
    closes = [float(row[4]) for row in rows]
    now = mean(closes[-20:])
    prior = mean(closes[-25:-5])
    if closes[-1] > now and now > prior:
        return "alta"
    if closes[-1] < now and now < prior:
        return "baixa"
    return "mista"


def swing_structure(rows, wing=2):
    """Usa apenas pivots confirmados (sem olhar candles futuros)."""
    highs, lows = [], []
    for i in range(wing, len(rows) - wing):
        if all(rows[i][2] > rows[j][2]
               for j in range(i-wing, i+wing+1) if j != i):
            highs.append(float(rows[i][2]))
        if all(rows[i][3] < rows[j][3]
               for j in range(i-wing, i+wing+1) if j != i):
            lows.append(float(rows[i][3]))
    if len(highs) < 2 or len(lows) < 2:
        return "indefinida"
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return "alta"
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return "baixa"
    return "mista"


def atr14(rows):
    if len(rows) < 15:
        raise ValueError("historico insuficiente para ATR14")
    true_ranges = []
    for i in range(len(rows)-14, len(rows)):
        high, low, prev_close = (float(rows[i][2]), float(rows[i][3]),
                                 float(rows[i-1][4]))
        true_ranges.append(max(high-low, abs(high-prev_close),
                               abs(low-prev_close)))
    atr = mean(true_ranges)
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("ATR invalido")
    return atr


def features(history):
    for tf in TIMEFRAMES:
        if tf not in history or len(history[tf]) < 35:
            raise ValueError(f"{tf} ausente ou com historico insuficiente")
    m = history["15m"]
    for tf in TIMEFRAMES:
        times = [int(c[0]) for c in history[tf]]
        if len(times) != len(set(times)) or times != sorted(times):
            raise ValueError(f"{tf} com timestamps duplicados ou fora de ordem")
        for row in history[tf]:
            if len(row) < 7 or not all(math.isfinite(float(v)) for v in row[:7]):
                raise ValueError(f"{tf} contem candle invalido")
            o, hi, lo, cl = map(float, row[1:5])
            if not (0 < lo <= min(o, cl) <= max(o, cl) <= hi):
                raise ValueError(f"{tf} contem OHLC inconsistente")
    last = m[-1]
    prev20 = m[-21:-1]
    close = float(last[4])
    open_ = float(last[1])
    atr = atr14(m)
    resistance = max(float(c[2]) for c in prev20)
    support = min(float(c[3]) for c in prev20)
    median_vol = statistics.median(float(c[6]) for c in prev20)
    rel_vol = float(last[6]) / median_vol if median_vol > 0 else 0.0
    return {
        "close": close,
        "open": open_,
        "atr_15m": atr,
        "support_15m": support,
        "resistance_15m": resistance,
        "dist_to_res_atr": (resistance - close) / atr,
        "dist_to_sup_atr": (close - support) / atr,
        "relative_volume_15m": rel_vol,
        "trend_2h": trend_sma(history["2h"]),
        "trend_1h": trend_sma(history["1h"]),
        "structure_1h": swing_structure(history["1h"]),
    }


def classify(info, side):
    close = info["close"]
    atr = info["atr_15m"]
    vol = info["relative_volume_15m"]
    trend1, trend2 = info["trend_1h"], info["trend_2h"]
    aligned = "alta" if side == "LONG" else "baixa"
    opposite = "baixa" if side == "LONG" else "alta"
    near_r = -0.1 <= info["dist_to_res_atr"] <= 1.0
    near_s = -0.1 <= info["dist_to_sup_atr"] <= 1.0

    if side == "LONG":
        breakout = (close > info["resistance_15m"] + atr*0.1
                    and close > info["open"] and vol >= 1.5)
        if breakout:
            stage, why = "rompimento_em_observacao", "15m acima de resistencia; corpo e volume favoraveis"
        elif near_r:
            stage, why = "pre_rompimento", "15m proximo da resistencia"
        elif near_s:
            stage, why = "pre_reacao", "15m proximo do suporte"
        else:
            stage, why = "monitorando", "sem gatilho de proximidade"
    else:
        breakout = (close < info["support_15m"] - atr*0.1
                    and close < info["open"] and vol >= 1.5)
        if breakout:
            stage, why = "rompimento_em_observacao", "15m abaixo do suporte; corpo e volume favoraveis"
        elif near_s:
            stage, why = "pre_rompimento", "15m proximo do suporte"
        elif near_r:
            stage, why = "pre_rejeicao", "15m proximo da resistencia"
        else:
            stage, why = "monitorando", "sem gatilho de proximidade"

    weight = {
        "rompimento_em_observacao": 50,
        "pre_rompimento": 32,
        "pre_reacao": 26,
        "pre_rejeicao": 26,
        "monitorando": 0,
    }[stage]
    score = (weight + min(vol, 4.0)*2
             + (7 if trend1 == aligned else -7 if trend1 == opposite else 0)
             + (4 if trend2 == aligned else -4 if trend2 == opposite else 0))
    return {
        "side": side,
        "stage": stage,
        "score_experimental": round(score, 2),
        "contexto_1h": trend1,
        "contexto_2h": trend2,
        "contra_tendencia": trend1 == opposite or trend2 == opposite,
        "motivo": why,
        "trade_signal": False,
    }


def analyze(data, early_warning=None):
    if data.get("market_type") != "USDT_PERPETUAL":
        raise ValueError("mercado de origem inesperado")
    universe = data.get("universe", [])
    candles = data.get("candles", {})
    source_stamp = data.get("collected_utc")
    valid_warning = (isinstance(early_warning, dict)
                     and early_warning.get("source_collected_utc") == source_stamp)
    warns = {w["symbol"]: w for w in (early_warning or {}).get("warnings", [])
             if isinstance(w, dict) and "symbol" in w} if valid_warning else {}

    assets = []
    ranked = []
    for item in universe:
        symbol = item["symbol"]
        try:
            if symbol not in candles:
                raise ValueError("candles ausentes")
            data_features = features(candles[symbol])
            plans = [classify(data_features, side) for side in ("LONG", "SHORT")]
            entry = {
                "symbol": symbol,
                "status": "analisado",
                "features": {k: round(v, 8) if isinstance(v, float) else v
                             for k, v in data_features.items()},
                "setups": plans,
                "alerta_5m_original": warns.get(symbol),
            }
            for setup in plans:
                ranked.append({
                    "symbol": symbol,
                    **setup,
                    "close_15m": round(data_features["close"], 8),
                })
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
            entry = {"symbol": symbol, "status": "dados_indisponiveis",
                     "motivo": str(exc), "setups": []}
        assets.append(entry)

    ranked.sort(key=lambda s: s["score_experimental"], reverse=True)
    analyzed = sum(a["status"] == "analisado" for a in assets)
    assert len(assets) == len(universe), "Exclusao inesperada"
    return {
        "status": "EXPERIMENTAL",
        "trade_signals_allowed": False,
        "source_collected_utc": source_stamp,
        "source_asset_count": len(universe),
        "retained_asset_count": len(assets),
        "analyzed_asset_count": analyzed,
        "unavailable_asset_count": len(assets)-analyzed,
        "ranking_note": "Ranking informativo, nao representa sinais confirmados, SL ou TP",
        "early_warning_integrated": valid_warning,
        "ranked_setups": ranked,
        "assets": assets,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", default="output/market_data.json")
    parser.add_argument("--early-warning", default="output/early_warning.json")
    parser.add_argument("--output", default="output/experimental_rank.json")
    args = parser.parse_args()
    source = json.loads(Path(args.market_data).read_text(encoding="utf-8"))
    warning_path = Path(args.early_warning)
    warnings = json.loads(warning_path.read_text(encoding="utf-8")) if warning_path.is_file() else None
    result = analyze(source, warnings)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SCANNER V2 EXPERIMENTAL",
          f"origem={result['source_asset_count']}",
          f"retidos={result['retained_asset_count']}",
          f"analisados={result['analyzed_asset_count']}",
          f"indisponiveis={result['unavailable_asset_count']}")
    for item in result["ranked_setups"][:10]:
        print(item["symbol"], item["side"], item["stage"], item["score_experimental"])


if __name__ == "__main__":
    main()
