"""Publica uma visão leve e detalhes por moeda do snapshot OKX existente.

Nao coleta precos, nao gera sinais e nao executa ordens.
Entrada: output/market_data.json (gerado por collector.py)
Saidas: output/scan_summary.json, output/coins/<SYMBOL>.json,
        output/manifest.json (atualizado com referencias aos novos arquivos).
"""
from __future__ import annotations

import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path('output')
VALID_SYMBOL = re.compile(r'^[A-Z0-9]+-USDT-SWAP$')
TIMEFRAMES = ('2h', '1h', '15m')


def round_safe(value, digits=5):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Valor nao finito nos candles')
    return round(number, digits)


def change_pct(new: float, old: float) -> float | None:
    if old <= 0:
        return None
    return round((new / old - 1.0) * 100.0, 3)


def timeframe_features(rows: list[list]) -> dict:
    if len(rows) < 35:
        raise ValueError('Historico insuficiente para features (minimo 35)')
    closes = [float(row[4]) for row in rows]
    highs = [float(row[2]) for row in rows]
    lows = [float(row[3]) for row in rows]
    last = closes[-1]
    sma20 = statistics.mean(closes[-20:])
    previous_sma20 = statistics.mean(closes[-25:-5])
    recent_20_high = max(highs[-20:])
    recent_20_low = min(lows[-20:])
    out = {
        'last_closed_open_ms': int(rows[-1][0]),
        'last_close': round_safe(last, 8),
        'sma20': round_safe(sma20, 8),
        'sma20_slope_5_pct': change_pct(sma20, previous_sma20),
        'return_8_candles_pct': change_pct(last, closes[-9]),
        'high_20': round_safe(recent_20_high, 8),
        'low_20': round_safe(recent_20_low, 8),
    }
    if len(rows[-1]) > 6:
        historic = [float(row[6]) for row in rows[-21:-1]]
        if len(historic) == 20 and statistics.median(historic) > 0:
            out['relative_quote_volume_last_vs_median20'] = round(
                float(rows[-1][6]) / statistics.median(historic), 3)
    return out


def trend_label(features: dict) -> str:
    # Triagem simples, NAO e gatilho nem recomendacao.
    price = features['last_close']
    ma = features['sma20']
    slope = features['sma20_slope_5_pct']
    if slope is not None and price > ma and slope > 0:
        return 'up'
    if slope is not None and price < ma and slope < 0:
        return 'down'
    return 'mixed'


def main() -> None:
    source = OUTPUT / 'market_data.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    if data.get('status') != 'COLLECTED' or data.get('market_type') != 'USDT_PERPETUAL':
        raise ValueError('Snapshot inesperado: status ou tipo de mercado incorreto')
    universe = data.get('universe')
    candles = data.get('candles')
    if not isinstance(universe, list) or not isinstance(candles, dict) or not universe:
        raise ValueError('Universo ou candles ausentes')

    coins_dir = OUTPUT / 'coins'
    coins_dir.mkdir(parents=True, exist_ok=True)
    for old in coins_dir.glob('*.json'):
        old.unlink()

    catalog = []
    for ticker in universe:
        symbol = ticker['symbol']
        if not VALID_SYMBOL.fullmatch(symbol):
            raise ValueError(f'Simbolo inseguro ou inesperado: {symbol}')
        history = candles[symbol]
        features = {tf: timeframe_features(history[tf]) for tf in TIMEFRAMES}
        direction2h = trend_label(features['2h'])
        direction1h = trend_label(features['1h'])
        alignment = direction2h if direction2h == direction1h and direction2h != 'mixed' else 'mixed'
        coin_filename = f'coins/{symbol}.json'
        catalog.append({
            'symbol': symbol,
            'base': ticker.get('base', symbol.split('-')[0]),
            'ticker_price': ticker.get('price'),
            'change_24h_pct': ticker.get('change_24h_pct'),
            'turnover_24h_usdt_est': ticker.get('turnover_24h_usdt_est'),
            'spread_pct': ticker.get('spread_pct'),
            'liquidity_tier': ticker.get('liquidity_tier'),
            'discovery_group': ticker.get('group'),
            'features': features,
            'trend_screen_2h': direction2h,
            'trend_screen_1h': direction1h,
            'trend_alignment_screen': alignment,
            'detail_file': coin_filename,
        })
        coin = {
            'status': 'OK',
            'venue': data.get('venue'),
            'market_type': data['market_type'],
            'collected_utc': data['collected_utc'],
            'symbol': symbol,
            'ticker': ticker,
            'candles': {tf: history[tf] for tf in TIMEFRAMES},
            'candle_columns': ['open_time_ms', 'open', 'high', 'low', 'close', 'base_volume', 'quote_volume'],
            'closed_candles_only': True,
        }
        (OUTPUT / coin_filename).write_text(
            json.dumps(coin, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

    summary = {
        'schema_version': 1,
        'status': 'OK',
        'venue': data.get('venue'),
        'market_type': data.get('market_type'),
        'collected_utc': data['collected_utc'],
        'asset_count': len(catalog),
        'candles_per_asset': {tf: len(candles[catalog[0]['symbol']][tf]) for tf in TIMEFRAMES},
        'trend_screen_method': 'SMA20: last_close vs SMA20 and SMA20 slope (5 bars); exploratory only, not a trade signal',
        'diagnostics': data.get('diagnostics', {}),
        'assets': catalog,
    }
    (OUTPUT / 'scan_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

    manifest_path = OUTPUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.update({
        'summary_file': 'scan_summary.json',
        'coin_directory': 'coins/',
        'coin_count': len(catalog),
        'summary_schema_version': 1,
    })
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PUBLICATION VIEWS: {len(catalog)} ativos, resumo '
          f'{(OUTPUT / "scan_summary.json").stat().st_size:,} bytes, '
          f'{len(list(coins_dir.glob("*.json")))} arquivos individuais')


if __name__ == '__main__':
    main()
