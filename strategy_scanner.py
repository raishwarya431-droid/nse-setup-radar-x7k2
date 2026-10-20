#!/usr/bin/env python3
"""
strategy_scanner.py - finds stocks that match the 1D structure -> 1H confirmation setup.

What it checks, per stock (long side; shorts run the same logic on inverted prices):
  Daily  : CHoCH (close above the last lower high after a downtrend), then a BOS with
           displacement, then the FVG + order block left by that move.
  Zone   : is price now inside, or close to, the FVG/OB zone?
  1H     : rejection in the zone, close back above the 1H 9 EMA, relative volume.
  Context: confluence ticks (key level, fib 0.618-0.705, MTF EMA; futures OI is manual).
It then scores the setup on your 10-point card and proposes entry / SL / targets.

Usage:
  pip install -r requirements.txt
  python strategy_scanner.py --watchlist watchlist.txt --side both --min-score 4 --json _site/data/latest.json
The JSON feeds the mobile page in site/ (see README). --charts saves PNGs instead, for desktop use.
Run it after the close (or before the open) so no candle is still forming.
Data comes from Yahoo Finance via yfinance (unofficial, can be delayed or break).
For reliable intraday data use a broker API and feed the two DataFrames to scan_symbol().
Not investment advice. A scan finds candidates; you still check the chart, OI and results date.
"""
import argparse, json, math, os, sys
import numpy as np
import pandas as pd

CFG = dict(
    pivot=3,              # bars each side for a swing high/low on the daily chart
    atr_n=14,
    lookback=150,         # daily bars searched for the CHoCH
    disp_body_atr=1.0,    # BOS candle body must be >= this x ATR
    fvg_min_atr=0.2,      # ignore FVGs smaller than this x ATR
    stack_gap_atr=0.5,    # OB and FVG count as one stack if the gap is <= this x ATR
    approach_atr=1.5,     # "approaching" if price is within this x ATR above the zone
    sl_buffer_atr=0.25,   # buffer below the order block for the SL
    rvol_min=1.5, rvol_n=20,
    h_lookback=40,        # 1H bars searched for the rejection and trigger
    trigger_age=2,        # trigger must be within this many 1H bars of now
    min_price=50.0, min_turnover_cr=20.0, min_bars=60,
    max_bos_age=60,       # ignore structures whose BOS is older than this many daily bars
    max_zone_atr=3.5,     # ignore zones taller than this x ATR (the stop would be too wide)
    max_zone_pct=10.0,    # ... or taller than this % of price
    trigger_max_atr=1.0,  # a trigger counts only while price is within this x ATR of the zone
)
PAIRS = [('zone_bot', 'zone_top'), ('ob_low', 'ob_high'), ('fvg_low', 'fvg_high'), ('fib_lo', 'fib_hi'), ('leg_low', 'leg_high')]
SINGLES = ['entry', 'sl', 't1', 't2', 't3', 'price', 'bos_level', 'choch_level']

