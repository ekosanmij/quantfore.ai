"""Generate the deterministic Sprint 5 synthetic price panel.

The output is engineering test data.  It does not represent real securities or
support investment-performance claims.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_PACKAGE = REPO_ROOT / "packages" / "research"
if str(RESEARCH_PACKAGE) not in sys.path:
    sys.path.insert(0, str(RESEARCH_PACKAGE))

from quantfore_research.backtest import BACKTEST_CONTRACT  # noqa: E402


RANDOM_SEED = 20250302
SESSION_COUNT = 1_000
START_DATE = date(2021, 1, 4)
DEFAULT_OUTPUT = REPO_ROOT / "data" / "sample" / "synthetic_backtest_prices.csv"
SYNTHETIC_WARNING = (
    "SYNTHETIC ENGINEERING DATA ONLY - ALL PRICES AND VOLUMES ARE FICTIONAL "
    "- NOT VALIDATION EVIDENCE"
)
CSV_COLUMNS = (
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "synthetic_warning",
    "synthetic_pattern",
)


@dataclass(frozen=True)
class SyntheticProfile:
    name: str
    drift: float
    volatility: float
    beta: float
    cycle_amplitude: float
    cycle_length: int
    drawdown_start: int
    drawdown_days: int
    drawdown_shock: float


@dataclass(frozen=True)
class SyntheticPrice:
    open: float
    high: float
    low: float
    close: float
    volume: int


def weekday_sessions(*, start: date, count: int) -> tuple[date, ...]:
    """Return aligned Monday-Friday sessions, deliberately excluding holidays."""

    sessions: list[date] = []
    current = start
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return tuple(sessions)


def profile_for(ticker: str) -> SyntheticProfile:
    """Return a stable pattern profile for one fictional security."""

    index = int(ticker.removeprefix("QF"))
    group = (index - 1) // 5
    within_group = (index - 1) % 5
    profile_names = (
        "positive_momentum",
        "flat_to_negative_momentum",
        "cyclical_momentum",
        "high_volatility_drawdown",
    )
    group_drifts = (0.00055, -0.00022, 0.00012, 0.00028)
    group_volatility = (0.0040, 0.0060, 0.0085, 0.0120)
    group_cycle_amplitude = (0.00015, 0.00035, 0.00120, 0.00065)
    group_drawdown_shock = (-0.004, -0.007, -0.010, -0.018)

    return SyntheticProfile(
        name=profile_names[group],
        drift=group_drifts[group] + ((2 - within_group) * 0.000035),
        volatility=group_volatility[group] + (within_group * 0.0007),
        beta=0.65 + (group * 0.18) + (within_group * 0.07),
        cycle_amplitude=group_cycle_amplitude[group]
        * (0.8 + within_group * 0.12),
        cycle_length=32 + (group * 21) + (within_group * 9),
        drawdown_start=310 + (group * 105) + (within_group * 73),
        drawdown_days=5 + (group * 4) + within_group,
        drawdown_shock=group_drawdown_shock[group]
        * (0.85 + within_group * 0.08),
    )


def _market_returns(rng: random.Random) -> tuple[float, ...]:
    returns = []
    for session_index in range(SESSION_COUNT):
        cycle = 0.00035 * math.sin(session_index / 43.0)
        noise = (rng.random() * 2.0 - 1.0) * 0.0045
        drawdown = -0.009 if 520 <= session_index < 536 else 0.0
        recovery = 0.0035 if 545 <= session_index < 575 else 0.0
        returns.append(0.00025 + cycle + noise + drawdown + recovery)
    return tuple(returns)


def _security_returns(
    profile: SyntheticProfile,
    *,
    market_returns: tuple[float, ...],
    rng: random.Random,
) -> tuple[float, ...]:
    returns = []
    drawdown_end = profile.drawdown_start + profile.drawdown_days
    recovery_end = drawdown_end + (profile.drawdown_days * 2)
    for session_index, market_return in enumerate(market_returns):
        cycle = profile.cycle_amplitude * math.sin(
            (2.0 * math.pi * session_index) / profile.cycle_length
        )
        noise = (rng.random() * 2.0 - 1.0) * profile.volatility
        event_return = 0.0
        if profile.drawdown_start <= session_index < drawdown_end:
            event_return = profile.drawdown_shock
        elif drawdown_end <= session_index < recovery_end:
            event_return = abs(profile.drawdown_shock) * 0.32
        security_return = (
            profile.drift
            + profile.beta * (market_return - 0.00025)
            + cycle
            + noise
            + event_return
        )
        returns.append(max(security_return, -0.25))
    return tuple(returns)


def _prices_from_returns(
    daily_returns: tuple[float, ...],
    *,
    start_price: float,
    base_volume: int,
    rng: random.Random,
) -> tuple[SyntheticPrice, ...]:
    prices = []
    previous_close = start_price
    for daily_return in daily_returns:
        close = max(previous_close * (1.0 + daily_return), 1.0)
        gap = (rng.random() * 2.0 - 1.0) * 0.0015
        open_price = previous_close * (1.0 + gap)
        spread = 0.0015 + (abs(daily_return) * 0.30) + (rng.random() * 0.001)
        high = max(open_price, close) * (1.0 + spread)
        low = min(open_price, close) * (1.0 - spread)
        volume_multiplier = 0.82 + (rng.random() * 0.30) + min(
            abs(daily_return) * 9.0,
            0.60,
        )
        prices.append(
            SyntheticPrice(
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=int(base_volume * volume_multiplier),
            )
        )
        previous_close = close
    return tuple(prices)


def generate_panel(*, seed: int = RANDOM_SEED) -> tuple[tuple[object, ...], ...]:
    """Generate date-major rows for all contract securities and SPY."""

    rng = random.Random(seed)
    sessions = weekday_sessions(start=START_DATE, count=SESSION_COUNT)
    market_returns = _market_returns(rng)
    price_series: dict[str, tuple[SyntheticPrice, ...]] = {}
    pattern_names: dict[str, str] = {}

    price_series[BACKTEST_CONTRACT.benchmark] = _prices_from_returns(
        market_returns,
        start_price=250.0,
        base_volume=55_000_000,
        rng=rng,
    )
    pattern_names[BACKTEST_CONTRACT.benchmark] = "benchmark_market_cycle"

    for ticker_index, ticker in enumerate(BACKTEST_CONTRACT.ranked_universe, start=1):
        profile = profile_for(ticker)
        price_series[ticker] = _prices_from_returns(
            _security_returns(profile, market_returns=market_returns, rng=rng),
            start_price=35.0 + (ticker_index * 4.5),
            base_volume=650_000 + (ticker_index * 85_000),
            rng=rng,
        )
        pattern_names[ticker] = profile.name

    rows = []
    for session_index, session_date in enumerate(sessions):
        for ticker in BACKTEST_CONTRACT.price_panel_universe:
            price = price_series[ticker][session_index]
            rows.append(
                (
                    ticker,
                    session_date.isoformat(),
                    f"{price.open:.6f}",
                    f"{price.high:.6f}",
                    f"{price.low:.6f}",
                    f"{price.close:.6f}",
                    f"{price.close:.6f}",
                    price.volume,
                    SYNTHETIC_WARNING,
                    pattern_names[ticker],
                )
            )
    return tuple(rows)


def write_dataset(
    output_path: Path = DEFAULT_OUTPUT,
    *,
    seed: int = RANDOM_SEED,
) -> str:
    """Write the panel and return its SHA-256 hash."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        writer.writerows(generate_panel(seed=seed))
    return hashlib.sha256(output_path.read_bytes()).hexdigest()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic Sprint 5 backtest prices."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    dataset_hash = write_dataset(args.output)
    print(
        f"generated synthetic backtest panel rows={SESSION_COUNT * 21} "
        f"sessions={SESSION_COUNT} securities=20 benchmark=SPY "
        f"seed={RANDOM_SEED} sha256={dataset_hash} output={args.output}"
    )
    print("SYNTHETIC ENGINEERING DATA - NOT VALIDATION EVIDENCE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
