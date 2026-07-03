"""Build the private composite point-in-time equity bundle from frozen evidence."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from quantfore_research.ingest.free_point_in_time import (
    derive_membership_episodes,
    membership_on,
    parse_membership_history,
    tiingo_ticker,
)
from quantfore_research.validation.price_quality import exchange_sessions


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIMARY = REPO_ROOT / "data/raw/free-point-in-time/primary-b792557e915703398ef9a67e4b583a37c6ec80d5.csv"
DEFAULT_IDENTIFIERS = REPO_ROOT / "data/raw/free-point-in-time/resolved-identifiers-v1.json"
DEFAULT_RECONCILED = REPO_ROOT / "data/raw/free-point-in-time/reconciled-lineage-v1.json"
DEFAULT_MEMBERSHIP_SAMPLES = REPO_ROOT / "data/raw/free-point-in-time/wikipedia-membership-samples-v1/registry.json"
DEFAULT_DELISTING = REPO_ROOT / "data/raw/free-point-in-time/delisting-evidence-v1.json"
DEFAULT_EXCLUSIONS = REPO_ROOT / "data/raw/free-point-in-time/price-exclusions-v1.json"
DEFAULT_LICENSE = REPO_ROOT / "data/raw/free-point-in-time/license-evidence/personal-internal-use-v1.json"
DEFAULT_PRICE_ROOTS = (
    REPO_ROOT / "data/raw/free-point-in-time/tiingo-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v2",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v3",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v4",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v5",
)
DEFAULT_OUTPUT = REPO_ROOT / "data/raw/free-point-in-time/composite-equity-bundle-v1"
WINDOW_START = date(2017, 1, 1)
WINDOW_END = date(2025, 6, 30)
BUNDLE_EPISODE_IDENTITY_OVERRIDES = {
    "open-sp500:DXC:2017-04-04": {
        "cik": "0001688568",
        "name": "DXC Technology Company",
    }
}


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _timestamp(day: str, *, end_of_day: bool = False) -> str:
    return f"{day}T{'23:59:59' if end_of_day else '00:00:00'}Z"


def _evidence_date(value: Any, fallback: date) -> date:
    text = str(value or "")
    if len(text) >= 10 and text[:4].isdigit():
        return date.fromisoformat(text[:10])
    return fallback


def _vendor_id(
    *,
    ticker: str,
    identity: dict[str, Any],
    identifier_by_ticker: dict[str, dict[str, Any]],
    price_tickers: Sequence[str] = (),
) -> str:
    for price_ticker in reversed(price_tickers):
        mapping = identifier_by_ticker.get(price_ticker)
        if mapping and mapping.get("share_class_figi"):
            return str(mapping["share_class_figi"])
    mapping = identifier_by_ticker.get(ticker)
    if mapping and mapping.get("share_class_figi"):
        return str(mapping["share_class_figi"])
    if identity.get("cik"):
        return f"CIK{identity['cik']}:{ticker}"
    if identity.get("wikidata_entity"):
        return f"WIKIDATA:{str(identity['wikidata_entity']).rsplit('/', 1)[-1]}:{ticker}"
    raise ValueError(f"no permanent identity for {ticker}")


def _load_completion(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    body = path.read_bytes()
    completion = json.loads(body)
    rows = []
    for page in completion["pages"]:
        page_path = path.parent / page["path"]
        page_body = page_path.read_bytes()
        if _sha256(page_body) != page["sha256"]:
            raise ValueError(f"price page does not reproduce for {completion['ticker']}")
        rows.extend(json.loads(page_body))
    rows.sort(key=lambda row: row["date"])
    return completion, rows


def _bundle_price(vendor_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "vendor_id": vendor_id,
        "date": row["date"][:10],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "adj_open": row["adjOpen"],
        "adj_high": row["adjHigh"],
        "adj_low": row["adjLow"],
        "adj_close": row["adjClose"],
        "volume": row["volume"],
        "adj_volume": row["adjVolume"],
    }


def _month_counts(primary: Sequence[Any]) -> dict[str, int]:
    result = {}
    year, month = WINDOW_START.year, WINDOW_START.month
    while (year, month) <= (WINDOW_END.year, WINDOW_END.month):
        day = date(year, month, calendar.monthrange(year, month)[1])
        result[f"{year:04d}-{month:02d}"] = len(membership_on(primary, day))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _coalesce_memberships(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_vendor[row["vendor_id"]].append(dict(row))
    merged = []
    for vendor_id, vendor_rows in sorted(by_vendor.items()):
        current = None
        for row in sorted(vendor_rows, key=lambda value: value["effective_from"]):
            if current is None:
                current = row
                continue
            current_end = date.fromisoformat(current["effective_to"])
            next_start = date.fromisoformat(row["effective_from"])
            if next_start <= current_end + timedelta(days=1):
                current["effective_to"] = max(
                    current["effective_to"], row["effective_to"]
                )
                current["announced_at"] = min(
                    current["announced_at"], row["announced_at"]
                )
            else:
                merged.append(current)
                current = row
        if current is not None:
            merged.append(current)
    return sorted(merged, key=lambda row: (row["vendor_id"], row["effective_from"]))


def _membership_month_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    final_sessions = {}
    for session in exchange_sessions(WINDOW_START, WINDOW_END, calendar_name="XNYS"):
        final_sessions[f"{session.year:04d}-{session.month:02d}"] = session
    result = {}
    for month, day in sorted(final_sessions.items()):
        result[month] = sum(
            date.fromisoformat(row["effective_from"]) <= day
            <= date.fromisoformat(row["effective_to"])
            for row in rows
        )
    return result


def build_bundle(
    *,
    primary_body: bytes,
    identifiers_body: bytes,
    reconciled_body: bytes,
    membership_samples_body: bytes,
    delisting_body: bytes,
    exclusions_body: bytes,
    license_path: Path,
    price_roots: Sequence[Path],
    output_dir: Path,
    created_at: str,
) -> dict[str, Any]:
    parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if parsed_created_at.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    primary = parse_membership_history(primary_body, label="primary membership")
    identifiers = json.loads(identifiers_body)
    reconciled = json.loads(reconciled_body)
    samples = json.loads(membership_samples_body)
    delisting = json.loads(delisting_body)
    exclusions = json.loads(exclusions_body)
    if exclusions.get("coverage_gate_passed") is not True:
        raise ValueError(
            "price exclusions fail the 0.95 monthly coverage gate; refusing to publish a bundle"
        )
    excluded_episode_ids = {row["episode_id"] for row in exclusions["exclusions"]}
    identifier_by_ticker = {row["ticker"]: row for row in identifiers["mappings"]}
    reconciled_by_id = {row["episode_id"]: row for row in reconciled["episodes"]}
    additional_identity_by_ticker = {
        row["ticker"]: row for row in reconciled.get("additional_identities", [])
    }
    main_completion_by_ticker = {
        path.parent.name: path.resolve()
        for root in price_roots
        for path in root.glob("batch-*/*/complete.json")
    }

    security_state: dict[str, dict[str, Any]] = {}
    memberships = []
    vendor_price_paths: dict[str, set[Path]] = defaultdict(set)
    membership_vendor_by_episode: dict[str, list[tuple[date, date, str]]] = {}

    def register_security(
        vendor_id: str,
        *,
        ticker: str,
        name: str,
        cik: Optional[str],
        identity_mapping: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        state = security_state.setdefault(
            vendor_id,
            {
                "vendor_id": vendor_id,
                "ticker": ticker,
                "name": name or ticker,
                "exchange": None,
                "sector": None,
                "industry": None,
                "cik": cik,
                "active_from": None,
                "active_to": None,
                "identifiers": {},
                "ticker_aliases": {},
            },
        )
        if identity_mapping:
            for field, kind in (("share_class_figi", "FIGI_SHARE_CLASS"), ("composite_figi", "FIGI_COMPOSITE")):
                value = identity_mapping.get(field)
                if value:
                    state["identifiers"][(kind, value, WINDOW_START.isoformat())] = {
                        "identifier_type": kind,
                        "identifier_value": value,
                        "valid_from": WINDOW_START.isoformat(),
                        "valid_to": None,
                        "is_permanent": kind == "FIGI_SHARE_CLASS",
                    }
        return state

    episodes = derive_membership_episodes(primary, window_start=WINDOW_START, window_end=WINDOW_END)
    for episode in episodes:
        ticker = tiingo_ticker(episode.ticker)
        if episode.episode_id in excluded_episode_ids:
            continue
        reconciled_row = reconciled_by_id.get(episode.episode_id)
        if reconciled_row is None:
            overlapping = [
                candidate
                for candidate in reconciled["episodes"]
                if candidate["ticker"] == ticker
                and date.fromisoformat(candidate["effective_from"])
                <= episode.effective_to
                and date.fromisoformat(candidate["effective_to"])
                >= episode.effective_from
            ]
            if len(overlapping) > 1:
                raise ValueError(f"ambiguous reconciled lineage overlap for {episode.episode_id}")
            reconciled_row = overlapping[0] if overlapping else None
        segments: list[dict[str, Any]]
        price_paths: list[Path] = []
        if reconciled_row is None:
            mapping = identifier_by_ticker[ticker]
            identity = BUNDLE_EPISODE_IDENTITY_OVERRIDES.get(
                episode.episode_id,
                additional_identity_by_ticker.get(
                    ticker,
                    {"cik": mapping.get("cik"), "name": mapping.get("name") or ticker},
                ),
            )
            completion = main_completion_by_ticker.get(ticker)
            if completion is None:
                raise ValueError(f"safe membership ticker lacks prices: {ticker}")
            price_paths = [completion]
            segments = [
                {
                    **identity,
                    "target_start": episode.effective_from.isoformat(),
                    "target_end": episode.effective_to.isoformat(),
                    "aliases": [ticker],
                    "usable_prices": [{"completion_path": str(completion)}],
                }
            ]
        else:
            selected = reconciled_row.get("selected_identity") or {}
            segments = list(selected.get("segments") or [selected])
            price_paths = [
                Path(row["completion_path"])
                for segment in segments
                for row in segment.get("usable_prices", [])
            ]
        episode_bindings = []
        for segment in segments:
            start = max(
                episode.effective_from,
                _evidence_date(segment.get("target_start"), episode.effective_from),
            )
            end = min(
                episode.effective_to,
                _evidence_date(segment.get("target_end"), episode.effective_to),
            )
            segment_paths = [Path(row["completion_path"]) for row in segment.get("usable_prices", [])]
            segment_price_tickers = []
            for path in segment_paths:
                segment_price_tickers.append(json.loads(path.read_text())["ticker"])
            vendor_id = _vendor_id(
                ticker=ticker,
                identity=segment,
                identifier_by_ticker=identifier_by_ticker,
                price_tickers=segment_price_tickers,
            )
            canonical_ticker = segment_price_tickers[-1] if segment_price_tickers else ticker
            mapping = identifier_by_ticker.get(canonical_ticker) or identifier_by_ticker.get(ticker)
            state = register_security(
                vendor_id,
                ticker=canonical_ticker,
                name=str(segment.get("name") or (mapping or {}).get("name") or ticker),
                cik=segment.get("cik") or (mapping or {}).get("cik"),
                identity_mapping=mapping,
            )
            state["active_from"] = min(filter(None, [state["active_from"], start.isoformat()]), default=start.isoformat())
            alias_key = (ticker, start.isoformat())
            state["ticker_aliases"][alias_key] = {
                "ticker": ticker,
                "exchange": None,
                "effective_from": start.isoformat(),
                "effective_to": end.isoformat(),
                "announced_at": _timestamp(start.isoformat()),
            }
            memberships.append(
                {
                    "vendor_id": vendor_id,
                    "effective_from": start.isoformat(),
                    "effective_to": end.isoformat(),
                    "announced_at": _timestamp(start.isoformat()),
                }
            )
            for path in segment_paths:
                vendor_price_paths[vendor_id].add(path.resolve())
            episode_bindings.append((start, end, vendor_id))
        membership_vendor_by_episode[episode.episode_id] = episode_bindings

    # SPY is the benchmark and is never a universe member.
    spy_mapping = identifier_by_ticker["SPY"]
    spy_vendor_id = str(spy_mapping["share_class_figi"])
    spy_path = main_completion_by_ticker["SPY"]
    spy_state = register_security(
        spy_vendor_id,
        ticker="SPY",
        name=str(spy_mapping["name"]),
        cik=spy_mapping.get("cik"),
        identity_mapping=spy_mapping,
    )
    spy_state["active_from"] = WINDOW_START.isoformat()
    spy_state["ticker_aliases"][("SPY", WINDOW_START.isoformat())] = {
        "ticker": "SPY",
        "exchange": "NYSE Arca",
        "effective_from": WINDOW_START.isoformat(),
        "effective_to": None,
        "announced_at": _timestamp(WINDOW_START.isoformat()),
    }
    vendor_price_paths[spy_vendor_id].add(spy_path.resolve())

    prices = []
    actions = []
    completion_vendor: dict[str, str] = {}
    for vendor_id, paths in sorted(vendor_price_paths.items()):
        chosen: dict[str, dict[str, Any]] = {}
        for path in sorted(paths, key=lambda value: json.loads(value.read_text())["first_price_date"]):
            completion, rows = _load_completion(path)
            completion_vendor[str(path.resolve())] = vendor_id
            state = security_state[vendor_id]
            first = max(WINDOW_START, date.fromisoformat(completion["first_price_date"]))
            state["active_from"] = min(filter(None, [state["active_from"], first.isoformat()]), default=first.isoformat())
            alias_key = (completion["ticker"], first.isoformat())
            if first <= WINDOW_END:
                state["ticker_aliases"].setdefault(
                    alias_key,
                    {
                        "ticker": completion["ticker"],
                        "exchange": None,
                        "effective_from": first.isoformat(),
                        "effective_to": min(WINDOW_END, date.fromisoformat(completion["last_price_date"])).isoformat(),
                        "announced_at": _timestamp(first.isoformat()),
                    },
                )
            for row in rows:
                day = row["date"][:10]
                if WINDOW_START.isoformat() <= day <= WINDOW_END.isoformat():
                    chosen[day] = row
        for day, row in sorted(chosen.items()):
            prices.append(_bundle_price(vendor_id, row))
            if float(row.get("splitFactor", 1)) != 1:
                actions.append(
                    {
                        "vendor_id": vendor_id,
                        "action_type": "split",
                        "effective_date": day,
                        "announced_at": _timestamp(day),
                        "cash_amount": None,
                        "currency": None,
                        "ratio_from": 1,
                        "ratio_to": row["splitFactor"],
                        "related_vendor_id": None,
                        "details": {"availability_precision": "effective_date_only", "source": "tiingo_eod"},
                    }
                )
            if float(row.get("divCash", 0)) != 0:
                actions.append(
                    {
                        "vendor_id": vendor_id,
                        "action_type": "cash_dividend",
                        "effective_date": day,
                        "announced_at": _timestamp(day),
                        "cash_amount": row["divCash"],
                        "currency": "USD",
                        "ratio_from": None,
                        "ratio_to": None,
                        "related_vendor_id": None,
                        "details": {"availability_precision": "effective_date_only", "source": "tiingo_eod"},
                    }
                )

    ended_by_path = {
        str(Path(row["completion_path"]).resolve()): row
        for row in delisting["ended_listings"]
    }
    terminal_by_vendor = {}
    for vendor_id, paths in vendor_price_paths.items():
        endpoint_rows = [ended_by_path.get(str(path.resolve())) for path in paths]
        if endpoint_rows and all(row is not None for row in endpoint_rows):
            terminal = max(str(row["listing_end_date"]) for row in endpoint_rows)
            if terminal <= WINDOW_END.isoformat():
                terminal_by_vendor[vendor_id] = terminal

    delistings = []
    for vendor_id, terminal in sorted(terminal_by_vendor.items()):
        security_state[vendor_id]["active_to"] = terminal
        delistings.append(
            {
                "vendor_id": vendor_id,
                "delisting_date": terminal,
                "announced_at": _timestamp(terminal, end_of_day=True),
                "delisting_return": None,
                "return_available_at": None,
                "reason": "tiingo_listing_ended; separate delisting return unavailable",
                "successor_vendor_id": None,
            }
        )

    securities = []
    for state in sorted(security_state.values(), key=lambda row: row["vendor_id"]):
        state = dict(state)
        state["identifiers"] = sorted(state["identifiers"].values(), key=lambda row: (row["identifier_type"], row["identifier_value"]))
        state["ticker_aliases"] = sorted(state["ticker_aliases"].values(), key=lambda row: (row["ticker"], row["effective_from"]))
        securities.append(state)

    memberships = _coalesce_memberships(memberships)
    for row in memberships:
        terminal = terminal_by_vendor.get(row["vendor_id"])
        if terminal is not None and row["effective_to"] > terminal:
            row["effective_to"] = terminal
    independent_samples = []
    for sample in samples["samples"]:
        as_of = date.fromisoformat(sample["as_of_date"])
        active_ids = sorted(
            {
                row["vendor_id"]
                for row in memberships
                if date.fromisoformat(row["effective_from"]) <= as_of
                and date.fromisoformat(row["effective_to"]) >= as_of
            }
        )
        independent_samples.append(
            {
                "as_of_date": sample["as_of_date"],
                "vendor_ids": active_ids,
                "source_uri": sample["source_url"],
                "source_sha256": sample["sha256"],
            }
        )

    documents = {
        "securities": securities,
        "memberships": memberships,
        "prices": sorted(prices, key=lambda row: (row["vendor_id"], row["date"])),
        "corporate_actions": sorted(actions, key=lambda row: (row["vendor_id"], row["effective_date"], row["action_type"])),
        "delistings": sorted(delistings, key=lambda row: (row["vendor_id"], row["delisting_date"])),
    }
    files = {}
    for role, document in documents.items():
        body = _json_bytes(document)
        path = output_dir / f"{role}.json"
        _atomic_write(path, body)
        files[role] = {
            "path": path.name,
            "dataset": f"free_composite_{role}_v1",
            "source_uri": f"private://free-point-in-time/{role}",
            "retrieved_at": created_at,
            "sha256": _sha256(body),
        }
    manifest = {
        "schema_version": "point-in-time-equity-bundle-v1",
        "created_at": created_at,
        "vendor": "Composite: Tiingo, OpenFIGI, SEC, Wikipedia, open S&P histories",
        "license_tag": "personal_internal_research_no_redistribution_v1",
        "license_rights_confirmed": True,
        "license_evidence_uri": license_path.resolve().as_uri(),
        "vendor_identifier_type": "COMPOSITE_PERMANENT_ID",
        "universe": {
            "universe_id": "sp500-pit-v1",
            "name": "Historical S&P 500",
            "version": "free-composite-v1",
            "description": "Revision-pinned membership with permanent identities and explicit price exclusions",
            "window_start": WINDOW_START.isoformat(),
            "window_end": WINDOW_END.isoformat(),
            "benchmark_vendor_id": spy_vendor_id,
            "benchmark_excluded_from_rankings": True,
        },
        "files": files,
        "audit_contract": {
            "expected_row_counts": {role: len(document) for role, document in documents.items()},
            "monthly_membership_counts": _membership_month_counts(memberships),
            "independent_membership_samples": independent_samples,
        },
    }
    manifest_body = _json_bytes(manifest)
    _atomic_write(output_dir / "manifest.json", manifest_body)
    return {
        "manifest_sha256": _sha256(manifest_body),
        "row_counts": manifest["audit_contract"]["expected_row_counts"],
        "security_count": len(securities),
        "membership_count": len(memberships),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--expected-primary-hash", required=True)
    parser.add_argument("--identifiers", type=Path, default=DEFAULT_IDENTIFIERS)
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--membership-samples", type=Path, default=DEFAULT_MEMBERSHIP_SAMPLES)
    parser.add_argument("--delisting-evidence", type=Path, default=DEFAULT_DELISTING)
    parser.add_argument("--price-exclusions", type=Path, default=DEFAULT_EXCLUSIONS)
    parser.add_argument("--license-evidence", type=Path, default=DEFAULT_LICENSE)
    parser.add_argument("--price-root", type=Path, action="append")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--created-at", required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        primary_body = args.primary.read_bytes()
        if _sha256(primary_body) != args.expected_primary_hash.lower():
            raise ValueError("primary membership SHA-256 does not match")
        result = build_bundle(
            primary_body=primary_body,
            identifiers_body=args.identifiers.read_bytes(),
            reconciled_body=args.reconciled.read_bytes(),
            membership_samples_body=args.membership_samples.read_bytes(),
            delisting_body=args.delisting_evidence.read_bytes(),
            exclusions_body=args.price_exclusions.read_bytes(),
            license_path=args.license_evidence,
            price_roots=tuple(args.price_root or DEFAULT_PRICE_ROOTS),
            output_dir=args.output_dir,
            created_at=args.created_at,
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"free PIT bundle build failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"manifest_sha256={result['manifest_sha256']} "
        f"securities={result['security_count']} memberships={result['membership_count']} "
        f"prices={result['row_counts']['prices']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