# ------------------------------------------------------------------ indicators
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(), (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def invert(df, K):
    """Mirror prices (p -> K - p) so the long logic can find short setups. EMA/ATR commute with this."""
    o = df.copy()
    o['Open'], o['High'], o['Low'], o['Close'] = K - df.Open, K - df.Low, K - df.High, K - df.Close
    return o

def swing_points(df, n):
    h, l = df.High.values, df.Low.values
    ph, pl = [], []
    for i in range(n, len(df) - n):
        if h[i] >= h[i - n:i].max() and h[i] > h[i + 1:i + n + 1].max(): ph.append(i)
        if l[i] <= l[i - n:i].min() and l[i] < l[i + 1:i + n + 1].min(): pl.append(i)
    return ph, pl

# ------------------------------------------------------------------ daily structure
def find_structures(df, cfg):
    """Return candidate (CHoCH, BOS, zone) structures, newest first."""
    N = len(df); o, h, l, c = (df[k].values for k in ('Open', 'High', 'Low', 'Close'))
    a = atr(df, cfg['atr_n']).values; n = cfg['pivot']
    ph, pl = swing_points(df, n)
    out = []
    for ci in range(N - 1, max(n * 3, N - cfg['lookback']) - 1, -1):
        phs = [i for i in ph if i + n <= ci - 1]; pls = [i for i in pl if i + n <= ci - 1]
        if len(phs) < 2 or len(pls) < 2: continue
        lh, lh2 = phs[-1], phs[-2]
        if not h[lh] < h[lh2]: continue                      # last swing high is a lower high
        if not l[pls[-1]] < l[pls[-2]]: continue             # and lows are falling: a downtrend
        lvl = h[lh]
        if not c[ci] > lvl or np.any(c[lh + 1:ci] > lvl): continue   # first close above only
        base_low = l[pls[-1]]
        if np.any(l[ci:] < base_low): continue               # structure failed after the CHoCH
        bos, run, pulled = None, h[ci], False
        for j in range(ci + 1, N):
            if c[j] < o[j]: pulled = True
            body = c[j] - o[j]; aj = a[j - 1] if not math.isnan(a[j - 1]) else (h[j] - l[j])
            if pulled and c[j] > run and body >= cfg['disp_body_atr'] * aj:
                bos = j; break
            run = max(run, h[j])
        if bos is None: continue
        b = bos; a_b = a[b - 1] if not math.isnan(a[b - 1]) else (h[b] - l[b])
        # order block: last down candle before the displacement
        ob = None
        for k in range(b - 1, max(ci, b - 6) - 1, -1):
            if c[k] < o[k]: ob = (l[k], o[k], k); break
        # FVG: largest bullish gap around the displacement candle
        fvg = None
        for m in range(b - 1, min(b + 2, N - 1)):
            if m >= 1 and m + 1 < N and l[m + 1] > h[m - 1] and (l[m + 1] - h[m - 1]) >= cfg['fvg_min_atr'] * a_b:
                if fvg is None or (l[m + 1] - h[m - 1]) > (fvg[1] - fvg[0]): fvg = (h[m - 1], l[m + 1], m)
        if ob is None and fvg is None: continue
        zone_top = fvg[1] if fvg else ob[1]
        zone_bot = ob[0] if ob else fvg[0]
        stacked = bool(ob and fvg and (fvg[0] - ob[1]) <= cfg['stack_gap_atr'] * a_b)
        zone_q = 2 if stacked else 1
        leg_low = ob[0] if ob else l[b - 1]; leg_high = h[b:].max()
        if leg_high - leg_low < 2 * a_b: continue
        rng = leg_high - leg_low
        out.append(dict(choch_i=ci, bos_i=b, choch_level=lvl, bos_level=run, ob_low=ob[0] if ob else np.nan, ob_high=ob[1] if ob else np.nan,
                        fvg_low=fvg[0] if fvg else np.nan, fvg_high=fvg[1] if fvg else np.nan, zone_bot=zone_bot, zone_top=zone_top,
                        zone_q=zone_q, leg_low=leg_low, leg_high=leg_high, fib_hi=leg_high - 0.618 * rng, fib_lo=leg_high - 0.705 * rng, atr=a[-1]))
        if len(out) >= 5: break
    return out

def zone_state(df, s, cfg, px_ref=None):
    c, l = df.Close.values, df.Low.values; a = s['atr']; b = s['bos_i']
    px_ref = px_ref if px_ref is not None else c[-1]
    if len(c) - 1 - b > cfg['max_bos_age']: return 'STALE'
    height = s['zone_top'] - s['zone_bot']
    if height > cfg['max_zone_atr'] * a or 100 * height / px_ref > cfg['max_zone_pct']: return 'WIDE'
    if np.any(c[b + 1:] < s['zone_bot'] - cfg['sl_buffer_atr'] * a): return 'BROKEN'
    if c[-1] >= s['zone_bot'] and l[max(b + 2, len(l) - 3):].min() <= s['zone_top']: return 'IN_ZONE'
    if c[-1] > s['zone_top'] and c[-1] - s['zone_top'] <= cfg['approach_atr'] * a: return 'APPROACH'
    return 'FAR'

# ------------------------------------------------------------------ 1H confirmation
def confirm_1h(h1, s, cfg):
    res = dict(rejection=0, reclaim=0, rvol_ok=0, rvol=np.nan, trigger_ago=None, touched=False, pullback_low=np.nan)
    if h1 is None or len(h1) < cfg['rvol_n'] + 5: return res
    e = ema(h1.Close, 9).values; o, h, l, c, v = (h1[k].values for k in ('Open', 'High', 'Low', 'Close', 'Volume'))
    N = len(h1); lo_i = max(0, N - cfg['h_lookback'])
    zb, zt, buf = s['zone_bot'], s['zone_top'], cfg['sl_buffer_atr'] * s['atr']
    touch = [i for i in range(lo_i, N) if l[i] <= zt and l[i] >= zb - 2 * buf]
    if not touch: return res
    res['touched'] = True; t0 = touch[0]; res['pullback_low'] = l[t0:].min()
    for i in touch:                                           # rejection candle inside the zone
        rg = h[i] - l[i]; body = abs(c[i] - o[i]) + 1e-9; lw = min(o[i], c[i]) - l[i]
        if rg > 0 and c[i] >= l[i] + 0.6 * rg and lw >= 0.5 * body: res['rejection'] = 1
    for t in range(N - 1, max(t0 + 1, N - 1 - cfg['trigger_age']) - 1, -1):   # the bar that closes back above the 9 EMA
        if c[t] > e[t] and c[t - 1] <= e[t - 1]:
            res['reclaim'] = 1; res['trigger_ago'] = N - 1 - t
            base = v[max(0, t - cfg['rvol_n']):t]; rv = v[t] / base.mean() if len(base) and base.mean() > 0 else np.nan
            res['rvol'] = rv; res['rvol_ok'] = int(rv >= cfg['rvol_min']) if not math.isnan(rv) else 0
            break
    return res

# ------------------------------------------------------------------ scoring + plan
def grade(score): return 'A+' if score >= 8 else 'A' if score >= 6 else 'B' if score >= 4 else 'skip'

def liquid(daily, cfg):
    return daily.Close.iloc[-1] >= cfg['min_price'] and (daily.Close * daily.Volume).tail(20).mean() / 1e7 >= cfg['min_turnover_cr']

def round_in_zone(bot, top):
    step = 5 if top < 200 else 10 if top < 600 else 25 if top < 1500 else 50 if top < 4000 else 100 if top < 15000 else 500
    return math.floor(top / step) * step >= bot

def detect(daily, hourly, cfg=CFG, K=None):
    daily = daily.dropna(subset=['Open', 'High', 'Low', 'Close']).copy()
    if len(daily) < cfg['min_bars']: return None
    px = daily.Close.iloc[-1]
    cands = find_structures(daily, cfg)
    if not cands: return None
    chosen = None
    px_o = px if K is None else K - px            # price in the original (un-mirrored) space
    for s in cands:
        st = zone_state(daily, s, cfg, px_o)
        if st in ('IN_ZONE', 'APPROACH'): chosen = (s, st); break
    if chosen is None: chosen = (cands[0], zone_state(daily, cands[0], cfg, px_o))
    s, state = chosen
    if state not in ('IN_ZONE', 'APPROACH'): return dict(symbol=None, state=state, score=0, grade='skip')
    cf = confirm_1h(hourly, s, cfg)
    a = s['atr']
    ticks = []
    zb_o, zt_o = (s['zone_bot'], s['zone_top']) if K is None else (K - s['zone_top'], K - s['zone_bot'])   # original price space
    if round_in_zone(zb_o, zt_o) or (s['zone_bot'] - 0.25 * a <= s['bos_level'] <= s['zone_top'] + 0.25 * a): ticks.append('key level')
    if min(s['fib_hi'], s['zone_top']) >= max(s['fib_lo'], s['zone_bot']): ticks.append('fib')
    d_ema = ema(daily.Close, 21).iloc[-1]
    h_ok = hourly is None or len(hourly) < 15 or hourly.Close.iloc[-1] > ema(hourly.Close, 9).iloc[-1]
    if px >= d_ema - 0.5 * a and h_ok: ticks.append('MTF EMA')   # trend intact on the daily, 1H aligned
    structure = 2; zone_q = s['zone_q']; conf = cf['rejection'] + cf['reclaim'] + cf['rvol_ok']; ctx = min(3, len(ticks))
    score = structure + zone_q + conf + ctx
    triggered = bool(cf['reclaim'] and cf['touched'])
    last_h = hourly.Close.iloc[-1] if hourly is not None and len(hourly) else px
    if triggered and (last_h - s['zone_top']) > cfg['trigger_max_atr'] * a:
        return dict(symbol=None, state='EXTENDED', score=0, grade='skip')       # price already ran away from the zone
    if triggered and not cf['rvol_ok']: status = 'WATCH (trigger, low volume)'    # your rule needs strong volume on the trigger
    elif triggered: status = 'TRIGGERED'
    else: status = 'WATCH (in zone)' if state == 'IN_ZONE' else 'APPROACHING (set alert)'
    # trade plan
    entry = last_h if status == 'TRIGGERED' else s['zone_top'] - 0.25 * (s['zone_top'] - s['zone_bot'])
    base = s['ob_low'] if not math.isnan(s['ob_low']) else s['zone_bot']
    sl = min(base, cf['pullback_low'] if not math.isnan(cf['pullback_low']) else base) - cfg['sl_buffer_atr'] * a
    risk = entry - sl
    plan = {}
    if risk > 0:
        plan = dict(entry=entry, sl=sl, t1=s['leg_high'], t2=entry + 2 * risk, t3=entry + 3 * risk, risk_pct=100 * risk / entry, r_t1=(s['leg_high'] - entry) / risk)
    notes = ['check futures OI', 'check results date']
    if plan and plan['r_t1'] < 1.5: notes.append('limited room to prior high')
    return dict(state=state, status=status, structure=structure, zone_q=zone_q, confirm=conf, context=ctx, score=score, grade=grade(score),
                ticks=', '.join(ticks) or '-', rvol=cf['rvol'], price=px, choch_level=s['choch_level'], bos_level=s['bos_level'],
                zone_bot=s['zone_bot'], zone_top=s['zone_top'], ob_low=s['ob_low'], ob_high=s['ob_high'], fvg_low=s['fvg_low'], fvg_high=s['fvg_high'],
                fib_lo=s['fib_lo'], fib_hi=s['fib_hi'], leg_low=s['leg_low'], leg_high=s['leg_high'],
                choch_date=str(daily.index[s['choch_i']])[:10], bos_date=str(daily.index[s['bos_i']])[:10], notes='; '.join(notes), **plan)

def unflip(r, K):
    for lo, hi in PAIRS:
        if lo in r and hi in r: r[lo], r[hi] = K - r[hi], K - r[lo]
    for k in SINGLES:
        if k in r and r[k] is not None: r[k] = K - r[k]
    return r

def scan_symbol(daily, hourly=None, side='long', cfg=CFG):
    daily = daily.dropna(subset=['Open', 'High', 'Low', 'Close'])
    if len(daily) < cfg['min_bars'] or not liquid(daily, cfg): return None
    if side == 'short':
        K = float(2 * daily.High.max())
        r = detect(invert(daily, K), invert(hourly, K) if hourly is not None else None, cfg, K)
        if r and 'zone_bot' in r: r = unflip(r, K)
        return r
    return detect(daily, hourly, cfg)

# ------------------------------------------------------------------ data (yfinance)
def _yahoo(sym): return sym if sym.startswith('^') or sym.endswith('.NS') else sym + '.NS'

def _drop_partial_daily(df, now):
    if len(df) and df.index[-1].date() == now.date() and now.hour * 60 + now.minute < 15 * 60 + 45: return df.iloc[:-1]
    return df

def _drop_partial_hourly(df, now):
    if not len(df): return df
    start = df.index[-1]; end = min(start + pd.Timedelta(hours=1), start.normalize() + pd.Timedelta(hours=15, minutes=30))
    return df.iloc[:-1] if end > now else df

def _prep(d, h, now, multi, t):
    cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    dd = (d[t] if multi else d)[cols].dropna(); hh = (h[t] if multi else h)[cols].dropna()
    if dd.empty: raise ValueError('no daily data returned')
    hh.index = hh.index.tz_convert('Asia/Kolkata') if hh.index.tz is not None else hh.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
    return _drop_partial_daily(dd, now), _drop_partial_hourly(hh, now)

def fetch_all(symbols):
    import time, yfinance as yf
    tick = [_yahoo(s) for s in symbols]
    kw = dict(group_by='ticker', auto_adjust=False, threads=True, progress=False)
    if len(tick) == 1: kw['multi_level_index'] = False
    d = yf.download(tick, period='2y', interval='1d', **kw); h = yf.download(tick, period='3mo', interval='1h', **kw)
    now = pd.Timestamp.now(tz='Asia/Kolkata'); out, missing = {}, []
    for s, t in zip(symbols, tick):
        try: out[s] = _prep(d, h, now, len(tick) > 1, t)
        except Exception: missing.append((s, t))
    for s, t in missing:        # bulk downloads sometimes fail for a symbol (for example "database is locked"), so retry one by one
        err = None
        for attempt in range(3):
            try:
                time.sleep(1.0 + attempt)
                one = dict(auto_adjust=False, progress=False, threads=False, multi_level_index=False)
                out[s] = _prep(yf.download(t, period='2y', interval='1d', **one), yf.download(t, period='3mo', interval='1h', **one), now, False, t); err = None; break
            except Exception as ex: err = ex
        if err is not None: print(f'  skip {s} after retries: {err}', file=sys.stderr)
    return out

# ------------------------------------------------------------------ clean chart
def draw_chart(sym, daily, r, path, bars=70):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    d = daily.tail(bars); x = np.arange(len(d)); fig, ax = plt.subplots(figsize=(9, 5.2))
    for i, (o, h, l, c) in enumerate(zip(d.Open, d.High, d.Low, d.Close)):
        col = '#089981' if c >= o else '#F23645'; ax.plot([i, i], [l, h], color=col, lw=1); ax.add_patch(plt.Rectangle((i - .3, min(o, c)), .6, max(abs(c - o), 1e-9), color=col))
    ax.plot(x, ema(daily.Close, 9).tail(bars).values, color='#7c3aed', lw=1.6)
    ax.axhspan(r['zone_bot'], r['zone_top'], color='#2563eb', alpha=.14)
    ax.axhspan(r['fib_lo'], r['fib_hi'], color='#ca8a04', alpha=.30)
    for k, col in (('entry', '#2563eb'), ('sl', '#F23645'), ('t1', '#16803c'), ('t2', '#16803c'), ('t3', '#16803c')):
        if r.get(k) is not None and not (isinstance(r[k], float) and math.isnan(r[k])):
            ax.hlines(r[k], -1, len(d) + 1, color=col, ls='--', lw=1.2); ax.text(len(d) + 1.6, r[k], f"{k.upper()} {r[k]:,.1f}", color=col, va='center', fontsize=9)
    ax.set_xlim(-1, len(d) + 9); ax.set_xticks([]); ax.grid(axis='y', alpha=.2)
    for sp in ('top', 'right'): ax.spines[sp].set_visible(False)
    ax.set_title(f"{sym}  {r['side']}  {r['grade']} ({r['score']}/10)  {r['status']}", loc='left', fontsize=11)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)

