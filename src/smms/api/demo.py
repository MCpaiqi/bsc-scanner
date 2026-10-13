"""Explicit development fixtures for previewing the dashboard offline."""

from datetime import UTC, datetime, timedelta
from typing import Any


def demo_bsc_tokens(limit: int = 18) -> dict[str, Any]:
    now = datetime.now(UTC)
    samples = [
        ("BYTE", "Byte Meme", 184000, 39200, 19800, 284000, 14.8, 58, 31, 22),
        ("NOVA", "Nova BSC", 92000, 24600, 12400, 131000, -6.3, 41, 28, 55),
        ("MOMO", "Momo Club", 375000, 68100, 9700, 218000, 32.7, 73, 36, 95),
        ("PEEL", "Peel Protocol", 61000, 18700, 6200, 97000, 4.1, 29, 19, 180),
        ("WAVE", "Green Wave", 510000, 89400, 15900, 412000, -2.8, 66, 52, 360),
        ("BOP", "Bop on BNB", 143000, 31700, 8300, 156000, 9.6, 34, 17, 720),
    ]
    tokens = []
    for index, sample in enumerate(samples):
        symbol, name, cap, liquidity, volume_m5, volume_h24, change, buys, sells, minutes = sample
        address = f"0x{index + 1:040x}"
        tokens.append(
            {
                "chain": "bsc",
                "token_address": address,
                "pair_address": f"0x{index + 101:040x}",
                "name": name,
                "symbol": symbol,
                "quote_symbol": "WBNB",
                "dex_id": "demo-pancakeswap",
                "price_usd": round(cap / 1_000_000_000, 8),
                "market_cap_usd": cap,
                "liquidity_usd": liquidity,
                "volume_m5": volume_m5,
                "volume_h1": volume_m5 * 3.4,
                "volume_h24": volume_h24,
                "price_change_m5": round(change / 7, 2),
                "price_change_h1": round(change / 3, 2),
                "price_change_h24": change,
                "txns_m5": {"buys": buys, "sells": sells},
                "txns_h1": {"buys": buys * 4, "sells": sells * 4},
                "pair_created_at": (now - timedelta(minutes=minutes)).isoformat(),
                "image_url": None,
                "description": "仅用于界面预览的演示代币",
                "socials": [],
                "dex_url": None,
                "smart_money_score": [78, 61, 84, None, 72, 55][index],
                "smart_wallet_count": [7, 3, 11, 0, 6, 2][index],
                "smart_money_buy_usd": [18400, 3200, 52100, 0, 9700, 1200][index],
                "early_smart_buy_count": [4, 1, 7, 0, 3, 1][index],
                "smart_money_top_wallets": [f"0x{index + 200:040x}"],
                "contract_risk": ["LOW", "HIGH", "CRITICAL", "UNKNOWN", "MEDIUM", "HIGH"][index],
                "liquidity_risk": ["MEDIUM", "HIGH", "LOW", "CRITICAL", "LOW", "HIGH"][index],
                "holder_risk": ["LOW", "MEDIUM", "HIGH", "UNKNOWN", "MEDIUM", "HIGH"][index],
                "behavior_risk": ["LOW", "MEDIUM", "HIGH", "UNKNOWN", "LOW", "HIGH"][index],
                "bot_probability": [0.08, 0.22, 0.72, None, 0.11, 0.66][index],
                "wash_probability": [0.04, 0.18, 0.61, None, 0.08, 0.58][index],
                "overall_risk": ["MEDIUM", "HIGH", "CRITICAL", "UNKNOWN", "LOW", "HIGH"][index],
                "risk_evidence": [["liquidity_observed"], ["mintable", "low_current_liquidity"], ["top_holder_concentration", "high_frequency"], [], ["holder_distribution_observed"], ["circular_trading", "high_sell_tax"]][index],
            }
        )
    return {
        "chain": "bsc",
        "source": "demo",
        "demo": True,
        "observed_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "freshness": "FRESH",
        "data_status": "DEMO",
        "tokens": tokens[:limit],
    }
