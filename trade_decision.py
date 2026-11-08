#!/usr/bin/env python3
"""Motor de decisao V2 experimental: NAO envia ordens nem recomenda entrada imediata.

Entradas apenas condicionais a preco atual, liquidez e risco. Um ativo jamais e
excluido por indicadores: sempre gera um plano LONG e um plano SHORT.
"""
from __future__ import annotations
import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from experimental_scan import features

MIN_NET_RR = 1.6
MAX_SPREAD_PCT = .15
MAX_EXTENSION_ATR = .9
MAX_RISK_ATR = 3.
FEE_BPS_EACH_SIDE = 5.
SLIPPAGE_BPS_EACH_SIDE = 5.


def pretty(num):
    # Nao arredondar para 8 casas: tokens baratos perderiam precisao.
    return float(f"{num:.12g}")


def median_volume(rows):
    return statistics.median(float(x[6]) for x in rows)


def candle_quality(candle, side):
    o, h, l, c = map(float, candle[1:5])
    if h <= l:
        return False
    body = abs(c - o) / (h - l)
    close_pos = (c - l) / (h - l)
    return (c > o and body >= .40 and close_pos >= .65) if side == "LONG" else (
        c < o and body >= .40 and close_pos <= .35)


def setup(history, info, side):
    bars = history["15m"]
    current, prev = bars[-1], bars[-2]
    atr = info["atr_15m"]
    positive = side == "LONG"
    base = bars[-21:-1]  # nao inclui o candle atual
    oldbase = bars[-22:-2]  # nao inclui o candle anterior
    level = max(float(c[2]) for c in base) if positive else min(float(c[3]) for c in base)
    oldlevel = max(float(c[2]) for c in oldbase) if positive else min(float(c[3]) for c in oldbase)
    close = float(current[4])
    ratio = float(current[6]) / median_volume(base) if median_volume(base) > 0 else 0
    prev_ratio = float(prev[6]) / median_volume(oldbase) if median_volume(oldbase) > 0 else 0
    threshold = .12*atr
    confirmed_break = (close > level+threshold if positive else close < level-threshold)
    confirmed_break = confirmed_break and candle_quality(current, side) and ratio >= 1.4
    prev_break = (float(prev[4]) > oldlevel+threshold if positive else
                  float(prev[4]) < oldlevel-threshold)
    prev_break = prev_break and candle_quality(prev, side) and prev_ratio >= 1.4
    retest = (prev_break and ratio >= .65 and (
        (float(current[3]) <= oldlevel+.30*atr and close > oldlevel+.05*atr
         and close >= float(current[1])) if positive else
        (float(current[2]) >= oldlevel-.30*atr and close < oldlevel-.05*atr
         and close <= float(current[1]))))
    if retest:
        return "RETESTE_CONFIRMADO", oldlevel, ratio
    if confirmed_break:
        return "AGUARDAR_RETESTE", level, ratio
    dist = (level-close)/atr if positive else (close-level)/atr
    # Estrategia distinta: reacao no suporte / rejeicao na resistencia.
    opposite_level = (min(float(c[3]) for c in base) if positive
                      else max(float(c[2]) for c in base))
    dist_opposite = (close-opposite_level)/atr if positive else (
        opposite_level-close)/atr
    touched = (float(current[3]) <= opposite_level+.25*atr if positive else
               float(current[2]) >= opposite_level-.25*atr)
    recovered = (close >= opposite_level+.06*atr if positive else
                 close <= opposite_level-.06*atr)
    reaction = (touched and recovered and candle_quality(current, side)
                and ratio >= 1.2 and -.10 <= dist_opposite <= .65)
    if reaction:
        return ("REACAO_CONFIRMADA" if positive else "REJEICAO_CONFIRMADA"), opposite_level, ratio
    if -.10 <= dist <= 1:
        return "PREPARANDO", level, ratio
    if -.10 <= dist_opposite <= 1:
        return ("PRE_REACAO" if positive else "PRE_REJEICAO"), opposite_level, ratio
    # Nao confundir preco alem do nivel com rompimento validado.
    if (close > level if positive else close < level):
        return "ROMPIMENTO_FRACO", level, ratio
    return "MONITORANDO", level, ratio


