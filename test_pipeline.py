"""Tests the phone alert path without internet: a local server stands in for ntfy.
Needs /tmp/site_test/data/latest.json from a scanner run, or pass --json. Run: python test_pipeline.py [latest.json]"""
import http.server, json, os, subprocess, sys, tempfile, threading

js = sys.argv[1] if len(sys.argv) > 1 else '/tmp/site_test/data/latest.json'
got = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); got.append((self.path, dict(self.headers), self.rfile.read(n).decode('utf-8'))); self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def log_message(self, *a): pass
srv = http.server.HTTPServer(('127.0.0.1', 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
state = os.path.join(tempfile.mkdtemp(), 'notified.json')
env = dict(os.environ, NTFY_TOPIC='my-secret-topic', NTFY_URL=f'http://127.0.0.1:{srv.server_port}', NTFY_MIN_SCORE='6', PAGE_URL='https://example.github.io/scan/')
run = lambda: subprocess.run([sys.executable, 'notify.py', '--json', js, '--state', state], env=env, capture_output=True, text=True).stdout.strip()
fails = []
def check(name, cond, info=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'   {info}' if info else ''))
    if not cond: fails.append(name)

out1 = run()
check('first run sends the TRIGGERED setups only', len(got) == 2 and all('TRIGGERED' in g[1]['Title'] for g in got), out1)
check('posts to the topic with ASCII title, tags and click link', got[0][0] == '/my-secret-topic' and got[0][1].get('Click') == 'https://example.github.io/scan/' and got[0][1]['Title'].isascii(), got[0][1]['Title'])
check('message has zone, entry, SL, targets and the OI reminder', all(w in got[0][2] for w in ('Zone', 'Entry', 'SL', 'T1', 'T2', 'futures OI')), got[0][2].replace('\n', ' | '))
n = len(got); out2 = run()
check('second run sends nothing (once per day)', len(got) == n and 'alerts sent: 0' in out2, out2)
env['NTFY_STATUSES'] = 'TRIGGERED,WATCH'; out3 = run()
check('WATCH can be switched on and is sent once', len(got) == n + 1 and 'WATCH' in got[-1][1]['Title'], out3)
env.pop('NTFY_TOPIC'); out4 = run()
check('no topic set -> skips quietly', 'skipping' in out4, out4)
print('\nALL PASSED' if not fails else f'\n{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
