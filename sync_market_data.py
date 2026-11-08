"""Sincronizacao temporal entre a coleta e o candle 15m fechado.

O candle de 15m mais recente deve estar publicado antes que a V2 sugira
um gatilho atual. A cotacao do ticker tambem precisa ser recente. Caso
contrario, o radar continua informativo, mas nao aciona oportunidades.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

CANDLE_15M_SECONDS = 900
MAX_QUOTE_AGE_SECONDS = 300
MAX_SNAPSHOT_AGE_SECONDS = 1800


def parse_utc(value):
    if not value:
        return None
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def check_market_freshness(market, now_utc=None):
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_seconds = now.timestamp()
    expected = int(now_seconds // CANDLE_15M_SECONDS) * CANDLE_15M_SECONDS
    observed = set()
    for ticker in market.get("universe", []):
        bars = market.get("candles", {}).get(ticker.get("symbol"), {}).get("15m", [])
        if not bars:
            continue
        observed.add(int(bars[-1][0]) // 1000 + CANDLE_15M_SECONDS)
    source = parse_utc(market.get("collected_utc"))
    age = now_seconds - source.timestamp() if source else None
    last_close = max(observed) if observed else None
    if not observed or len(observed) != 1:
        status = "CANDLES_INCONSISTENTES"
    elif last_close > expected or (age is not None and age < -120):
        status = "HORARIO_INCONSISTENTE"
    elif last_close < expected:
        status = "AGUARDANDO_NOVO_CANDLE_PUBLICADO"
    elif age is None or age > MAX_SNAPSHOT_AGE_SECONDS:
        status = "COLETA_DESATUALIZADA"
    else:
        status = "CANDLE_ATUAL"
    quote_ages = []
    missing = 0
    for ticker in market.get("universe", []):
        quote_time = parse_utc(ticker.get("ticker_time_utc"))
        if quote_time:
            quote_ages.append(now_seconds - quote_time.timestamp())
        else:
            missing += 1
    quote_current = (not missing and bool(quote_ages)
                     and all(-120 <= n <= MAX_QUOTE_AGE_SECONDS for n in quote_ages))
    if status == "CANDLE_ATUAL" and not quote_current:
        status = "COTACAO_DESATUALIZADA"
    format_dt = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat() if t is not None else None
    return {
        "source_sync_status": status,
        "source_collected_utc": market.get("collected_utc"),
        "source_collected_age_minutes": round(age/60, 3) if age is not None else None,
        "expected_last_closed_15m_utc": format_dt(expected),
        "source_last_closed_15m_utc": format_dt(last_close),
        "candle_lag_minutes": round((expected-last_close)/60, 3) if last_close is not None else None,
        "ticker_max_age_minutes": round(max(quote_ages)/60, 3) if quote_ages else None,
        "missing_ticker_timestamp_count": missing,
        "ticker_quotes_current": quote_current,
        "current_for_new_entries": status == "CANDLE_ATUAL",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", default="market_data.json")
    parser.add_argument("--require-current", action="store_true")
    args = parser.parse_args()
    market = json.loads(Path(args.market_data).read_text(encoding="utf-8"))
    state = check_market_freshness(market)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if args.require_current and not state["current_for_new_entries"]:
        parser.exit(2, "V2: dados nao atuais; nova coleta obrigatoria antes de sugerir entradas.\n")


if __name__ == "__main__":
    main()
