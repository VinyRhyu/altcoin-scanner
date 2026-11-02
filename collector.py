"""Public OHLCV collector: crypto-only universe, native 2h/1h/15m.

The collector never executes trades or notifies about signals. Only completed
candles are retained. Market types are never mixed in a single run.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OKX = 'https://www.okx.com/api/v5'
BINANCE = 'https://data-api.binance.vision/api/v3'
COINGECKO = 'https://api.coingecko.com/api/v3'
INTERVALS = {'2h': 7200, '1h': 3600, '15m': 900}
MIN_VOLUME_USDT = float(os.getenv('MIN_VOLUME_USDT', '2000000'))
PRIORITY_VOLUME_USDT = float(os.getenv('PRIORITY_VOLUME_USDT', '5000000'))
MIN_VALIDATED = int(os.getenv('MIN_VALIDATED', '15'))
UNIVERSE_SIZE = int(os.getenv('UNIVERSE_SIZE', '50'))
MAX_PRESCREEN = int(os.getenv('MAX_PRESCREEN', '90'))
MARKET_PROVIDER = os.getenv('MARKET_PROVIDER', 'okx_swap')
CG_KEY = os.getenv('COINGECKO_DEMO_API_KEY', '')

# Safety gate: known cryptocurrency tickers, not a real-time top-100 list.
# CoinGecko (when configured) intersects this gate with the current top 100.
CRYPTO_BASES = set('''SOL XRP BNB DOGE ADA AVAX LINK SUI TRX TON LTC BCH DOT UNI AAVE NEAR ICP ATOM FIL ETC XLM HBAR ALGO XMR ZEC TAO INJ RENDER RNDR OP ARB WLD ONDO ENA PENDLE SEI TIA FET GRT SAND MANA APT STX IMX RUNE KAS JUP JTO PYTH WIF PEPE BONK SHIB FLOKI CRV COMP LDO MKR SKY DYDX ETHFI STRK ZRO ZK POL GALA CHZ FLOW THETA VET IOTA KAVA XTZ NEO MINA ROSE HYPE MORPHO AERO TRUMP PUMP VIRTUAL JASMY ZIL KSM DASH SNX ENS FTM S AXS CAKE GNO ZRX NMR LPT RSR PAXG XAUT CAKE FARTCOIN INIT BIO PYR SUSHI BERA PENGU POPCAT TURBO'''.split()) - {'PAXG','XAUT','TRUMP','PUMP','FARTCOIN'}
BLOCKED_BASES = set('''BTC ETH USDC USDT DAI USDE FDUSD TUSD USDD PYUSD USD1 WBTC WETH STETH WSTETH WEETH WBETH CBBTC RLUSD XAUT PAXG XAG XAU XPT SOXL'''.split())


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def finite_positive(x):
    n = float(x)
    if not math.isfinite(n) or n <= 0:
        raise ValueError('Nonpositive/nonfinite field')
    return n


def request_json(url, params=None, headers=None, retries=3):
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'AltcoinScanner/1.0', 'Accept': 'application/json', **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code in (403, 451):
                raise RuntimeError(f'HTTP {e.code}: provider restricted access') from e
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries-1:
                raise RuntimeError(f'HTTP {e.code}') from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == retries - 1:
                raise RuntimeError(f'Network or JSON failure: {e}') from e
        time.sleep(1.0 + attempt)
    raise RuntimeError('Request failed')


def okx(path, **params):
    result = request_json(f'{OKX}/{path}', params)
    if result.get('code') != '0' or not isinstance(result.get('data'), list):
        raise RuntimeError(f'OKX response rejected: {str(result)[:100]}')
    return result['data']


def binance(path, **params):
    result = request_json(f'{BINANCE}/{path}', params)
    if isinstance(result, dict) and 'code' in result:
        raise RuntimeError(f'Binance response rejected: {str(result)[:100]}')
    return result


def eligible_bases():
    """Optional live top-100 membership. Fail safely to curated crypto-only gate."""
    if not CG_KEY:
        return CRYPTO_BASES, 'curated_cryptocurrency_allowlist_NOT_live_top100'
    try:
        rows = request_json(f'{COINGECKO}/coins/markets', {
            'vs_currency': 'usd', 'order': 'market_cap_desc',
            'per_page': 100, 'page': 1, 'sparkline': 'false',
        }, headers={'x-cg-demo-api-key': CG_KEY})
        if not isinstance(rows, list) or len(rows) < 80:
            raise ValueError('Incomplete CoinGecko top-100 response')
        counts = {}
        for row in rows:
            symbol = str(row.get('symbol', '')).upper()
            counts[symbol] = counts.get(symbol, 0) + 1
        allowed = {x for x in counts if counts[x] == 1 and x in CRYPTO_BASES and x not in BLOCKED_BASES}
        if len(allowed) < MIN_VALIDATED:
            raise ValueError('Insufficient eligible cryptocurrencies')
        return allowed, 'coingecko_live_top100_intersect_curated_crypto_allowlist'
    except (RuntimeError, ValueError, TypeError, KeyError) as e:
        print(f'CoinGecko unavailable ({e}); using curated list, not claiming top 100.', flush=True)
        return CRYPTO_BASES, 'coingecko_unavailable_curated_allowlist_NOT_live_top100'


def tickers(venue, allowed, now_ms):
    if venue == 'okx_swap':
        live = {x['instId'] for x in okx('public/instruments', instType='SWAP')
                if x.get('state') == 'live' and x.get('instId', '').endswith('-USDT-SWAP')}
        raw = okx('market/tickers', instType='SWAP')
    else:
        live = {x['symbol'] for x in binance('exchangeInfo')['symbols']
                if x.get('status') == 'TRADING' and x.get('quoteAsset') == 'USDT'}
        raw = binance('ticker/24hr')
    result = []
    for item in raw:
        sym = item.get('instId' if venue == 'okx_swap' else 'symbol', '')
        base = sym[:-10] if venue == 'okx_swap' and sym.endswith('-USDT-SWAP') else sym[:-4] if venue != 'okx_swap' and sym.endswith('USDT') else ''
        if sym not in live or base not in allowed or base in BLOCKED_BASES or base.startswith('1000'):
            continue
        try:
            if venue == 'okx_swap':
                price = finite_positive(item['last'])
                open24 = finite_positive(item['open24h'])
                # OKX derivatives volCcy24h is BASE currency; multiply by price.
                turnover = finite_positive(item['volCcy24h']) * price
                change = 100 * (price/open24 - 1)
                ts = int(item['ts'])
                bid = finite_positive(item['bidPx'])
                ask = finite_positive(item['askPx'])
                spread = (ask-bid)/price*100
                if ask < bid or spread > 0.25 or not 0 <= now_ms-ts <= 300_000:
                    continue
            else:
                price = finite_positive(item['lastPrice'])
                turnover = finite_positive(item['quoteVolume'])
                change = float(item['priceChangePercent'])
                ts = now_ms  # HTTP observation, not exchange ticker time
                spread = None
            high = finite_positive(item['high24h'] if venue == 'okx_swap' else item['highPrice'])
            low = finite_positive(item['low24h'] if venue == 'okx_swap' else item['lowPrice'])
            if high < max(price, low) or low > price or not math.isfinite(change) or turnover < MIN_VOLUME_USDT:
                continue
            result.append({'symbol': sym, 'base': base, 'price': price,
                           'change_24h_pct': round(change, 3), 'turnover_24h_usdt_est': round(turnover, 2),
                           'range_24h_pct': round((high-low)/price*100, 3),
                           'spread_pct': round(spread, 4) if spread is not None else None,
                           'ticker_time_utc': utc(ts)})
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
            continue
    return result


def parse_candles(rows, venue, tf, now_ms):
    duration = INTERVALS[tf] * 1000
    out = []
    for r in rows:
        if venue == 'okx_swap' and (len(r) < 9 or str(r[8]) != '1'):
            continue
        if len(r) < 8:
            raise ValueError('Bad kline response')
        t = int(r[0]); o,h,l,c,v = map(float, r[1:6]); turnover = float(r[7])
        if t+duration > now_ms:
            continue
        if t % duration or not all(math.isfinite(x) for x in (o,h,l,c,v,turnover)) or not (0 < l <= min(o,c) <= max(o,c) <= h and v >= 0 and turnover >= 0):
            raise ValueError('Malformed candle')
        out.append([t,o,h,l,c,v,turnover])
    out.sort(key=lambda x:x[0])
    if len(out) < 60 or any(y[0]-x[0] != duration for x,y in zip(out, out[1:])):
        raise ValueError('Missing/duplicate candles or insufficient history')
    if now_ms - (out[-1][0]+duration) > duration:
        raise ValueError('Stale latest closed candle')
    return out


def candles(venue, symbol, tf, now_ms):
    if venue == 'okx_swap':
        bar = {'2h':'2H','1h':'1H','15m':'15m'}[tf]
        rows = okx('market/candles', instId=symbol, bar=bar, limit='100')
    else:
        rows = binance('klines', symbol=symbol, interval=tf, limit=100)
    return parse_candles(rows, venue, tf, now_ms)


def intraday_anomaly(candles15):
    # Use completed 15m candle vs median of preceding 20 candles.
    last, prev = candles15[-1], candles15[-21:-1]
    median_v = statistics.median(x[6] for x in prev)
    med_range = statistics.median((x[2]-x[3])/x[4] for x in prev)
    volume_ratio = last[6]/median_v if median_v else 0
    range_ratio = ((last[2]-last[3])/last[4])/med_range if med_range else 0
    return round(volume_ratio, 3), round(range_ratio, 3)



def validated_liquidity(c15, ticker_estimate, min_volume=MIN_VOLUME_USDT, priority_volume=PRIORITY_VOLUME_USDT):
    """Quote turnover of the last 96 closed 15m candles, crosschecked with ticker."""
    if len(c15) < 96:
        raise ValueError('Insufficient 15m candles for 24h liquidity check')
    bar_turnover = sum(float(row[6]) for row in c15[-96:])
    ticker_turnover = float(ticker_estimate)
    if not all(math.isfinite(x) and x > 0 for x in (bar_turnover, ticker_turnover)):
        raise ValueError('Non-positive/nonfinite turnover')
    if bar_turnover < min_volume:
        raise ValueError('Closed-candle 24h quote turnover below discovery threshold')
    if not 0.5 <= ticker_turnover / bar_turnover <= 2.0:
        raise ValueError('Ticker 24h turnover differs >2x from closed 15m turnover')
    tier = 'priority' if bar_turnover >= priority_volume else 'exploratory'
    return round(bar_turnover, 2), tier


def prescreen(candidates, max_size):
    # Include winners, losers and liquid/volatile names before fetching 15m.
    buckets = [sorted(candidates, key=lambda x:-x['change_24h_pct']),
               sorted(candidates, key=lambda x:x['change_24h_pct']),
               sorted(candidates, key=lambda x:-x['range_24h_pct']),
               sorted(candidates, key=lambda x:-x['turnover_24h_usdt_est'])]
    chosen, seen = [], set()
    for i in range(len(candidates)):
        for bucket in buckets:
            if i >= len(bucket):
                continue
            item = bucket[i]
            if item['symbol'] not in seen:
                seen.add(item['symbol']); chosen.append(item)
            if len(chosen) == max_size:
                return chosen
    return chosen


def choose_universe(pool, n):
    gain = sorted([x for x in pool if x['change_24h_pct'] > 0], key=lambda x:-x['change_24h_pct'])
    loss = sorted([x for x in pool if x['change_24h_pct'] < 0], key=lambda x:x['change_24h_pct'])
    anomalies = sorted([x for x in pool if max(x['rel_volume_15m'], x['rel_range_15m']) >= 1.6],
                       key=lambda x:-max(x['rel_volume_15m'], x['rel_range_15m']))
    chosen, seen = [], set()
    quotas = [('gainers24h',gain,6), ('losers24h',loss,8), ('intraday_anomaly',anomalies,6)]
    for name, bucket, quota in quotas:
        for item in bucket:
            if len([x for x in chosen if x['group']==name]) >= quota:
                break
            if item['symbol'] not in seen:
                chosen.append({**item, 'group':name}); seen.add(item['symbol'])
    for item in sorted(pool, key=lambda x:-x['turnover_24h_usdt_est']):
        if len(chosen) >= n:
            break
        if item['symbol'] not in seen:
            chosen.append({**item, 'group':'liquidity_backfill'}); seen.add(item['symbol'])
    return chosen[:n]


def collect(venue, allowed, source, now_ms):
    all_tickers = tickers(venue, allowed, now_ms)
    if len(all_tickers) < MIN_VALIDATED:
        raise RuntimeError(f'Only {len(all_tickers)} eligible liquid tickers')
    pre = prescreen(all_tickers, MAX_PRESCREEN)
    pool, rejected, prescreen_candles = [], {}, {}
    for item in pre:
        try:
            item = dict(item)
            c15 = candles(venue, item['symbol'], '15m', now_ms)
            # Independently validate quote turnover using 96 closed 15m candles.
            ticker_estimate = item['turnover_24h_usdt_est']
            bar_turnover, tier = validated_liquidity(c15, ticker_estimate)
            item['turnover_24h_usdt_est_ticker'] = ticker_estimate
            item['turnover_24h_usdt_est'] = bar_turnover
            item['liquidity_tier'] = tier
            item['rel_volume_15m'], item['rel_range_15m'] = intraday_anomaly(c15)
            item['has_intraday_anomaly'] = max(item['rel_volume_15m'], item['rel_range_15m']) >= 1.6
            prescreen_candles[item['symbol']] = c15
            pool.append(item)
        except (ValueError, RuntimeError, TypeError) as e:
            rejected[item['symbol']] = str(e)
        time.sleep(0.10)
    selected = choose_universe(pool, UNIVERSE_SIZE)
    fallback = [{**x, 'group': 'liquidity_backfill'} for x in sorted(pool, key=lambda x:-x['turnover_24h_usdt_est'])
                if x['symbol'] not in {s['symbol'] for s in selected}]
    validated, universe = {}, []
    for item in selected + fallback:
        if len(validated) >= UNIVERSE_SIZE:
            break
        try:
            sym = item['symbol']
            series = {'15m': prescreen_candles[sym]}
            for tf in ('2h','1h'):
                series[tf] = candles(venue, sym, tf, now_ms)
            validated[sym] = series
            universe.append(item)
        except (ValueError, RuntimeError, TypeError) as e:
            rejected[item['symbol']] = str(e)
        time.sleep(0.10)
    if len(validated) < MIN_VALIDATED:
        raise RuntimeError(
            f'Only {len(validated)} full OHLCV validated (minimum {MIN_VALIDATED}); '
            f'{len(pool)} passed closed-candle 24h turnover threshold'
        )
    return {'status':'COLLECTED', 'signals_allowed':False, 'venue':venue,
            'exchange':'OKX' if venue=='okx_swap' else 'Binance',
            'market_type':'USDT_PERPETUAL' if venue=='okx_swap' else 'SPOT',
            'top100_filter':source, 'collected_utc':utc(now_ms),
            'universe':universe, 'candles':validated, 'rejected':rejected,
            'diagnostics':{'anomalies_any_group':sum(x.get('has_intraday_anomaly',False) for x in universe), 'liquidity_priority':sum(x.get('liquidity_tier')=='priority' for x in universe), 'liquidity_exploratory':sum(x.get('liquidity_tier')=='exploratory' for x in universe), 'available_liquid':len(all_tickers), 'prescreen':len(pre), 'initial_selected':len(selected), 'validated':len(validated), 'discovery_groups':{k:sum(x['group']==k for x in universe) for k in ('gainers24h','losers24h','intraday_anomaly','liquidity_backfill')}}}


def main():
    Path('output').mkdir(exist_ok=True)
    now_ms = int(time.time()*1000)
    allowed, source = eligible_bases()
    venues = ['okx_swap'] if MARKET_PROVIDER == 'auto' else [MARKET_PROVIDER]
    errs = {}
    for venue in venues:
        try:
            if venue not in ('okx_swap','binance_spot'):
                raise ValueError('Unknown MARKET_PROVIDER')
            print(f'Collecting {venue}; whitelist={source}', flush=True)
            output = collect(venue, allowed, source, now_ms)
            output['provider_failures'] = errs
            Path('output/market_data.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
            print(f"SUCCESS {venue}: {output['diagnostics']}; signals still disabled", flush=True)
            return
        except (RuntimeError, ValueError, TypeError, KeyError, urllib.error.URLError) as e:
            errs[venue] = str(e)
            print(f'{venue} failed: {e}', flush=True)
    Path('output/collector_status.json').write_text(json.dumps({'status':'FAILED', 'signals_allowed':False, 'errors':errs, 'utc':utc(now_ms)}, indent=2), encoding='utf-8')
    raise RuntimeError('No market provider returned 15 fully verified crypto instruments')


if __name__ == '__main__':
    main()
