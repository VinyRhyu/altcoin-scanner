"""Conservative early-trend candidate detector. Research only, notifications OFF.

Use only closed 2h/1h/15m candles for confirmation. This is a rule-based
prototype requiring paper-trading validation, NOT an execution algorithm.
"""
from __future__ import annotations
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from collector import CRYPTO_BASES, BLOCKED_BASES

DUR = {'2h':7200_000, '1h':3600_000, '15m':900_000}


def candles_from(data, name):
    """Accept both original list OHLCV and newer OHLCV+quote-volume."""
    raw = data[name]
    out = []
    for x in raw:
        if isinstance(x, dict):
            r = [x['open_ms'], x['open'], x['high'], x['low'], x['close'], x['volume'], x.get('turnover_quote', x['volume'])]
        else:
            r = list(x)
            if len(r) == 6:
                r.append(r[5])
        if len(r) < 7 or not all(math.isfinite(float(y)) for y in r):
            raise ValueError('Invalid OHLCV')
        if not (0 < r[3] <= min(r[1],r[4]) <= max(r[1],r[4]) <= r[2] and r[5]>=0 and r[6]>=0):
            raise ValueError('Invalid OHLCV bounds')
        out.append(r)
    if len(out) < 60 or any(b[0]-a[0]!=DUR[name] for a,b in zip(out,out[1:])):
        raise ValueError('Too few candles / gaps')
    return out


def atr(data, period=14):
    trs = []
    for i in range(max(1,len(data)-period),len(data)):
        a,b = data[i-1],data[i]
        trs.append(max(b[2]-b[3],abs(b[2]-a[4]),abs(b[3]-a[4])))
    return statistics.mean(trs)


def pivots(rows, idx, window=2):
    # Strict chronological: no look-ahead beyond the candles already closed.
    highs, lows = [], []
    for i in range(window,idx-window):
        cur = rows[i]
        other = rows[i-window:i]+rows[i+1:i+window+1]
        if all(cur[2] > x[2] for x in other):
            highs.append((i, cur[2]))
        if all(cur[3] < x[3] for x in other):
            lows.append((i, cur[3]))
    return highs,lows


def latest_bos(hourly, direction):
    """Return earliest structural change in the most recent six 1h bars."""
    changes = []
    end = len(hourly)
    for i in range(end-6, end):
        highs,lows = pivots(hourly,i)
        swings = highs if direction=='LONG' else lows
        if not swings:
            continue
        level = swings[-1][1]
        prior,cur = hourly[i-1][4],hourly[i][4]
        if (prior <= level < cur) if direction=='LONG' else (prior >= level > cur):
            # Reject a 'new' break that occurred repeatedly in the prior bars.
            prev_closes = [x[4] for x in hourly[max(0,i-5):i]]
            if direction=='LONG' and any(p>level for p in prev_closes):
                continue
            if direction=='SHORT' and any(p<level for p in prev_closes):
                continue
            changes.append((i,level))
    return changes[-1] if changes else None


def near_support_retest(quart, level, direction, tolerance):
    # First touch/rejection in recent three closed 15m bars, not merely OB touch.
    last = quart[-1]
    recent = quart[-4:-1]
    if direction=='LONG':
        touch = min(r[3] for r in recent+[last]) <= level+tolerance
        rejection = last[4]>last[1] and last[4]>max(x[2] for x in recent[-2:])
        return touch and rejection and last[4]>level
    touch = max(r[2] for r in recent+[last]) >= level-tolerance
    rejection = last[4]<last[1] and last[4]<min(x[3] for x in recent[-2:])
    return touch and rejection and last[4]<level


