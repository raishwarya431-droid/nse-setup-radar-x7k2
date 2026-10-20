"""Self-test: builds a synthetic daily + 1H setup with clear swings and checks that every step is
detected (CHoCH, BOS, OB, FVG, fib, pullback, 1H trigger, score, trade plan, short mirror).
Run: python test_scanner.py"""
import sys
import numpy as np, pandas as pd
import strategy_scanner as sc

closes = [1115,1117,1118,1119,1119,1120,1112,1103,1094,1085,1088,1093,1097,1100,1090,1079,1069,1062,1067,1072,1076,1080,1071,1060,1049,1040,
          1047,1056,1065,1074,1090,1084,1078,1112,1122,1130,1122,1114,1106,1100,1096,1094]
wick_up = {5: 5, 13: 5, 21: 5}; wick_dn = {9: 5, 17: 5, 25: 5}
vols = {30: 190, 33: 220, 25: 200}
def daily_rows():
    rows = []; prev = 1114
    for i, c in enumerate(closes):
        o = prev; h = max(o, c) + wick_up.get(i, 2); l = min(o, c) - wick_dn.get(i, 2)
        if i == 25: l = 1028
        rows.append([o, h, l, c, vols.get(i, 100)]); prev = c
    return rows
D = daily_rows()

h1c = [1122,1120,1123,1116,1112,1115,1108,1104,1107,1100,1096,1099,1094,1090,1093,1088,1094,1096,1093,1092,1101,1103]
def hourly_rows():
    rows = []; prev = 1124
    for i, c in enumerate(h1c):
        o = prev; h = max(o, c) + 3; l = min(o, c) - 3
        if i == 15: l = 1083
        rows.append([o, h, l, c, 175 if i == 20 else 100 + (i % 4) * 5]); prev = c
    return rows
H = hourly_rows()

def frame(rows, freq, tz=None):
    idx = pd.date_range('2026-01-01', periods=len(rows), freq=freq, tz=tz)
    return pd.DataFrame(rows, columns=['Open', 'High', 'Low', 'Close', 'Volume'], index=idx)

CFG = dict(sc.CFG, pivot=3, min_turnover_cr=0, min_price=0, min_bars=30, rvol_n=10)
fails = []
def check(name, cond, info=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'   {info}' if info else ''))
    if not cond: fails.append(name)

h = [r[1] for r in D]; l = [r[2] for r in D]; o = [r[0] for r in D]
daily = frame(D, 'D'); hourly = frame(H, 'h', 'Asia/Kolkata')
r = sc.scan_symbol(daily, hourly, 'long', CFG)
check('long setup found', r is not None and 'zone_bot' in r, str(r and r.get('state')))
if r and 'zone_bot' in r:
    check('CHoCH on bar 30', r['choch_date'] == str(daily.index[30])[:10] and abs(r['choch_level'] - h[21]) < .01, f"{r['choch_date']} level {r['choch_level']}")
    check('BOS on bar 33', r['bos_date'] == str(daily.index[33])[:10], r['bos_date'])
    check('OB = last down candle before BOS', abs(r['ob_low'] - l[32]) < .01 and abs(r['ob_high'] - o[32]) < .01, f"{r['ob_low']}-{r['ob_high']}")
    check('FVG = gap between bar 32 high and bar 34 low', abs(r['fvg_low'] - h[32]) < .01 and abs(r['fvg_high'] - l[34]) < .01, f"{r['fvg_low']}-{r['fvg_high']}")
    rng = max(h[33:]) - l[32]
    check('fib 0.618-0.705 of the leg', abs(r['fib_hi'] - (max(h[33:]) - .618 * rng)) < .01 and abs(r['fib_lo'] - (max(h[33:]) - .705 * rng)) < .01, f"{r['fib_lo']:.1f}-{r['fib_hi']:.1f}")
    check('price is in the zone and the 1H trigger fired', r['state'] == 'IN_ZONE' and r['status'] == 'TRIGGERED', f"{r['state']} / {r['status']}")
    check('confirmation 3/3 (rejection, EMA reclaim, volume)', r['confirm'] == 3, f"confirm={r['confirm']} rvol={r['rvol']:.2f}")
    check('three confluence ticks', all(t in r['ticks'] for t in ['key level', 'fib', 'MTF EMA']), r['ticks'])
    check('score 10 = A+', r['score'] == 10 and r['grade'] == 'A+', f"{r['score']} {r['grade']}")
    check('plan: SL below the OB, T2 = 2R', r['sl'] < r['ob_low'] and abs((r['t2'] - r['entry']) - 2 * (r['entry'] - r['sl'])) < .01,
          f"entry {r['entry']:.1f} sl {r['sl']:.1f} t1 {r['t1']:.1f} ({r['r_t1']:.1f}R) t2 {r['t2']:.1f}")

r2 = sc.scan_symbol(daily, frame(H[:14], 'h', 'Asia/Kolkata'), 'long', CFG)
check('no 1H trigger yet -> WATCH / APPROACHING', r2 is not None and 'zone_bot' in r2 and not r2['status'].startswith('TRIGGERED'), str(r2 and r2.get('status')))

r3 = sc.scan_symbol(frame(D[:36], 'D'), None, 'long', CFG)
check('price just above the zone -> APPROACHING (set an alert)', r3 is not None and 'zone_bot' in r3 and r3['status'].startswith('APPROACHING'), str(r3 and r3.get('status')))
r3b = sc.scan_symbol(frame(D[:36] + [[1130, 1160, 1128, 1156, 100]], 'D'), None, 'long', CFG)
check('price far above the zone is not reported', r3b is None or 'zone_bot' not in r3b, str(r3b and r3b.get('state')))

r4 = sc.scan_symbol(frame(D + [[1094, 1095, 1040, 1048, 200]], 'D'), None, 'long', CFG)
check('closing below the zone cancels the setup', r4 is None or 'zone_bot' not in r4, str(r4 and r4.get('state')))

flip = lambda rows: [[2300 - a, 2300 - c_, 2300 - b, 2300 - d_, v] for a, b, c_, d_, v in rows]
rs = sc.scan_symbol(frame(flip(D), 'D'), frame(flip(H), 'h', 'Asia/Kolkata'), 'short', CFG)
check('short setup found on mirrored data', rs is not None and 'zone_bot' in rs, str(rs and rs.get('state')))
if rs and 'zone_bot' in rs:
    check('short zone mirrors the long zone', abs(rs['zone_bot'] - (2300 - r['zone_top'])) < .01 and abs(rs['zone_top'] - (2300 - r['zone_bot'])) < .01, f"{rs['zone_bot']}-{rs['zone_top']}")
    check('short: SL above the zone, targets below entry', rs['sl'] > rs['zone_top'] and rs['t2'] < rs['entry'], f"entry {rs['entry']:.1f} sl {rs['sl']:.1f} t2 {rs['t2']:.1f}")
    check('short score 10', rs['score'] == 10, str(rs['score']))

print('\nALL PASSED' if not fails else f'\n{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