# ------------------------------------------------------------------ json export (for the mobile page)
def _num(v):
    try: f = float(v)
    except (TypeError, ValueError): return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 2)

def export_json(path, rows, universe, scanned, failed, history, args):
    items = []
    for dd, r in rows:
        d = dd.tail(history)
        it = {k: _num(r.get(k)) for k in ('price', 'zone_bot', 'zone_top', 'entry', 'sl', 't1', 't2', 't3', 'r_t1', 'risk_pct', 'rvol',
                                         'fib_lo', 'fib_hi', 'ob_low', 'ob_high', 'fvg_low', 'fvg_high')}
        it.update(symbol=r['symbol'], side=r['side'], grade=r['grade'], score=int(r['score']), status=r['status'],
                  structure=int(r['structure']), zone_q=int(r['zone_q']), confirm=int(r['confirm']), context=int(r['context']),
                  ticks=[t for t in r['ticks'].split(', ') if t and t != '-'], notes=[n for n in r['notes'].split('; ') if n],
                  choch_date=r['choch_date'], bos_date=r['bos_date'], dates=[str(i)[:10] for i in d.index],
                  candles=[[round(float(x), 2) for x in row] for row in d[['Open', 'High', 'Low', 'Close']].values],
                  ema=[round(float(x), 2) for x in ema(dd.Close, 9).tail(history).values])
        items.append(it)
    payload = dict(generated_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat(timespec='seconds'), universe=universe, scanned=scanned,
                   failed=failed, side=args.side, min_score=args.min_score, setups=items)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f: json.dump(payload, f, separators=(',', ':'))
    print(f'saved {path} ({len(items)} setups, {scanned}/{universe} scanned)')

