# SOL/USDC Arb Monitor — Jupiter (Raydium-only) vs Jupiter (Orca-only)

Checks every minute whether buying SOL on Raydium and selling it on Orca
(or vice versa) would net a profit on a $1,000 round trip, after Solana
network fees. Posts a summary to Telegram every hour.

## Scope, and why it's built this way

You asked to compare Jupiter and PancakeSwap, but those are on different
chains (Solana vs BNB Chain) — there's no atomic arbitrage between them,
only a directional price-gap bet that needs pre-funded capital on both
sides. You confirmed **Solana-only: Jupiter vs Raydium/Orca** instead,
which is the version where real, same-block-ish arbitrage is possible.

Jupiter itself is not a DEX — it's a router that already scans Raydium,
Orca, and everything else on Solana for the best price. So instead of
scraping Raydium's and Orca's pools directly, this script asks **Jupiter's
own `/quote` endpoint**, restricted to one DEX at a time via the `dexes`
parameter. That gives Raydium-only and Orca-only prices computed the exact
same way Jupiter would compute them at execution time (fees and price
impact for your trade size already netted in), so the profit math is
realistic, not theoretical.

**Important, honest caveat:** because Jupiter already aggregates across
Raydium and Orca, a *positive* net edge here should be rare — it means
Jupiter's own router would have found and closed that gap already. The
hourly report is as much a "how efficient is this market right now"
gauge as it is an alert system. The script also logs what Jupiter's full
aggregator round-trip would have returned (`reference_edge_usd`) so you
can see that baseline.

## What it does NOT do

- **It does not trade.** No private key, no signing, no execution. It's a
  monitor. Turning a detected edge into an actual filled trade needs a
  wallet, transaction building/signing, and — because the two legs here
  are sequential, not atomic — either very fast execution or bundling
  both legs into one Jito bundle to avoid the price moving against you
  between leg 1 and leg 2. That's a separate, materially riskier project.
- **It is not financial advice**, and detected "edge" is a backtest-style
  estimate, not a guarantee — real slippage, MEV, and latency can erase it
  by the time an order lands.

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point, scheduler, Telegram report formatting |
| `arb_engine.py` | Core check: quote both directions, compute net edge |
| `jupiter_client.py` | Jupiter `/quote` HTTP client (+ offline mock mode) |
| `storage.py` | SQLite logging of every check, used for the hourly stats |
| `notifier.py` | Telegram sendMessage wrapper |
| `config.py` | All settings, loaded from environment / `.env` |
| `.env.example` | Copy to `.env` and fill in |
| `sol-arb-monitor.service` | Example systemd unit for 24/7 running |

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: at minimum set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

Get a Telegram bot token from **@BotFather** (`/newbot`), then get your
chat id by messaging the bot once and visiting
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

### Verify DEX labels before trusting the output

Jupiter's exact DEX label strings (`Raydium`, `Orca V2`, etc.) can change.
Check the current list:

```bash
curl https://lite-api.jup.ag/swap/v1/program-id-to-label | python3 -m json.tool | grep -i -E "raydium|orca"
```

and update `RAYDIUM_LABELS` / `ORCA_LABELS` in `.env` if they've drifted.
The script also does this check automatically at startup and logs a
warning if a configured label isn't currently recognized.

## Test it offline first (no network needed)

```bash
python main.py --self-test --cycles 8
```

This runs the full pipeline — quote parsing, profit math, SQLite writes,
report formatting — against synthetic data and prints a sample report, so
you can confirm everything works before pointing it at live APIs and a
real Telegram chat.

## Run it

```bash
python main.py
```

Runs forever: checks every 60s (`CHECK_INTERVAL_SEC`), reports every 3600s
(`REPORT_INTERVAL_SEC`). `Ctrl+C` stops it cleanly and sends a "stopped"
message to Telegram.

## Running 24/7 (production)

Don't leave it in a terminal — use systemd (Linux servers) so it survives
reboots and restarts on crash:

```bash
sudo useradd -r -s /bin/false solarb
sudo mkdir -p /opt/sol-arb-monitor
sudo cp -r . /opt/sol-arb-monitor
cd /opt/sol-arb-monitor && sudo -u solarb python3 -m venv venv
sudo -u solarb ./venv/bin/pip install -r requirements.txt
sudo cp sol-arb-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sol-arb-monitor
sudo journalctl -u sol-arb-monitor -f   # tail logs
```

No Linux server? A small VPS (Hetzner/DigitalOcean, ~$4-6/mo) or a free
`pm2`/`screen` session on any always-on machine both work fine — the
script is a single lightweight process (SQLite, one background thread).

## Rate limits

Jupiter's free tier (`lite-api.jup.ag`) is fine at one check per minute
(4-6 quote calls/minute). If you shorten `CHECK_INTERVAL_SEC`, add more
pairs, or hit 429s, get a free API key at
`https://developers.jup.ag/portal` and set `JUPITER_API_KEY` in `.env` —
the script automatically switches to the paid base URL when a key is set.

## Extending

- **More pairs:** the engine is written for one pair (SOL/USDC); to add
  more, parametrize `run_check()` over a list of `(base_mint, quote_mint)`
  tuples and loop, or run a second instance with its own `DB_PATH`.
- **More venues:** add more DEX label groups (e.g. Meteora, Lifinity) and
  compare all pairs of venues, not just Raydium vs Orca.
- **Execution:** would require a funded wallet, transaction
  signing (`/swap` endpoint), and ideally Jito bundling for both legs to
  land atomically — happy to help design that as a separate, carefully
  reviewed project if you want to go there.
