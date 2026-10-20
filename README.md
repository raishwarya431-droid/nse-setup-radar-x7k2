# Strategy scanner for iPhone

The scan runs in the cloud on a schedule (GitHub Actions, free). You read the results in a mobile web page that you add to your Home Screen, and new TRIGGERED setups can push a notification to your phone. Nothing runs on the iPhone itself.

```
GitHub Actions (cron)  ->  strategy_scanner.py  ->  latest.json  ->  GitHub Pages  ->  Home Screen app (Safari)
                                  |-> notify.py  ->  ntfy  ->  push notification
```

## One-time setup (about 15 minutes, on a computer)
1. Create a GitHub repository. Pages on a private repo needs a paid plan, so use a public repo with a hard-to-guess name. The page and your watchlist are then public to anyone with the link. Never put keys or tokens in the repo.
2. Upload everything in this folder, including the hidden `.github` folder. With git:
   ```
   git init && git add . && git commit -m "scanner" && git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main
   ```
3. Repo Settings > Pages > Build and deployment > Source: **GitHub Actions**.
4. Optional phone alerts: install the free **ntfy** app on the iPhone and subscribe to a long random topic name (anyone who knows the topic can read it). Then in the repo, Settings > Secrets and variables > Actions: add a secret `NTFY_TOPIC` with that name, and a variable `PAGE_URL` with your page address (tapping a notification opens it). Optional variable `NTFY_MIN_SCORE` (default 6).
5. Actions tab > **scan** > Run workflow. After about 2 minutes the page is live at `https://<you>.github.io/<repo>/`.
6. On the iPhone, open that address in Safari > Share > **Add to Home Screen**.

Edit `watchlist.txt` in the GitHub app or Safari and commit. The next run uses it.

## Schedule (IST, Monday to Friday)
| Run | Why |
|---|---|
| 08:30 | Pre-market list from yesterday's close |
| 10:35, 11:35, 12:35, 13:35, 14:35 | 20 minutes after each 1H candle closes, so the 1H trigger is current (Yahoo's NSE intraday data can lag about 15 minutes) |
| 16:15 | After the close, with the final daily candle |

Edit the `cron` lines in `.github/workflows/scan.yml` to change it (times there are UTC). GitHub can start scheduled runs several minutes late.

## Using the page
- Chips filter by status (Triggered, In zone, Approaching), side and minimum score. Your choices are remembered.
- Tap a card for the chart (daily candles, 9 EMA, zone, fib pocket, entry, SL, targets), the structure dates, the 1H volume and a quantity calculator (your risk in rupees is remembered).
- Open in TradingView jumps to the chart. Copy levels copies the plan.
- Refresh reloads the latest results. The header turns red if the data is more than 30 hours old.

## Run it now from the phone
Safari: repo > Actions > scan > Run workflow. Or build an iOS Shortcut with "Get Contents of URL": `POST https://api.github.com/repos/<you>/<repo>/actions/workflows/scan.yml/dispatches`, headers `Authorization: Bearer <fine-grained token with Actions: write on this repo only>`, `Accept: application/vnd.github+json`, and a JSON body `{"ref":"main"}`. Keep the token only inside the Shortcut.

## What can go wrong
- Yahoo sometimes blocks cloud IP addresses. If nothing downloads, the run fails, GitHub emails you, and the page keeps the last good results (stale warning after 30 hours). The fix is a broker API (Upstox, Angel One, Dhan, Zerodha): feed the two DataFrames to `scan_symbol()`, keep the key in a repo secret.
- Public repos pause scheduled workflows after 60 days without repo activity. Re-enable it in the Actions tab, or commit anything.
- Free usage limits apply to Actions and Pages. This setup uses a few minutes a day.

## Rules and limits
Same engine as the desktop scanner: daily CHoCH + BOS, FVG + order block, pullback into the zone, 1H rejection, 9 EMA reclaim, volume at least 1.5x, three confluence ticks, scored on your 10-point card. All thresholds are in `CFG` at the top of `strategy_scanner.py`. Tests: `python test_scanner.py` and `python test_pipeline.py`.

Futures OI, the results date and sector or Nifty strength stay manual and are flagged on every card. The scan finds candidates by rules, so check the chart yourself. It is not a trade signal and not investment advice.