def levels(history, info, side, level, spread_pct):
    positive = side == "LONG"
    atr = info["atr_15m"]
    zone = (level+.05*atr, level+.45*atr) if positive else (
        level-.45*atr, level-.05*atr)
    worst = max(zone) if positive else min(zone)
    recent = history["15m"][-3:]
    stop = min(level-.4*atr, min(float(c[3]) for c in recent)-.15*atr) if positive else (
        max(level+.4*atr, max(float(c[2]) for c in recent)+.15*atr))
    risk = worst-stop if positive else stop-worst
    if risk <= 0:
        raise ValueError("entrada/stop inconsistentes")
    tp1 = worst + (1 if positive else -1) * 2.2*risk
    tp2 = worst + (1 if positive else -1) * 3.0*risk
    costs = worst*((2*FEE_BPS_EACH_SIDE + 2*SLIPPAGE_BPS_EACH_SIDE)/10000
                   + spread_pct/100)
    net_rr = (2.2*risk-costs)/(risk+costs)
    obstacles = []
    for tf in ("1h", "2h"):
        for c in history[tf][-21:-1]:
            p = float(c[2] if positive else c[3])
            if (worst < p < tp1) if positive else (tp1 < p < worst):
                obstacles.append((p, tf))
    closest = (min(obstacles) if positive else max(obstacles)) if obstacles else None
    return {
        "entry_zone": [pretty(min(zone)), pretty(max(zone))],
        "rr_worst_entry": pretty(worst),
        "stop": pretty(stop),
        "tp1_2_2R_projection": pretty(tp1),
        "tp2_3_0R_projection": pretty(tp2),
        "risk_ATR": pretty(risk/atr),
        "estimated_net_RR_tp1": pretty(net_rr),
        "estimated_cost_pct_roundtrip": pretty(costs/worst*100),
        "obstacle_before_tp1": {"price": pretty(closest[0]), "tf": closest[1]} if closest else None,
        "note": "Alvos projetados, nao suportes garantidos; ajuste tick-size antes de usar.",
    }


def watch_fields(side, stage, level, close, atr, decision, stale, plan):
    """Valores para radar. Nunca implicam entrada pelo simples toque do preco.

    rompimento: precisa candle 15m fechado alem de 0.12 ATR, corpo e volume;
    reacao/rejeicao: requer toque, fechamento de retorno, corpo, volume e
    tendencia validos; reteste exige novo candle fechado confirmando suporte.
    """
    positive = side == "LONG"
    breakout_trigger = level + (.12*atr if positive else -.12*atr)
    is_reaction = stage in (
        "PRE_REACAO", "PRE_REJEICAO", "REACAO_CONFIRMADA", "REJEICAO_CONFIRMADA"
    )
    is_retest = stage in ("AGUARDAR_RETESTE", "RETESTE_CONFIRMADO")
    is_weak = stage == "ROMPIMENTO_FRACO"
    if is_reaction:
        trigger = level + (.06*atr if positive else -.06*atr)
        condition = (
            "15m tocar suporte e fechar acima dele com candle comprador, "
            "volume relativo >= 1.2 e tendencia de 1h alinhada"
            if positive else
            "15m tocar resistencia e fechar abaixo dela com candle vendedor, "
            "volume relativo >= 1.2 e tendencia de 1h alinhada"
        )
        dist_atr = abs(close - level) / atr
        alert_type = "PROXIMO_SUPORTE" if positive else "PROXIMO_RESISTENCIA"
        within = dist_atr <= .5
    elif is_retest:
        trigger = level
        condition = (
            "Aguardar novo fechamento 15m apos reteste com nivel sustentado, "
            "volume e risco-retorno adequados; recalcular SL/TP"
        )
        dist_atr = abs(close - level) / atr
        alert_type = "PROXIMO_RETESTE"
        within = dist_atr <= .5
    else:
        trigger = breakout_trigger
        condition = (
            f"Fechamento de 15m {'acima' if positive else 'abaixo'} de "
            f"{pretty(trigger)} com corpo >= 40%, volume relativo >= 1.4 "
            "e posterior reteste fechado; rompimento isolado nao e entrada"
        )
        directional_distance = ((trigger-close)/atr if positive else
                                (close-trigger)/atr)
        dist_atr = abs(directional_distance)
        alert_type = "PROXIMO_ROMPIMENTO"
        within = 0 <= directional_distance <= .5
    if stage == "MONITORANDO":
        # Monitorar todos os ativos, mas alertar apenas quando uma regiao
        # com formacao de setup estiver explicitamente classificada.
        within = False
    if is_weak:
        within = False
        alert_type = "ROMPIMENTO_SEM_CONFIRMACAO"
    if stale:
        within = False
        alert_type = "DADOS_DESATUALIZADOS"
    invalidation = (plan["stop"] if plan else
                    level - .4*atr if positive else level + .4*atr)
    alert = bool(within)
    return {
        "monitor_price": pretty(level),
        "confirmation_close_price": pretty(trigger),
        "confirmation_rule": condition,
        "distance_to_watch_level_atr": pretty(abs(close-level)/atr),
        "distance_to_trigger_atr": pretty(dist_atr),
        "near_trigger_0_5_atr": alert,
        "alert_type": alert_type if alert else None,
        "alert_message": (
            "Proximo ao gatilho, mas NAO entrar: aguardando confirmacao "
            "ou com plano de risco reprovado" if alert and decision !=
            "ENTRADA_CONDICIONAL" else
            "Setup confirmado por candle; revalidar preco de mercado antes "
            "de entrar" if alert else None
        ),
        "last_snapshot_required": True,
        "invalid_if_15m_closes_beyond": pretty(invalidation),
        "entry_zone_if_confirmed": plan["entry_zone"] if plan else None,
        "stop_if_confirmed": plan["stop"] if plan else None,
        "tp1_if_confirmed": plan["tp1_2_2R_projection"] if plan else None,
        "tp2_if_confirmed": plan["tp2_3_0R_projection"] if plan else None,
        "estimated_net_rr": plan["estimated_net_RR_tp1"] if plan else None,
        "signal_is_actionable_now": bool(decision == "ENTRADA_CONDICIONAL" and not stale),
        "note": "Preco de observacao nao e ordem; somente candles fechados.",
    }


