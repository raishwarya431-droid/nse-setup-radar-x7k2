"""Tests the download layer with a stand-in for yfinance (no internet needed): bulk download misses one symbol
(like the 'database is locked' error seen in the live log) and the retry must recover it."""
import sys, types
import numpy as np, pandas as pd

calls = []
def fake_download(tickers, period=None, interval=None, group_by=None, auto_adjust=False, threads=True, progress=False, multi_level_index=True):
    single = isinstance(tickers, str); names = [tickers] if single else list(tickers)
    calls.append((tuple(names), interval, threads))
    n = 300 if interval == '1d' else 120
    idx = pd.date_range('2025-01-01', periods=n, freq='D' if interval == '1d' else 'h', tz=None if interval == '1d' else 'Asia/Kolkata')
    frames = {}
    for t in names:
        base = 100 + np.cumsum(np.ones(n))
        bad = t == 'BADLOCK.NS' and not single and len(names) > 1          # fails only in the bulk call
        gone = t == 'GONE.NS'                                              # never returns data
        vals = np.nan if (bad or gone) else 1.0
        frames[t] = pd.DataFrame({'Open': base * vals, 'High': base * vals + 2, 'Low': base * vals - 2, 'Close': base * vals + 1, 'Volume': 1e6 * vals}, index=idx)
    if single and not multi_level_index: return frames[names[0]]
    return pd.concat(frames, axis=1)
sys.modules['yfinance'] = types.SimpleNamespace(download=fake_download)

import strategy_scanner as sc
fails = []
def check(name, cond, info=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'   {info}' if info else ''))
    if not cond: fails.append(name)

data = sc.fetch_all(['AAA', 'BADLOCK', 'GONE'])
check('bulk download returns the healthy symbol', 'AAA' in data and len(data['AAA'][0]) > 200)
check('a symbol that failed in the bulk call is recovered by the retry', 'BADLOCK' in data and len(data['BADLOCK'][0]) > 200)
check('a symbol that never returns data is skipped, not counted', 'GONE' not in data)
check('retries run one symbol at a time without threads', any(c[0] == ('BADLOCK.NS',) and c[2] is False for c in calls))
solo = sc.fetch_all(['AAA'])
check('a single-symbol watchlist works', 'AAA' in solo)
print('\nALL PASSED' if not fails else f'\n{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
