#!/usr/bin/env python3
"""Push new TRIGGERED setups to your iPhone through ntfy (free app + service, https://ntfy.sh).

Env vars:
  NTFY_TOPIC       required. Pick a long, random topic name; anyone who knows it can read the alerts.
  NTFY_MIN_SCORE   default 6 (A and above)
  NTFY_STATUSES    default TRIGGERED. Comma list, e.g. TRIGGERED,WATCH
  PAGE_URL         optional. Opens when you tap the notification.
  NTFY_URL         default https://ntfy.sh
Each setup is sent once per day (state kept in --state)."""
import argparse, datetime, json, os, sys, urllib.request

def ist_today():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')

def fmt(v):
    return '-' if v is None else f'{v + 1e-9:,.1f}'

def message(s):
    lines = [f"Zone {fmt(s['zone_bot'])}-{fmt(s['zone_top'])}   Entry {fmt(s['entry'])}   SL {fmt(s['sl'])}",
             f"T1 {fmt(s['t1'])} ({fmt(s['r_t1'])}R)   T2 {fmt(s['t2'])}",
             'Ticks: ' + (', '.join(s['ticks']) or 'none')]
    lines.append('Check futures OI and the results date before trading.')
    return '\n'.join(lines)

def send(base, topic, s, click):
    title = f"{s['status'].split(' ')[0]} {s['grade']} {s['symbol']} {s['side']} ({s['score']}/10)"
    headers = {'Title': title.encode('ascii', 'ignore').decode(), 'Priority': '4' if s['grade'] == 'A+' else '3',
               'Tags': 'chart_with_upwards_trend' if s['side'] == 'long' else 'chart_with_downwards_trend'}
    if click: headers['Click'] = click
    req = urllib.request.Request(f'{base}/{topic}', data=message(s).encode('utf-8'), headers=headers, method='POST')
    urllib.request.urlopen(req, timeout=20).read()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--json', required=True); ap.add_argument('--state', default='.state/notified.json')
    a = ap.parse_args()
    topic = os.environ.get('NTFY_TOPIC', '').strip()
    if not topic: print('NTFY_TOPIC not set, skipping alerts.'); return 0
    base = os.environ.get('NTFY_URL', 'https://ntfy.sh').rstrip('/')
    min_score = int(os.environ.get('NTFY_MIN_SCORE', '6'))
    statuses = tuple(x.strip().upper() for x in os.environ.get('NTFY_STATUSES', 'TRIGGERED').split(',') if x.strip())
    click = os.environ.get('PAGE_URL', '').strip()
    data = json.load(open(a.json)); today = ist_today()
    try: state = json.load(open(a.state))
    except Exception: state = {}
    if state.get('date') != today: state = {'date': today, 'sent': []}
    sent = 0
    for s in data.get('setups', []):
        key = f"{s['symbol']}|{s['side']}|{s['bos_date']}|{s['status'].split(' ')[0]}"
        if s['score'] < min_score or not s['status'].upper().startswith(statuses) or key in state['sent']: continue
        try: send(base, topic, s, click); state['sent'].append(key); sent += 1
        except Exception as ex: print(f"  could not send {s['symbol']}: {ex}", file=sys.stderr)
    os.makedirs(os.path.dirname(a.state) or '.', exist_ok=True); json.dump(state, open(a.state, 'w'))
    print(f'alerts sent: {sent}'); return 0

if __name__ == '__main__':
    sys.exit(main())
