import json

from pipelines.acquire_wikidata_lineage_evidence import (
    build_query,
    normalize_response,
)


def test_wikidata_query_uses_dated_exchange_ticker_qualifiers():
    query = build_query(["META", "ABC"])

    assert 'VALUES ?targetTicker { "ABC" "META" }' in query
    assert "pq:P249 ?targetTicker" in query
    assert "pq:P580 ?targetStart" in query
    assert "pq:P582 ?targetEnd" in query


def test_wikidata_response_normalizes_missing_optional_values():
    body = json.dumps({
        "results": {
            "bindings": [{
                "targetTicker": {"value": "ABC"},
                "company": {"value": "http://www.wikidata.org/entity/Q470156"},
                "companyLabel": {"value": "Cencora"},
                "cik": {"value": "0001140859"},
            }]
        }
    }).encode()

    rows = normalize_response(body)

    assert rows[0]["targetTicker"] == "ABC"
    assert rows[0]["cik"] == "0001140859"
    assert rows[0]["aliasTicker"] is None