def find_targets(two, hourly, entry_worst, direction, risk, a1):
    # Actual 2h/1h historical pivot obstacles; target is placed BEFORE barrier.
    high2,low2 = pivots(two,len(two)-1)
    high1,low1 = pivots(hourly,len(hourly)-2)
    raw = ([x[1] for x in high2+high1] if direction=='LONG'
           else [x[1] for x in low2+low1])
    obstacles = sorted(set(round(x,10) for x in raw),reverse=direction=='SHORT')
    obstacles = [x for x in obstacles if (x>entry_worst if direction=='LONG' else x<entry_worst)]
    if len(obstacles)<2:
        return None
    # Nearest obstacle must permit TP1; no skipping close barriers.
    first,second=obstacles[:2]
    pad = max(a1*0.08,entry_worst*0.0005)
    tp1=first-pad if direction=='LONG' else first+pad
    tp2=second-pad if direction=='LONG' else second+pad
    reward1 = (tp1-entry_worst) if direction=='LONG' else (entry_worst-tp1)
    reward2 = (tp2-entry_worst) if direction=='LONG' else (entry_worst-tp2)
    if reward1/risk<0.65 or reward2/risk<1.6 or reward2<=reward1:
        return None
    return tp1,tp2,reward2/risk


def evaluate(item, frame, collected_ms, report_stale=False):
    result={'symbol':item['symbol'],'status':'REJECTED', 'reasons':[]}
    base=item.get('base') or (item['symbol'].split('-')[0] if '-' in item['symbol'] else item['symbol'].removesuffix('USDT'))
    if base not in CRYPTO_BASES or base in BLOCKED_BASES:
        result['reasons'].append('Not an eligible cryptocurrency (or BTC/ETH context only)')
        return result
    try:
        two,hour,quart=[candles_from(frame,k) for k in ('2h','1h','15m')]
    except (ValueError,KeyError,TypeError) as e:
        result['reasons'].append(f'Unverified candles: {e}')
        return result
    # Validate historical timestamps against the snapshot, not only row ordering.
    for tf,series in [('2h',two),('1h',hour),('15m',quart)]:
        end_ms=series[-1][0]+DUR[tf]
        if not 0 <= collected_ms-end_ms <= DUR[tf]:
            result['reasons'].append(f'Stale or future {tf} closed candle')
            return result
    # A supplied old snapshot is REPLAY ONLY and must not produce executable signals.
    if not report_stale and abs(datetime.now(timezone.utc).timestamp()*1000-collected_ms)>20*60_000:
        result['reasons'].append('Market snapshot is stale (>20 min)')
        return result
    a2,a1,a15=atr(two),atr(hour),atr(quart)
    if min(a2,a1,a15)<=0:
        result['reasons'].append('ATR invalid');return result
    price=float(item.get('price',item.get('last',0)))
    lastclose=quart[-1][4]
    if price<=0 or abs(price/lastclose-1)>max(0.004,0.8*a15/price):
        result['reasons'].append('Ticker departed from last closed 15m');return result
    # 2h context is mandatory and is never replaced with 4h.
    two_hi,two_lo=pivots(two,len(two)-1)
    if not two_hi or not two_lo:
        result['reasons'].append('Missing 2h swing structure');return result
    attempted=[]
    for direction in ('LONG','SHORT'):
        bos=latest_bos(hour,direction)
        if not bos:
            attempted.append(f'{direction}: no new 1h structural break')
            continue
        i,level=bos
        # Avoid mature impulse: near first change, recent 1h BOS, price near origin.
        if abs(price-level)>1.15*a1:
            attempted.append(f'{direction}: extended > 1.15 ATR1h from break level')
            continue
        # The 15m trigger must be observed as a closed candle rejection / break.
        if not near_support_retest(quart,level,direction,max(0.35*a1,0.5*a15)):
            attempted.append(f'{direction}: no 15m closed-candle rejection on first retest')
            continue
        last=quart[-1]
        band=0.12*a15
        entry_low=last[4]-band;entry_high=last[4]+band
        if not entry_low<=price<=entry_high:
            attempted.append(f'{direction}: observed price outside entry band')
            continue
        if direction=='LONG':
            stop=min(level,min(x[3] for x in quart[-4:]))-0.15*a15
            worst=entry_high
            risk=worst-stop
        else:
            stop=max(level,max(x[2] for x in quart[-4:]))+0.15*a15
            worst=entry_low
            risk=stop-worst
        if not 0.7*a15<=risk<=2.2*a1 or risk<=0:
            attempted.append(f'{direction}: invalid stop distance versus volatility')
            continue
        targets=find_targets(two,hour,worst,direction,risk,a1)
        if targets is None:
            attempted.append(f'{direction}: nearby structural barriers or R:R < 1:1.6')
            continue
        tp1,tp2,rr=targets
        # Score 1: structure (mandatory). 2: momentum. 3: participation. 4: RR/entry.
        impulse=hour[i]
        impulse_body=(impulse[4]-impulse[1]) if direction=='LONG' else (impulse[1]-impulse[4])
        momentum=impulse_body>=0.25*a1 and (
            (last[4]-last[1]) if direction=='LONG' else (last[1]-last[4]))>=0.18*a15
        prev_v=[x[6] for x in quart[-21:-1]]
        vratio=last[6]/statistics.median(prev_v) if statistics.median(prev_v)>0 else 0
        impulse_prev=[x[6] for x in hour[max(0,i-20):i]]
        impulse_v=impulse[6]/statistics.median(impulse_prev) if impulse_prev and statistics.median(impulse_prev)>0 else 0
        participation=max(vratio,impulse_v)>=1.2
        # Continuation retests require meaningful participation, not merely a zone touch.
        if not participation:
            attempted.append(f'{direction}: no coherent 15m/1h participation')
            continue
        score=1+int(momentum)+int(participation)+1
        if score<3:
            attempted.append(f'{direction}: score < 3/4')
            continue
        signal={'symbol':item['symbol'],'direction':direction,'status':'PAPER_CANDIDATE',
                'entry_min':round(entry_low,8),'entry_max':round(entry_high,8),
                'sl':round(stop,8),'tp1':round(tp1,8),'tp2':round(tp2,8),
                'rr_worst_to_tp2':round(rr,3),'score':score,
                'structure':'1h first BOS + closed 15m first retest rejection',
                'preparation':'First pullback toward the broken 1h structure; 2h swings used as obstacles',
                'bos_utc':datetime.fromtimestamp((hour[i][0]+DUR['1h'])/1000,timezone.utc).isoformat(),
                'last_15m_close_utc':datetime.fromtimestamp((quart[-1][0]+DUR['15m'])/1000,timezone.utc).isoformat(),
                'volume_ratio_15m':round(vratio,2), 'volume_ratio_bos_1h':round(impulse_v,2),
                'ticker_observed_utc':item.get('ticker_time_utc'),
                'observed_price':price, 'market_type':item.get('market_type'),
                'replay':report_stale}
        result.update(signal)
        result.pop('reasons',None)
        return result
    result['reasons'].extend(attempted or ['No qualified setup'])
    return result