def evaluate(symbol, history, ticker, side, info, stale):
    stage, level, volume = setup(history, info, side)
    spread = float(ticker.get("spread_pct", 0))
    opposite = "baixa" if side == "LONG" else "alta"
    close = float(history["15m"][-1][4])
    why = []
    trade = None
    decision = "AGUARDAR"
    if stage != "MONITORANDO":
        trade = levels(history, info, side, level, spread)
        if trade["risk_ATR"] > MAX_RISK_ATR:
            why.append("stop muito distante: risco > 3 ATR")
        if trade["estimated_net_RR_tp1"] < MIN_NET_RR:
            why.append("R:R estimado liquido < 1,6 na pior entrada")
        if trade["obstacle_before_tp1"] is not None:
            why.append("obstaculo estrutural antes de TP1 projetado")
    if spread > MAX_SPREAD_PCT:
        why.append("spread elevado")
    if stage in ("RETESTE_CONFIRMADO", "REACAO_CONFIRMADA", "REJEICAO_CONFIRMADA"):
        aligned = "alta" if side == "LONG" else "baixa"
        if info["trend_1h"] == opposite:
            why.append("contra tendencia 1h; estrutura de reversao insuficiente")
        if stage != "RETESTE_CONFIRMADO" and info["trend_1h"] != aligned:
            why.append("reacao sem tendencia de 1h alinhada")
        if info["trend_2h"] == opposite:
            why.append("contra tendencia 2h; exige confirmacao adicional")
        if trade and not trade["entry_zone"][0] <= close <= trade["entry_zone"][1]:
            why.append("fechamento fora da faixa: evitar perseguir o preco")
        if stale:
            why.append("coleta desatualizada: revalidacao obrigatoria")
        decision = "NAO_ENTRAR" if why else "ENTRADA_CONDICIONAL"
    elif stage == "AGUARDAR_RETESTE":
        if abs(close-level)/info["atr_15m"] > MAX_EXTENSION_ATR:
            why.append("rompimento estendido > 0,9 ATR: nao perseguir")
            decision = "NAO_ENTRAR"
        else:
            why.append("rompimento sem reteste fechado")
    elif stage in ("PREPARANDO", "PRE_REACAO", "PRE_REJEICAO"):
        why.append("proximidade da regiao nao e confirmacao")
    elif stage == "ROMPIMENTO_FRACO":
        why.append("preco passou do nivel, mas candle/volume nao confirmaram")
        decision = "NAO_ENTRAR"
    else:
        why.append("sem gatilho operacional")
    # "Nao entrar" e apenas uma decisao para este snapshot, nao exclui o ativo.
    blocked = ("stop muito distante", "R:R estimado liquido", "obstaculo estrutural",
               "spread elevado")
    if stage != "MONITORANDO" and any(
            reason.startswith(blocked) for reason in why):
        decision = "NAO_ENTRAR"
    if stale and stage != "MONITORANDO":
        decision = "NAO_ENTRAR"
        why.append("snapshot desatualizado ou com horario inconsistente")
    return {
        "symbol": symbol, "side": side, "stage": stage, "decision": decision,
        "reference_level": pretty(level),
        "close_15m": pretty(close),
        "last_15m_close_utc": datetime.fromtimestamp(
            (int(history["15m"][-1][0])+900000)/1000, timezone.utc).isoformat(),
        "relative_volume_15m": pretty(volume),
        "trend_2h": info["trend_2h"],
        "trend_1h": info["trend_1h"],
        "structure_1h": info["structure_1h"],
        "trade_plan": trade, "reasons": why,
        "watch": watch_fields(side, stage, level, close, info["atr_15m"],
                              decision, stale, trade),
        "live_price_reconfirmation_required": True,
        "order_allowed": False,
    }


