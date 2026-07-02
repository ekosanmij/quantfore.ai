"""Tiingo daily market-price download and normalisation.

The API token is sent only in the Authorization header. Source URLs and error
messages are therefore safe to persist without leaking the credential.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


TIINGO_API_KEY_ENV = "TIINGO_API_KEY"
TIINGO_VENDOR = "Tiingo"
TIINGO_BASE_URL = "https://api.tiingo.com/tiingo/daily"
RETRIABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class MarketPriceError(RuntimeError):
    """Base class for market-price ingestion failures."""


class MissingCredentialsError(MarketPriceError):
    """Raised when the configured vendor API key is unavailable."""


class VendorResponseError(MarketPriceError):
    """Raised when a vendor response cannot be safely accepted."""


class IncompleteDownloadError(MarketPriceError):
    """Raised when a complete requested response cannot be downloaded."""


@dataclass(frozen=True)
class CanonicalPrice:
    """A Tiingo daily row mapped to Quantfore's canonical Price fields."""

    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adj_open: Decimal
    adj_high: Decimal
    adj_low: Decimal
    adj_close: Decimal
    adj_volume: Decimal


@dataclass(frozen=True)
class RawPage:
    """One exact HTTP response page with audit metadata."""

    source_url: str
    retrieved_at: datetime
    body: bytes
    headers: tuple[tuple[str, str], ...]
    prices: tuple[CanonicalPrice, ...]

    @property
    def source_hash(self) -> str:
        return sha256(self.body).hexdigest()


@dataclass(frozen=True)
class TickerDownload:
    """A complete, validated response for one requested ticker."""

    ticker: str
    pages: tuple[RawPage, ...]
    prices: tuple[CanonicalPrice, ...]
    price_page_numbers: tuple[int, ...]


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


def load_api_key(
    environ: Optional[Mapping[str, str]] = None,
    *,
    variable_name: str = TIINGO_API_KEY_ENV,
) -> str:
    """Load a non-empty Tiingo API key from the environment."""

    source = os.environ if environ is None else environ
    api_key = source.get(variable_name, "").strip()
    if not api_key:
        raise MissingCredentialsError(
            f"missing Tiingo credentials: set {variable_name} in the environment"
        )
    return api_key


def _decimal(value: object, field: str, row_number: int) -> Decimal:
    if value is None or isinstance(value, bool):
        raise VendorResponseError(f"row {row_number}: {field} must be numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise VendorResponseError(
            f"row {row_number}: {field} must be numeric"
        ) from exc


def _integer(value: object, field: str, row_number: int) -> int:
    parsed = _decimal(value, field, row_number)
    if parsed != parsed.to_integral_value():
        raise VendorResponseError(f"row {row_number}: {field} must be an integer")
    return int(parsed)


def _price_date(value: object, row_number: int) -> date:
    if not isinstance(value, str) or not value.strip():
        raise VendorResponseError(f"row {row_number}: date is required")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        if "T" in normalized:
            return datetime.fromisoformat(normalized).date()
        return date.fromisoformat(normalized)
    except ValueError as exc:
        raise VendorResponseError(
            f"row {row_number}: invalid ISO-8601 date {value!r}"
        ) from exc


def _response_rows(document: object) -> Sequence[object]:
    if isinstance(document, list):
        return document
    if isinstance(document, dict) and isinstance(document.get("data"), list):
        return document["data"]
    raise VendorResponseError("Tiingo response must be a JSON array or data envelope")


