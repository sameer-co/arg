#!/usr/bin/env python3
"""
SOL/USDC arbitrage monitor: Jupiter-restricted-to-Raydium vs
Jupiter-restricted-to-Orca, checked every minute, reported to Telegram
every hour.

Usage:
    python main.py                 # run continuously (production)
    python main.py --self-test     # run a handful of cycles offline with
                                    # mocked data, print a report, and exit
                                    # (validates logic without hitting any
                                    # network — useful for a first smoke test)

See README.md for setup, deployment (systemd), and important caveats
about non-atomic execution.
"""
import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import config
import storage
from arb_engine import run_check
from jupiter_client import JupiterClient
from notifier import TelegramNotifier

log = logging.getLogger("main")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_PATH),
        ],
    )


def validate_dex_labels(client: JupiterClient):
    """Best-effort sanity check that configured DEX labels still exist."""
    mapping = client.fetch_dex_labels()
    if not mapping:
        return
    known_labels = set(mapping.values())
    for name, labels in (("RAYDIUM_LABELS", config.RAYDIUM_LABELS), ("ORCA_LABELS", config.ORCA_LABELS)):
        missing = [l for l in labels if l not in known_labels]
        if missing:
            log.warning(
                "%s contains labels not currently returned by "
                "/program-id-to-label: %s. Check %s/program-id-to-label "
                "and update your .env if routing looks wrong.",
                name, missing, config.JUPITER_BASE_URL,
            )


def format_check_line(result: dict) -> str:
    if result.get("error"):
        return f"check failed: {result['error']}"
    edge = result["net_edge_usd"]
    flag = "✅" if edge > 0 else "—"
    return (
        f"{flag} buy {result['buy_dex']} → sell {result['sell_dex']} | "
        f"gross {result['gross_profit_usd']:+.3f} USD | "
        f"net edge {edge:+.3f} USD"
    )


def format_hourly_report(stats: dict, window_label: str) -> str:
    lines = [f"<b>SOL/USDC arb monitor — {window_label}</b>"]
    if stats["count"] == 0:
        lines.append(f"No successful checks in this window (errors: {stats['error_count']}).")
        return "\n".join(lines)

    lines.append(f"Checks: {stats['count']} (errors: {stats['error_count']})")
    lines.append(f"Positive net edge: {stats['positive_count']}/{stats['count']}")
    lines.append(
        f"Net edge — min {stats['min_edge']:+.3f} / avg {stats['avg_edge']:+.3f} / "
        f"max {stats['max_edge']:+.3f} USD (on ${config.TRADE_SIZE_USD:.0f} trades)"
    )
    b = stats["best"]
    lines.append(
        f"Best opportunity: buy {b['buy_dex']} → sell {b['sell_dex']} at {b['ts']}, "
        f"net edge {b['net_edge_usd']:+.3f} USD"
    )
    if stats["max_edge"] <= 0:
        lines.append(
            "No profitable window this hour after costs — expected most of the "
            "time, since Jupiter's own aggregator already routes around most "
            "single-DEX mispricing."
        )
    return "\n".join(lines)


def make_check_job(client: JupiterClient, notifier: TelegramNotifier):
    def _job():
        result = run_check(client)
        storage.insert_check(result)
        log.info(format_check_line(result))

        if not result.get("error") and config.ALERT_ENABLED:
            if result["net_edge_usd"] >= config.ALERT_THRESHOLD_USD:
                notifier.send(
                    f"⚡ <b>Edge alert</b>\n{format_check_line(result)}\n"
                    f"(threshold: {config.ALERT_THRESHOLD_USD:.2f} USD)"
                )
    return _job


def make_report_job(notifier: TelegramNotifier):
    def _job():
        since = (datetime.now(timezone.utc) - timedelta(seconds=config.REPORT_INTERVAL_SEC)).isoformat()
        stats = storage.stats_since(since)
        window_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        notifier.send(format_hourly_report(stats, window_label))
    return _job


def run_self_test(cycles: int = 5):
    setup_logging()
    log.info("Running in --self-test mode: no real network calls, mocked Jupiter data.")
    storage.init_db()
    client = JupiterClient(test_mode=True)
    notifier = TelegramNotifier(test_mode=True)

    for i in range(cycles):
        result = run_check(client)
        storage.insert_check(result)
        print(f"cycle {i+1}/{cycles}: {format_check_line(result)}")
        time.sleep(0.05)

    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    stats = storage.stats_since(since)
    report = format_hourly_report(stats, "self-test window")
    print("\n--- sample hourly report ---")
    print(report)
    notifier.send(report)
    print("\nself-test OK: quote parsing, profit calc, DB storage and report "
          "formatting all ran without error.")


def run_forever():
    setup_logging()
    log.info("Starting SOL/USDC arb monitor (check every %ss, report every %ss)",
              config.CHECK_INTERVAL_SEC, config.REPORT_INTERVAL_SEC)
    storage.init_db()

    client = JupiterClient(test_mode=False)
    notifier = TelegramNotifier(test_mode=False)
    validate_dex_labels(client)

    notifier.send(
        f"🟢 SOL/USDC arb monitor started. Checking every "
        f"{config.CHECK_INTERVAL_SEC}s, trade size ${config.TRADE_SIZE_USD:.0f}, "
        f"reporting every {config.REPORT_INTERVAL_SEC // 60} min."
    )

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(make_check_job(client, notifier), "interval",
                       seconds=config.CHECK_INTERVAL_SEC, id="check", max_instances=1,
                       coalesce=True, next_run_time=datetime.now())
    scheduler.add_job(make_report_job(notifier), "interval",
                       seconds=config.REPORT_INTERVAL_SEC, id="report",
                       max_instances=1, coalesce=True)
    scheduler.start()

    def _shutdown(signum, frame):
        log.info("Shutting down (signal %s)...", signum)
        notifier.send("🔴 SOL/USDC arb monitor stopped.")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SOL/USDC Jupiter vs Raydium/Orca arb monitor")
    parser.add_argument("--self-test", action="store_true",
                         help="Run a few mocked cycles offline and print a report, then exit.")
    parser.add_argument("--cycles", type=int, default=5, help="Cycles to run in --self-test mode")
    args = parser.parse_args()

    if args.self_test:
        run_self_test(args.cycles)
    else:
        run_forever()
