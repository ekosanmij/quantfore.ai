import json

from pipelines.acquire_sec_point_in_time_fundamentals import (
    _additional_identities,
    acquire_sec_sources,
)


class Client:
    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        cik = url.split("CIK", 1)[1].split(".json", 1)[0]
        if "companyfacts" in url:
            return json.dumps({"cik": int(cik), "facts": {}}).encode()
        return json.dumps({"cik": cik, "filings": {}}).encode()


def test_sec_acquisition_is_resumable(tmp_path):
    identifiers = {
        "mappings": [
            {
                "status": "resolved",
                "cik": "0000000001",
                "ticker": "AAA",
                "share_class_figi": "FIGI1",
            }
        ]
    }
    body = json.dumps(identifiers).encode()
    client = Client()
    first = acquire_sec_sources(
        client=client,
        identifier_body=body,
        output_root=tmp_path,
        request_delay_seconds=0,
    )
    second = acquire_sec_sources(
        client=client,
        identifier_body=body,
        output_root=tmp_path,
        request_delay_seconds=0,
    )
    assert len(client.calls) == 2
    assert first["complete_cik_count"] == 1
    assert second["reused_cik_count"] == 1


def test_additional_lineage_ciks_are_normalized_and_grouped():
    assert _additional_identities(["39911:GPS", "0000039911:GAP"]) == (
        {
            "cik": "0000039911",
            "tickers": ["GAP", "GPS"],
            "share_class_figis": [],
        },
    )