def analyze(market, now_utc=None):
    if market.get("market_type") != "USDT_PERPETUAL":
        raise ValueError("mercado inesperado")
    now = now_utc or datetime.now(timezone.utc)
    stamp = datetime.fromisoformat(market["collected_utc"].replace("Z", "+00:00"))
    age = (now-stamp).total_seconds()/60
    stale = not (-2 <= age <= 30)
    assets, ranked = [], []
    for ticker in market["universe"]:
        symbol = ticker["symbol"]
        try:
            history = market["candles"][symbol]
            info = features(history)
            plans = [evaluate(symbol, history, ticker, side, info, stale)
                     for side in ("LONG", "SHORT")]
            assets.append({"symbol": symbol, "status": "ANALISADO", "plans": plans})
            ranked.extend(plans)
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
            assets.append({"symbol": symbol, "status": "DADOS_INSUFICIENTES",
                           "error": str(exc), "plans": []})
    ranked.sort(key=lambda s: (
        {"ENTRADA_CONDICIONAL": 0, "AGUARDAR": 1, "NAO_ENTRAR": 2}[s["decision"]],
        {"RETESTE_CONFIRMADO": 0, "REACAO_CONFIRMADA": 0,
         "REJEICAO_CONFIRMADA": 0, "AGUARDAR_RETESTE": 1,
         "PREPARANDO": 2, "PRE_REACAO": 2, "PRE_REJEICAO": 2,
         "ROMPIMENTO_FRACO": 3, "MONITORANDO": 4}[s["stage"]],
        -s["relative_volume_15m"]))
    return {
        "schema": 1, "status": "EXPERIMENTAL", "order_allowed": False,
        "source_collected_utc": market["collected_utc"],
        "evaluated_at_utc": now.isoformat(), "snapshot_age_min": pretty(age),
        "snapshot_stale": stale, "source_asset_count": len(market["universe"]),
        "retained_asset_count": len(assets),
        "analyzed_asset_count": sum(a["status"] == "ANALISADO" for a in assets),
        "ranked_setups": ranked, "assets": assets,
        "proximity_alerts": [
            { "symbol": p["symbol"], "side": p["side"], "stage": p["stage"],
              "decision": p["decision"], "watch": p["watch"],
              "close_15m": p["close_15m"], "reasons": p["reasons"] }
            for p in ranked if p["watch"]["near_trigger_0_5_atr"]
        ],
        "proximity_alert_count": sum(
            p["watch"]["near_trigger_0_5_atr"] for p in ranked),
        "assumptions": {"min_net_rr": MIN_NET_RR,
                        "fee_bps_each_side": FEE_BPS_EACH_SIDE,
                        "slippage_bps_each_side": SLIPPAGE_BPS_EACH_SIDE},
    }


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--market-data", default="market_data.json")
    cli.add_argument("--output", default="experimental_decisions.json")
    args = cli.parse_args()
    data = json.loads(Path(args.market_data).read_text(encoding="utf-8"))
    out = analyze(data)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False,
                                           indent=2), encoding="utf-8")
    print("DECISION V2:", out["source_collected_utc"],
          "retained", out["retained_asset_count"],
          "analyzed", out["analyzed_asset_count"],
          "stale", out["snapshot_stale"])
    for p in out["ranked_setups"]:
        if p["stage"] != "MONITORANDO":
            print(p["symbol"], p["side"], p["stage"], p["decision"],
                  p["trade_plan"]["estimated_net_RR_tp1"] if p["trade_plan"] else None)


if __name__ == "__main__":
    main()
