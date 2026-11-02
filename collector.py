"""Public Bybit market-data collector. No trading or signal generation."""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = 'https://api.bybit.com/v5/market'
INTERVALS = {'2h': ('120', 7200), '1h': ('60', 3600), '15m': ('15', 900)}
MIN_TURNOVER = float(os.getenv('MIN_TURNOVER_USDT', '10000000'))
EXCLUDE = {'USDC', 'USDE', 'FDUSD', 'DAI', 'TUSD', 'USDD', 'PYUSD', 'EUR', 'WBTC', 'WETH', 'STETH', 'BTC', 'ETH'}


def get_json(endpoint, params, retries=3):
    url = f'{BASE}/{endpoint}?' + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'AltcoinScanner/0.1', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=18) as response:
                payload = json.load(response)
            if payload.get('retCode') != 0:
                raise ValueError(f'Bybit retCode={payload.get("retCode")}')
            return payload['result']
        except (OSError, ValueError, KeyError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def choose_universe(tickers, count=20):
    candidates = []
    for t in tickers:
        sym = t.get('symbol', '')
        if not sym.endswith('USDT') or '-' in sym:
            continue
        base = sym[:-4]
        if base in EXCLUDE or base.startswith(('1000', '10000')):
            continue
        try:
            turnover = float(t['turnover24h'])
            change = float(t['price24hPcnt'])
            last = float(t['lastPrice'])
            high = float(t['highPrice24h'])
            low = float(t['lowPrice24h'])
        except (ValueError, TypeError, KeyError):
            continue
        if turnover < MIN_TURNOVER or min(last, low) <= 0:
            continue
        candidates.append({'symbol': sym, 'turnover24h': turnover, 'change24h': change,
                           'last': last, 'range24h_pct': (high - low) / last * 100})
    gain = sorted(candidates, key=lambda x: x['change24h'], reverse=True)
    loss = sorted(candidates, key=lambda x: x['change24h'])
    anomaly = sorted(candidates, key=lambda x: x['range24h_pct'] * x['turnover24h'] ** .15, reverse=True)
    chosen, seen = [], set()
    for group in (gain, loss, anomaly):
        for item in group[:7]:
            if item['symbol'] not in seen:
                chosen.append(item)
                seen.add(item['symbol'])
    for item in sorted(candidates, key=lambda x: x['turnover24h'], reverse=True):
        if len(chosen) >= count:
            break
        if item['symbol'] not in seen:
            chosen.append(item)
            seen.add(item['symbol'])
    return chosen[:count]


def parse_closed_candles(rows, seconds, now_ms):
    candles = []
    for r in rows:
        start = int(r[0]); end = start + seconds * 1000
        if end > now_ms:
            continue  # current unfinished candle
        o, h, l, c, volume, turnover = map(float, r[1:7])
        if not (0 < l <= min(o, c) <= max(o, c) <= h and volume >= 0 and turnover >= 0):
            raise ValueError('Invalid OHLCV')
        candles.append({'open_ms': start, 'open': o, 'high': h, 'low': l,
                        'close': c, 'volume': volume, 'turnover': turnover})
    candles.sort(key=lambda x: x['open_ms'])
    if len(candles) < 60:
        raise ValueError('Insufficient closed candles')
    if any(b['open_ms'] - a['open_ms'] != seconds * 1000 for a, b in zip(candles, candles[1:])):
        raise ValueError('Missing/duplicate candles')
    if now_ms - (candles[-1]['open_ms'] + seconds * 1000) > seconds * 1000:
        raise ValueError('Stale candles')
    return candles


def main():
    now_ms = int(time.time() * 1000)
    tickers = get_json('tickers', {'category': 'linear'})['list']
    universe = choose_universe(tickers)
    if len(universe) < 15:
        raise RuntimeError(f'Only {len(universe)} liquid altcoins found')
    output = {'collected_at_utc': datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(),
              'exchange': 'Bybit', 'market': 'linear', 'universe': universe, 'candles': {}, 'errors': {}}
    for item in universe:
        sym = item['symbol']
        output['candles'][sym] = {}
        try:
            for tf, (interval, seconds) in INTERVALS.items():
                rows = get_json('kline', {'category': 'linear', 'symbol': sym,
                                          'interval': interval, 'limit': 100})['list']
                output['candles'][sym][tf] = parse_closed_candles(rows, seconds, now_ms)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            output['errors'][sym] = str(exc)
            output['candles'].pop(sym, None)
    output['validated_symbols'] = len(output['candles'])
    out = Path('output'); out.mkdir(exist_ok=True)
    (out / 'market_data.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(f'Validated {output["validated_symbols"]}/{len(universe)} symbols; output/market_data.json')
    if output['validated_symbols'] < 15:
        raise RuntimeError('Insufficient validated symbols: no signals permitted')
    print('DATA COLLECTION ONLY — NO TRADE SIGNALS GENERATED')


if __name__ == '__main__':
    main()
