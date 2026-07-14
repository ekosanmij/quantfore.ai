"""Shared helpers for small ingestion scripts."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # Imported through the pipelines package.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.models import Security


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_ENV = "QUANTFORE_DATA_ROOT"
DEFAULT_USER_AGENT = "QuantforeAIResearch/0.1"


def configured_data_root(
    *,
    environment: Optional[Mapping[str, str]] = None,
    repository_root: Path = REPOSITORY_ROOT,
) -> Path:
    """Return the configured data root without embedding a machine-local path.

    ``QUANTFORE_DATA_ROOT`` points at the directory containing ``raw/``.  When
    unset, scripts retain the repository-local ``data/`` default.
    """

    values = os.environ if environment is None else environment
    configured = values.get(DATA_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return repository_root / "data"


DEFAULT_DATA_ROOT = configured_data_root()
DEFAULT_RAW_DIR = DEFAULT_DATA_ROOT / "raw"


def repository_relative_path(path: Path) -> str:
    """Return a portable repository-relative path when one is available."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def get_code_revision(*, repository_root: Path = REPOSITORY_ROOT) -> Optional[str]:
    """Return HEAD, or a deterministic identifier for dirty source state.

    Generated evidence below ``reports/`` is intentionally excluded: reports
    are outputs of the revision being identified, not source inputs. Ignored
    raw data and credentials are omitted by Git itself.
    """

    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repository_root,
            stderr=subprocess.DEVNULL,
        ).strip()
        tracked_diff = subprocess.check_output(
            [
                "git",
                "diff",
                "--binary",
                "HEAD",
                "--",
                ".",
                ":(exclude)reports/**",
            ],
            cwd=repository_root,
            stderr=subprocess.DEVNULL,
        )
        untracked_output = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repository_root,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    untracked = sorted(
        value.decode("utf-8")
        for value in untracked_output.split(b"\0")
        if value and not value.decode("utf-8").startswith("reports/")
    )
    if not tracked_diff and not untracked:
        return head.decode("ascii")

    digest = hashlib.sha256()
    digest.update(tracked_diff)
    for relative_path in untracked:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        path = repository_root / relative_path
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"{head.decode('ascii')}-dirty-{digest.hexdigest()[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fetch_bytes(
    url: str,
    *,
    user_agent: Optional[str] = DEFAULT_USER_AGENT,
    timeout_seconds: int = 20,
    retries: int = 2,
) -> bytes:
    curl_command = [
        "curl",
        "-L",
        "--http1.1",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        str(timeout_seconds),
    ]
    if user_agent:
        curl_command.extend(["-A", user_agent])
    curl_command.append(url)
    try:
        return subprocess.check_output(curl_command)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        curl_error: BaseException = exc

    headers = {"User-Agent": user_agent} if user_agent else {}
    request = Request(url, headers=headers)
    last_error: Optional[BaseException] = curl_error

    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except (TimeoutError, URLError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(1 + attempt)

    raise RuntimeError(f"failed to fetch {url}") from last_error


def write_raw_payload(raw_dir: Path, storage_uri: str, payload: bytes) -> Path:
    if not storage_uri.startswith("raw/"):
        raise ValueError("storage_uri must start with raw/")

    target = raw_dir.parent / storage_uri
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def open_research_database(database_url: Optional[str]):
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    return make_session_factory(engine)


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


def parse_filed_date(value: Optional[str]) -> Optional[datetime]:
    parsed = parse_date(value)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def get_or_create_security(
    session: Session,
    *,
    ticker: str,
    name: str,
    cik: Optional[str] = None,
    exchange: Optional[str] = None,
    sector: Optional[str] = None,
) -> Security:
    normalized_ticker = ticker.upper().strip()
    security = session.scalar(
        select(Security).where(Security.ticker == normalized_ticker)
    )
    if security is None:
        security = Security(
            ticker=normalized_ticker,
            name=name.strip(),
            cik=cik,
            exchange=exchange,
            sector=sector,
        )
        session.add(security)
        session.flush()
        return security

    if name and not security.name:
        security.name = name.strip()
    if cik and not security.cik:
        security.cik = cik
    if exchange and not security.exchange:
        security.exchange = exchange
    if sector and not security.sector:
        security.sector = sector
    session.flush()
    return security
