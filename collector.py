
"""Coletor de candles públicos. Somente dados; não gera sinais."""
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

INTERVALS = {'2h': 7200, '1h': 3600, '15m': 900}
EXCLUDED = {
    'BTC', 'ETH', 'USDC', 'USDE', 'DAI',
    'FDUSD', 'TUSD', 'WETH', 'WBTC', 'USDT'
}


def api(url, params=None):
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json'
        }
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def okx(path, **params):
    data = api('https://www.okx.com/api/v5/' + path, params)
    if data.get('code') != '0':
        raise ValueError(f'OKX: {str(data)[:160]}')
    return data['data']


def binance(path, **params):
    return api('https://data-api.binance.vision/api/v3/' + path, params)


def universe(venue):
    if venue == 'okx_swap':
        ids = {
            x['instId']
            for x in okx('public/instruments', instType='SWAP')
            if x.get('state') == 'live'
            and x['instId'].endswith('-USDT-SWAP')
        }
        raw = okx('market/tickers', instType='SWAP')
        items = []

        for t in raw:
            symbol = t.get('instId', '')
            base = symbol.removesuffix('-USDT-SWAP')
            if symbol not in ids or base in EXCLUDED or base.startswith('1000'):
                continue

            try:
                price = float(t['last'])
                open24 = float(t['open24h'])
                quote_vol = float(t['volCcy24h']) * price
                high = float(t['high24h'])
                low = float(t['low24h'])
                stamp = int(t['ts'])

                if (price <= 0 or open24 <= 0
                    or quote_vol < 10_000_000
                    or time.time() * 1000 - stamp > 300_000):
                    continue

                items.append(dict(
                    symbol=symbol,
                    price=price,
                    change=(price / open24 - 1) * 100,
                    volume_usdt_24h=quote_vol,
                    range_pct=(high - low) / price * 100,
                    timestamp_ms=stamp
                ))
            except (ValueError, KeyError, TypeError, ZeroDivisionError):
                continue
    else:
        ids = {
            x['symbol']
            for x in binance('exchangeInfo')['symbols']
            if x.get('status') == 'TRADING'
            and x.get('quoteAsset') == 'USDT'
        }
        items = []

        for t in binance('ticker/24hr'):
            symbol = t.get('symbol', '')
            base = symbol.removesuffix('USDT')
            if symbol not in ids or base in EXCLUDED or base.startswith('1000'):
                continue

            try:
                price = float(t['lastPrice'])
                volume = float(t['quoteVolume'])
                if price <= 0 or volume < 10_000_000:
                    continue

                items.append(dict(
                    symbol=symbol,
                    price=price,
                    change=float(t['priceChangePercent']),
                    volume_usdt_24h=volume,
                    range_pct=(
                        float(t['highPrice']) - float(t['lowPrice'])
                    ) / price * 100,
                    timestamp_ms=int(time.time() * 1000)
                ))
            except (ValueError, KeyError, TypeError, ZeroDivisionError):
                continue

    winners = sorted(items, key=lambda x: -x['change'])
    losers = sorted(items, key=lambda x: x['change'])
    volatility = sorted(items, key=lambda x: -x['range_pct'])

    chosen, seen = [], set()
    for trio in zip(winners, losers, volatility):
        for group, item in zip(
            ('gainers', 'losers', 'volatility'), trio
        ):
            if item['symbol'] not in seen:
                chosen.append(dict(item, group=group))
                seen.add(item['symbol'])
                if len(chosen) == 20:
                    return chosen
    return chosen


def candles(venue, symbol, tf, now_ms):
    if venue == 'okx_swap':
        bar = {'2h': '2H', '1h': '1H', '15m': '15m'}[tf]
        rows = okx(
            'market/candles',
            instId=symbol,
            bar=bar,
            limit='100'
        )
    else:
        rows = binance(
            'klines',
            symbol=symbol,
            interval=tf,
            limit=100
        )

    result = []
    duration = INTERVALS[tf] * 1000

    for r in rows:
        if venue == 'okx_swap' and str(r[8]) != '1':
            continue

        ts = int(r[0])
        o, h, l, c, v = map(float, r[1:6])

        if ts + duration > now_ms:
            continue

        if ts % duration != 0 or not (
            0 < l <= min(o, c) <= max(o, c) <= h and v >= 0
        ):
            raise ValueError('Candle invalido / desalinhado')

        result.append([ts, o, h, l, c, v])

    result.sort(key=lambda x: x[0])

    if len(result) < 60 or any(
        b[0] - a[0] != duration
        for a, b in zip(result, result[1:])
    ):
        raise ValueError('Historico insuficiente ou com lacunas')

    if now_ms - (result[-1][0] + duration) > duration:
        raise ValueError('Candles desatualizados')

    return result


def main():
    Path('output').mkdir(exist_ok=True)
    errors = {}

    for venue in ('okx_swap', 'binance_spot'):
        print('Consultando', venue, flush=True)

        try:
            selected = universe(venue)
            now_ms = int(time.time() * 1000)
            valid = {}

            for item in selected:
                symbol = item['symbol']

                try:
                    valid[symbol] = {
                        tf: candles(venue, symbol, tf, now_ms)
                        for tf in INTERVALS
                    }
                except Exception as e:
                    errors[f'{venue}/{symbol}'] = str(e)

            print(
                f'{venue}: {len(valid)} ativos com 2h/1h/15m verificados',
                flush=True
            )

            if len(valid) >= 15:
                output = dict(
                    status='COLLECTED',
                    venue=venue,
                    signals_allowed=False,
                    collected_utc=datetime.now(
                        timezone.utc
                    ).isoformat(),
                    universe=selected,
                    candles=valid,
                    errors=errors
                )
                Path('output/market_data.json').write_text(
                    json.dumps(output),
                    encoding='utf-8'
                )
                print('Coleta concluida; nenhum sinal gerado.')
                return

            errors[venue] = 'Menos de 15 ativos validos'

        except Exception as e:
            errors[venue] = str(e)
            print(f'{venue} indisponivel: {e}', flush=True)

    Path('output/collector_status.json').write_text(
        json.dumps(errors, indent=2),
        encoding='utf-8'
    )
    raise RuntimeError(
        'Nenhuma fonte forneceu 15 ativos validos. '
        'Consulte output/collector_status.json'
    )


if __name__ == '__main__':
    main()