def parse_tiingo_page(body: bytes) -> tuple[CanonicalPrice, ...]:
    """Parse one response page without repairing or defaulting vendor data."""

    try:
        document = json.loads(
            body.decode("utf-8"),
            parse_float=Decimal,
            parse_int=int,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VendorResponseError("Tiingo response is not valid UTF-8 JSON") from exc

    parsed_rows = []
    for row_number, row in enumerate(_response_rows(document), start=1):
        if not isinstance(row, dict):
            raise VendorResponseError(f"row {row_number}: expected a JSON object")
        parsed_rows.append(
            CanonicalPrice(
                date=_price_date(row.get("date"), row_number),
                open=_decimal(row.get("open"), "open", row_number),
                high=_decimal(row.get("high"), "high", row_number),
                low=_decimal(row.get("low"), "low", row_number),
                close=_decimal(row.get("close"), "close", row_number),
                volume=_integer(row.get("volume"), "volume", row_number),
                adj_open=_decimal(row.get("adjOpen"), "adjOpen", row_number),
                adj_high=_decimal(row.get("adjHigh"), "adjHigh", row_number),
                adj_low=_decimal(row.get("adjLow"), "adjLow", row_number),
                adj_close=_decimal(row.get("adjClose"), "adjClose", row_number),
                adj_volume=_decimal(
                    row.get("adjVolume"), "adjVolume", row_number
                ),
            )
        )
    return tuple(parsed_rows)


def _header_map(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _page_url(current_url: str, page_number: int) -> str:
    parsed = urlparse(current_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _next_url(current_url: str, body: bytes, headers: Mapping[str, str]) -> Optional[str]:
    normalized_headers = _header_map(headers)
    link = normalized_headers.get("link", "")
    for target, relation in re.findall(r"<([^>]+)>\s*;\s*rel=\"?([^\",;]+)", link):
        if relation.strip().lower() == "next":
            return urljoin(current_url, target)

    explicit_next = normalized_headers.get("x-next-page")
    if explicit_next:
        if explicit_next.isdigit():
            return _page_url(current_url, int(explicit_next))
        return urljoin(current_url, explicit_next)

    current_page = normalized_headers.get("x-page")
    total_pages = normalized_headers.get("x-total-pages")
    if current_page and total_pages:
        try:
            current_number = int(current_page)
            total_number = int(total_pages)
        except ValueError as exc:
            raise VendorResponseError("invalid vendor pagination headers") from exc
        if current_number < total_number:
            return _page_url(current_url, current_number + 1)

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(document, dict):
        for key in ("next", "next_url", "nextPage"):
            value = document.get(key)
            if isinstance(value, int):
                return _page_url(current_url, value)
            if isinstance(value, str) and value.strip():
                if value.strip().isdigit():
                    return _page_url(current_url, int(value.strip()))
                return urljoin(current_url, value.strip())
    return None


def _retry_after_seconds(value: Optional[str], now: datetime) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - now).total_seconds())


class TiingoMarketPriceClient:
    """Small Tiingo EOD client with bounded retries and pagination."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = TIINGO_BASE_URL,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        max_pages: int = 100,
        backoff_seconds: float = 1.0,
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not api_key.strip():
            raise MissingCredentialsError("Tiingo API key must not be blank")
        if max_retries < 0 or max_pages < 1:
            raise ValueError("max_retries must be non-negative and max_pages positive")
        self._api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_pages = max_pages
        self.backoff_seconds = backoff_seconds
        self._opener = opener
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        **kwargs: object,
    ) -> "TiingoMarketPriceClient":
        return cls(load_api_key(environ), **kwargs)

    def _request_once(self, url: str) -> HttpResult:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Token {self._api_key}",
                "User-Agent": "QuantforeAIResearch/0.1",
            },
        )
        response = self._opener(request, timeout=self.timeout_seconds)
        try:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            headers_object = getattr(response, "headers", {})
            headers = dict(headers_object.items())
            body = response.read()
            return HttpResult(status=int(status), headers=headers, body=body)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _request_with_retries(self, url: str) -> HttpResult:
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                result = self._request_once(url)
                if 200 <= result.status < 300:
                    return result
                if result.status not in RETRIABLE_STATUS_CODES:
                    raise MarketPriceError(
                        f"Tiingo request failed with HTTP {result.status}: {url}"
                    )
                last_error = MarketPriceError(
                    f"Tiingo request failed with HTTP {result.status}: {url}"
                )
                retry_after = _header_map(result.headers).get("retry-after")
            except HTTPError as exc:
                if exc.code not in RETRIABLE_STATUS_CODES:
                    raise MarketPriceError(
                        f"Tiingo request failed with HTTP {exc.code}: {url}"
                    ) from exc
                last_error = exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
            except (TimeoutError, URLError, OSError) as exc:
                last_error = exc

            if attempt >= self.max_retries:
                break
            delay = _retry_after_seconds(retry_after, self._clock())
            if delay is None:
                delay = self.backoff_seconds * (2**attempt)
            self._sleep(delay)

        raise IncompleteDownloadError(
            f"Tiingo request failed after {self.max_retries + 1} attempts: {url}"
        ) from last_error

    def _initial_url(self, ticker: str, start_date: date, end_date: date) -> str:
        query = urlencode(
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "resampleFreq": "daily",
                "format": "json",
            }
        )
        return f"{self.base_url}/{ticker}/prices?{query}"

    def _validate_next_url(self, next_url: str) -> None:
        base = urlparse(self.base_url)
        candidate = urlparse(next_url)
        if candidate.scheme != "https" or candidate.netloc != base.netloc:
            raise VendorResponseError(
                "refusing vendor pagination URL outside the configured HTTPS host"
            )
        sensitive_keys = {"token", "apikey", "api_key", "authorization"}
        query_pairs = parse_qsl(candidate.query, keep_blank_values=True)
        if any(
            key.lower() in sensitive_keys or value == self._api_key
            for key, value in query_pairs
        ):
            raise VendorResponseError(
                "refusing vendor pagination URL containing credentials"
            )

    def download(
        self,
        ticker: str,
        *,
        start_date: date,
        end_date: date,
    ) -> TickerDownload:
        """Download all pages for one ticker or raise without a partial result."""

        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker is required")
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")

        next_url: Optional[str] = self._initial_url(
            normalized_ticker, start_date, end_date
        )
        seen_urls = set()
        pages = []
        by_date: dict[date, tuple[CanonicalPrice, int]] = {}

        while next_url is not None:
            if next_url in seen_urls:
                raise IncompleteDownloadError("vendor pagination loop detected")
            if len(pages) >= self.max_pages:
                raise IncompleteDownloadError(
                    f"vendor response exceeded {self.max_pages} pages"
                )
            self._validate_next_url(next_url)
            seen_urls.add(next_url)
            result = self._request_with_retries(next_url)
            retrieved_at = self._clock()
            parsed_prices = parse_tiingo_page(result.body)
            page_number = len(pages) + 1
            page = RawPage(
                source_url=next_url,
                retrieved_at=retrieved_at,
                body=result.body,
                headers=tuple(sorted(result.headers.items())),
                prices=parsed_prices,
            )
            pages.append(page)

            for price in parsed_prices:
                if not start_date <= price.date <= end_date:
                    raise VendorResponseError(
                        f"{normalized_ticker}: vendor returned out-of-window date "
                        f"{price.date.isoformat()}"
                    )
                prior = by_date.get(price.date)
                if prior is not None and prior[0] != price:
                    raise VendorResponseError(
                        f"{normalized_ticker}: conflicting duplicate date "
                        f"{price.date.isoformat()}"
                    )
                if prior is None:
                    by_date[price.date] = (price, page_number)

            next_url = _next_url(next_url, result.body, result.headers)

        if not by_date:
            raise IncompleteDownloadError(
                f"{normalized_ticker}: vendor returned no prices for requested window"
            )

        ordered = sorted(by_date.values(), key=lambda item: item[0].date)
        return TickerDownload(
            ticker=normalized_ticker,
            pages=tuple(pages),
            prices=tuple(item[0] for item in ordered),
            price_page_numbers=tuple(item[1] for item in ordered),
        )