def parse_snapshot_ms(payload):
    stamp=payload.get('collected_utc') or payload.get('collected_at_utc')
    return int(datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()*1000)


def main():
    path=Path(sys.argv[1] if len(sys.argv)>1 else 'output/market_data.json')
    payload=json.loads(path.read_text(encoding='utf-8'))
    stamp=parse_snapshot_ms(payload)
    items=payload['universe']
    results=[]
    for item in items:
        symbol=item['symbol']
        if symbol not in payload['candles']:
            continue
        results.append(evaluate(item,payload['candles'][symbol],stamp,report_stale=False))
    approved=[x for x in results if x['status']=='PAPER_CANDIDATE'][:3]
    out={'status':'DRY_RUN','signals_enabled':False, 'market':payload.get('market_type',payload.get('venue')),
         'snapshot_utc':payload.get('collected_utc',payload.get('collected_at_utc')),
         'evaluated':len(results),'paper_candidates':approved,
         'rejected_count':len(results)-len(approved),
         'audit_rejections':[{'symbol':x['symbol'], 'reasons':x.get('reasons', [])} for x in results if x['status']=='REJECTED'],
         'note':'Research only: NO notifications, NO live trades. Requires replay/backtest and paper trading.'}
    Path('output').mkdir(exist_ok=True)
    Path('output/analysis.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(f'RESEARCH DRY RUN: {len(results)} checked; {len(approved)} paper candidates; notifications OFF')
    if approved:
        for x in approved:
            print(f"{x['symbol']} {x['direction']} R:R={x['rr_worst_to_tp2']} score={x['score']} PAPER ONLY")


if __name__=='__main__':
    main()
