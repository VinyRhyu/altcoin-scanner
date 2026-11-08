"""Gatilhos de entrada independentes: rompimento e reteste (somente simulacao).

Um candle de 15m fechado CONFIRMA um setup; seu high/low +/- um tick
define um preco STOP condicional, nao uma ordem executada. Sem tickSz
oficial da OKX, jamais inventar um preco negociavel.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, InvalidOperation
import math
import statistics

MIN_RR = 1.6
MAX_RISK_ATR = 3.0
MAX_TRIGGER_DISTANCE_ATR = 0.9
MAX_SPREAD_PCT = 0.15
FEE_BPS_PER_SIDE = 5.0
SLIPPAGE_BPS_PER_SIDE = 5.0


def numeric(value):
    return float(f"{float(value):.12g}")


def tick_decimal(tick_size):
    try:
        tick = Decimal(str(tick_size))
        if not tick.is_finite() or tick <= 0:
            return None
        return tick
    except (InvalidOperation, ValueError, TypeError):
        return None


def tick_round(value, tick, direction):
    fraction = Decimal(str(value)) / tick
    mode = ROUND_CEILING if direction == "up" else ROUND_FLOOR
    quantized = fraction.to_integral_value(rounding=mode) * tick
    return float(quantized)


def quality(bar, side, min_body=0.4):
    o, h, l, c = map(float, bar[1:5])
    if not (0 < l <= min(o, c) <= max(o, c) <= h) or h <= l:
        return False
    body = abs(c-o)/(h-l)
    location = (c-l)/(h-l)
    return (c > o and body >= min_body and location >= 0.65
            if side == "LONG" else
            c < o and body >= min_body and location <= 0.35)


def relative_volume(bars, index):
    sample = bars[index-20:index]
    median = statistics.median(float(bar[6]) for bar in sample)
    return float(bars[index][6])/median if median > 0 else 0.0


def directional_close(bar, level, atr, side):
    c = float(bar[4])
    return c > level+.12*atr if side == "LONG" else c < level-.12*atr


def is_breakout(bars, index, side, atr):
    if index < 21:
        return False, None
    previous = bars[index-20:index]
    level = (max(float(r[2]) for r in previous) if side == "LONG"
             else min(float(r[3]) for r in previous))
    passed = (directional_close(bars[index], level, atr, side)
              and quality(bars[index], side)
              and relative_volume(bars, index) >= 1.4)
    return passed, level


def is_retest(bars, side, atr):
    prev_passed, level = is_breakout(bars, len(bars)-2, side, atr)
    if not prev_passed:
        return False, level
    last = bars[-1]
    o, hi, lo, close = map(float, last[1:5])
    vol = relative_volume(bars, len(bars)-1)
    if side == "LONG":
        touched = lo <= level + .3*atr
        reclaimed = close > level+.05*atr and close >= o
    else:
        touched = hi >= level - .3*atr
        reclaimed = close < level-.05*atr and close <= o
    return bool(touched and reclaimed and quality(last, side, .20)
                and vol >= .65), level


def confirmed_pivots(history, side):
    # Pivots confirmados com 2 candles posteriores, sem look-ahead no ultimo candle.
    extreme_index = 2 if side == "LONG" else 3
    result = []
    for tf in ("1h", "2h"):
        bars = history[tf][-45:]
        for i in range(2, len(bars)-2):
            price = float(bars[i][extreme_index])
            neighbors = [float(bars[j][extreme_index])
                         for j in (i-2, i-1, i+1, i+2)]
            if side == "LONG" and all(price > v for v in neighbors):
                result.append((price, tf))
            if side == "SHORT" and all(price < v for v in neighbors):
                result.append((price, tf))
    return result


def projection(history, side, price, stop, tick, spread_pct):
    long = side == "LONG"
    risk = price-stop if long else stop-price
    if risk <= 0 or not math.isfinite(risk):
        raise ValueError("Stop invalido")
    sign = 1 if long else -1
    tp1 = tick_round(price + sign*2.2*risk, tick, "down" if long else "up")
    tp2 = tick_round(price + sign*3.0*risk, tick, "down" if long else "up")
    if not (0 < tp1 and 0 < tp2):
        raise ValueError("TP invalido para o preco")
    reward = sign*(tp1-price)
    # Simulacao conservadora: fee taker e slippage entrada/saida e spread.
    estimated_cost = price * ((2*FEE_BPS_PER_SIDE +
                                2*SLIPPAGE_BPS_PER_SIDE)/10000
                               + max(0.0, spread_pct)/100)
    net_rr = (reward-estimated_cost)/(risk+estimated_cost)
    barriers = [(p, tf) for p,tf in confirmed_pivots(history, side)
                if ((price < p < tp1) if long else (tp1 < p < price))]
    barrier = ((min(barriers) if long else max(barriers))
               if barriers else None)
    return {
        "stop_loss": numeric(stop),
        "tp1_projected": numeric(tp1),
        "tp2_projected": numeric(tp2),
        "estimated_net_rr_tp1": numeric(net_rr),
        "risk_price": numeric(risk),
        "roundtrip_cost_estimate_pct": numeric(100*estimated_cost/price),
        "pivot_obstacle_before_tp1": ({"price": numeric(barrier[0]),
                                      "timeframe": barrier[1]}
                                     if barrier else None),
        "targets_are_projections": True,
    }


def analyze_method(history, info, ticker, side, method, now_utc, stale):
    if side not in ("LONG", "SHORT") or method not in ("BREAKOUT", "RETEST"):
        raise ValueError("Metodo/direcao invalidos")
    bars = history["15m"]
    last = bars[-1]
    atr = float(info["atr_15m"])
    long = side == "LONG"
    close = float(last[4])
    spread = float(ticker.get("spread_pct", 0))
    price_now = float(ticker.get("price", close))
    tick = tick_decimal(ticker.get("tick_size"))
    breakout, breakout_level = is_breakout(bars, len(bars)-1, side, atr)
    retest, retest_level = is_retest(bars, side, atr)
    confirmed = breakout if method == "BREAKOUT" else retest
    level = breakout_level if method == "BREAKOUT" else retest_level
    source_close_ms = int(last[0])+900000
    expires_ms = source_close_ms+900000
    expired = now_utc.timestamp()*1000 >= expires_ms
    signal_time = datetime.fromtimestamp(source_close_ms/1000, timezone.utc).isoformat()
    expiry_time = datetime.fromtimestamp(expires_ms/1000, timezone.utc).isoformat()
    result = {
        "method": method, "side": side,
        "status": "CONFIRMADO" if confirmed else "AGUARDANDO_CONFIRMACAO",
        "decision": "AGUARDAR",
        "watch_price": numeric(level) if level is not None else None,
        "confirmation_close_price": numeric(level + (.12*atr if long else -.12*atr))
            if level is not None else None,
        "candle_close_utc": signal_time,
        "trigger_expires_utc": expiry_time,
        "tick_size": str(tick) if tick is not None else None,
        "entry_trigger_price": None, "stop_loss": None,
        "entry_state": "AGUARDANDO_CONFIRMACAO",
        "ticker_price_at_snapshot": numeric(price_now),
        "trigger_crossed_in_snapshot": False,
        "entry_eligible_at_snapshot": False,
        "tp1_projected": None, "tp2_projected": None,
        "estimated_net_rr_tp1": None,
        "requires_live_price_check": True,
        "order_allowed": False,
        "confirmation_conditions": (
            "Fechamento 15m alem do nivel (+/-0.12 ATR), corpo >=40%, "
            "volume relativo >=1.4; high/low do candle + 1 tick"
            if method == "BREAKOUT" else
            "Rompimento previo com volume; reteste tocado e recuperado "
            "por candle 15m fechado; high/low desse candle + 1 tick"
        ),
        "reasons": [],
    }
    if not confirmed:
        result["reasons"].append("candle de confirmacao ainda nao ocorreu")
        if method == "RETEST" and level is None:
            result["reasons"].append("rompimento anterior nao identificado")
        return result
    if tick is None:
        result["decision"] = "NAO_ENTRAR"
        result["entry_state"] = "REPROVADO_DADOS"
        result["reasons"].append("tickSz oficial OKX ausente ou invalido")
        return result
    try:
        extreme = float(last[2] if long else last[3])
        # Um tick ALÉM da maxima/minima do candle fechado, nao do nivel.
        entry = tick_round(Decimal(str(extreme)) +
                           (tick if long else -tick), tick,
                           "up" if long else "down")
        recent = bars[-3:]
        stop_raw = (min(level-.4*atr,
                        min(float(c[3]) for c in recent)-.15*atr) if long
                    else max(level+.4*atr,
                             max(float(c[2]) for c in recent)+.15*atr))
        stop = tick_round(stop_raw, tick, "down" if long else "up")
        if entry <= 0 or stop <= 0:
            raise ValueError("Preco/stop nao positivo")
        projection_ = projection(history, side, entry, stop, tick, spread)
        result.update(projection_)
        result["entry_trigger_price"] = numeric(entry)
        result["risk_atr"] = numeric(projection_["risk_price"]/atr)
        result["distance_to_entry_atr"] = numeric(
            (entry-close)/atr if long else (close-entry)/atr)
        reasons = result["reasons"]
        crossed = price_now >= entry if long else price_now <= entry
        result["trigger_crossed_in_snapshot"] = crossed
        if stale or expired:
            reasons.append("snapshot/candle expirado: recalcular com coleta atual")
        if spread > MAX_SPREAD_PCT:
            reasons.append("spread acima do limite")
        if float(result["risk_atr"]) > MAX_RISK_ATR:
            reasons.append("stop distante > 3 ATR")
        if result["distance_to_entry_atr"] < 0:
            reasons.append("gatilho ja ultrapassado no candle: nao perseguir")
        if result["distance_to_entry_atr"] > MAX_TRIGGER_DISTANCE_ATR:
            reasons.append("gatilho distante > 0.9 ATR")
        if result["estimated_net_rr_tp1"] < MIN_RR:
            reasons.append("R:R liquido inferior a 1.6 na entrada exata")
        if result["pivot_obstacle_before_tp1"] is not None:
            reasons.append("pivot estrutural entre entrada e TP1")
        if crossed:
            reasons.append("cotacao da coleta ja atravessou o preco de gatilho")
        invalidated = price_now <= stop if long else price_now >= stop
        if invalidated:
            reasons.append("cotacao da coleta cruzou nivel de invalidacao/stop")
        opposite = "baixa" if long else "alta"
        if info["trend_1h"] == opposite and info["structure_1h"] != (
                "alta" if long else "baixa"):
            reasons.append("1h em tendencia contraria sem virada estrutural")
        if info["trend_2h"] == opposite:
            reasons.append("2h contraria: setup exige analise adicional")
        if expired:
            result["entry_state"] = "EXPIRADO"
        elif stale:
            result["entry_state"] = "DADOS_DESATUALIZADOS"
        elif invalidated:
            result["entry_state"] = "INVALIDADO"
        elif crossed:
            # O ticker ja passou pelo gatilho. Nao significa fill de ordem.
            result["entry_state"] = "PRECO_JA_ULTRAPASSOU_GATILHO"
        elif reasons:
            result["entry_state"] = "REPROVADO_RISCO"
        else:
            result["entry_state"] = "GATILHO_DISPONIVEL"
        if reasons:
            result["decision"] = "NAO_ENTRAR"
        else:
            result["decision"] = "GATILHO_CONDICIONAL"
            result["entry_eligible_at_snapshot"] = True
            result["reasons"] = ["Aguardar preco cruzar gatilho e validar spread/volume novamente"]
    except (ValueError, TypeError, ArithmeticError, ZeroDivisionError) as exc:
        result["decision"] = "NAO_ENTRAR"
        result["entry_state"] = "REPROVADO_DADOS"
        result["reasons"].append(f"plano nao calculavel: {exc}")
    return result


def analyze_both(history, info, ticker, side, now_utc, stale):
    return [analyze_method(history, info, ticker, side, method, now_utc, stale)
            for method in ("BREAKOUT", "RETEST")]