# ------------------------------------------------------------------ cli
def load_universe(args):
    if args.universe_csv:
        df = pd.read_csv(args.universe_csv); col = 'Symbol' if 'Symbol' in df.columns else df.columns[0]; return [str(s).strip() for s in df[col]]
    return [ln.strip() for ln in open(args.watchlist) if ln.strip() and not ln.startswith('#')]

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--watchlist', default='watchlist.txt'); p.add_argument('--universe-csv', help='NSE index constituents CSV with a Symbol column')
    p.add_argument('--side', choices=['long', 'short', 'both'], default='long')
    p.add_argument('--min-score', type=int, default=6); p.add_argument('--top', type=int, default=20)
    p.add_argument('--csv', default=None); p.add_argument('--charts', default=None, help='folder for clean PNG charts')
    p.add_argument('--risk', type=float, default=None, help='rupees to risk per trade, adds a quantity column')
    p.add_argument('--json', default=None, help='write results for the mobile page, e.g. _site/data/latest.json')
    p.add_argument('--history', type=int, default=70, help='daily candles stored per setup in the JSON')
    args = p.parse_args()
    syms = load_universe(args); print(f'Downloading {len(syms)} symbols...'); data = fetch_all(syms); rows = []
    failed = [s for s in syms if s not in data]
    if not data: print('No data downloaded (Yahoo blocked or offline). Nothing written.', file=sys.stderr); sys.exit(2)
    for s, (dd, hh) in data.items():
        for side in (['long', 'short'] if args.side == 'both' else [args.side]):
            try: r = scan_symbol(dd, hh, side)
            except Exception as ex: print(f'  error {s}: {ex}', file=sys.stderr); continue
            if r and r.get('score', 0) >= args.min_score and 'zone_bot' in r:
                r.update(symbol=s, side=side)
                if args.risk and r.get('entry') and r['entry'] != r['sl']: r['qty'] = int(args.risk // abs(r['entry'] - r['sl']))
                rows.append((dd, r))
    rows.sort(key=lambda t: (-t[1]['score'], 0 if t[1]['status'] == 'TRIGGERED' else 1)); rows = rows[:args.top]
    if args.json: export_json(args.json, rows, len(syms), len(data), failed, args.history, args)
    if not rows: print('No setups at this score. Try --min-score 4 or a wider watchlist.'); return
    cols = ['symbol', 'side', 'grade', 'score', 'status', 'price', 'zone_bot', 'zone_top', 'entry', 'sl', 't1', 't2', 'r_t1', 'ticks', 'rvol', 'bos_date', 'notes'] + (['qty'] if args.risk else [])
    df = pd.DataFrame([r for _, r in rows]).reindex(columns=cols)
    with pd.option_context('display.width', 250, 'display.max_columns', 30, 'display.float_format', '{:,.1f}'.format): print(df.drop(columns=['notes']).to_string(index=False))
    if args.csv: df.to_csv(args.csv, index=False); print('saved', args.csv)
    if args.charts:
        os.makedirs(args.charts, exist_ok=True)
        for dd, r in rows:
            try: draw_chart(r['symbol'], dd, r, os.path.join(args.charts, f"{r['symbol']}_{r['side']}.png"))
            except Exception as ex: print('  chart failed', r['symbol'], ex, file=sys.stderr)
        print('charts in', args.charts)

if __name__ == '__main__':
    main()
