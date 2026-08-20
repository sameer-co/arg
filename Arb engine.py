"""
One check cycle = ask Jupiter's quote endpoint for a SOL/USDC price using
ONLY Raydium, then ONLY Orca, in both directions, and see whether buying on
the cheaper venue and selling on the other nets a profit after Solana
network fees.

Important: outAmount from Jupiter's /quote already nets out each pool's
AMM trading fee and the price impact for the requested size, so we don't
need to subtract swap fees separately — only the network/priority fee for
the two sequential transactions.

This measures a same-chain, near-atomic opportunity (both legs settle in
seconds on Solana), but it is NOT truly atomic unless bundled (e.g. via
Jito) — see README for that caveat.
"""
import logging
from datetime import datetime, timezone

import config
from jupiter_client import JupiterClient, JupiterError

log = logging.getLogger("arb_engine")


def _usdc_amount_units(usd: float) -> int:
    return int(round(usd * 10 ** config.USDC_DECIMALS))


def run_check(client: JupiterClient) -> dict:
    """Runs one full cycle. Returns a result dict (also written by caller to storage)."""
    ts = datetime.now(timezone.utc).isoformat()
    start_usdc = config.TRADE_SIZE_USD
    start_units = _usdc_amount_units(start_usdc)

    try:
        directions = [
            ("raydium_to_orca", config.RAYDIUM_LABELS, config.ORCA_LABELS, "Raydium", "Orca"),
            ("orca_to_raydium", config.ORCA_LABELS, config.RAYDIUM_LABELS, "Orca", "Raydium"),
        ]

        candidates = []
        for direction, buy_labels, sell_labels, buy_name, sell_name in directions:
            leg1 = client.quote(config.USDC_MINT, config.SOL_MINT, start_units, dexes=buy_labels)
            mid_sol_units = int(leg1["outAmount"])
            if mid_sol_units <= 0:
                continue

            leg2 = client.quote(config.SOL_MINT, config.USDC_MINT, mid_sol_units, dexes=sell_labels)
            final_usdc_units = int(leg2["outAmount"])

            mid_sol = mid_sol_units / 10 ** config.SOL_DECIMALS
            final_usdc = final_usdc_units / 10 ** config.USDC_DECIMALS
            gross_profit = final_usdc - start_usdc
            sol_price_usd = start_usdc / mid_sol if mid_sol else None

            candidates.append({
                "direction": direction,
                "buy_dex": buy_name,
                "sell_dex": sell_name,
                "start_usdc": start_usdc,
                "mid_sol": mid_sol,
                "final_usdc": final_usdc,
                "gross_profit_usd": gross_profit,
                "sol_price_usd": sol_price_usd,
            })

        if not candidates:
            return {"ts": ts, "error": "no valid quotes returned"}

        best = max(candidates, key=lambda c: c["gross_profit_usd"])

        network_fee_sol = config.TXS_PER_ARB * (
            config.BASE_FEE_SOL_PER_TX + config.PRIORITY_FEE_SOL_PER_TX
        )
        sol_price = best["sol_price_usd"] or 0.0
        network_fee_usd = network_fee_sol * sol_price
        net_edge = best["gross_profit_usd"] - network_fee_usd

        # Reference: what Jupiter's own full aggregator would have done on
        # the same round trip (no dex restriction). If this is also
        # negative, it confirms the aggregator already captured any real
        # edge and nothing is actually left on the table.
        reference_edge = None
        try:
            ref_leg1 = client.quote(config.USDC_MINT, config.SOL_MINT, start_units)
            ref_mid_sol_units = int(ref_leg1["outAmount"])
            ref_leg2 = client.quote(config.SOL_MINT, config.USDC_MINT, ref_mid_sol_units)
            ref_final_usdc = int(ref_leg2["outAmount"]) / 10 ** config.USDC_DECIMALS
            reference_edge = ref_final_usdc - start_usdc - network_fee_usd
        except JupiterError as exc:
            log.debug("reference quote failed (non-fatal): %s", exc)

        best.update({
            "ts": ts,
            "network_fee_usd": network_fee_usd,
            "net_edge_usd": net_edge,
            "reference_edge_usd": reference_edge,
            "error": None,
        })
        return best

    except JupiterError as exc:
        log.error("check cycle failed: %s", exc)
        return {"ts": ts, "error": str(exc)}
