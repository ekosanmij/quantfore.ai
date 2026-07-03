import json

from pipelines.resolve_free_point_in_time_identifiers import resolve_identifiers


def test_resolution_uses_historical_name_over_recycled_current_ticker():
    html = """
    <table id="constituents"><tr><th>Symbol</th><th>Security</th></tr>
    """ + "".join(
        f"<tr><td>X{i}</td><td>Company {i}</td></tr>" for i in range(500)
    ) + """</table><table id="changes">
    <tr><th>Date</th><th>Added</th><th>Removed</th><th>Reason</th></tr>
    <tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
    """ + "".join(
        (
            "<tr><td>2020</td><td></td><td></td><td>APC</td>"
            "<td>Anadarko Petroleum</td><td>Acquired</td></tr>"
        )
        for _ in range(300)
    ) + "</table>"
    wiki = {"parse": {"revid": 1, "text": {"*": html}}}
    openfigi = {
        "acquisition_plan_sha256": "a" * 64,
        "requested_ticker_count": 1,
        "mappings": [
            {
                "ticker": "APC",
                "status": "ambiguous",
                "candidate_status": "ambiguous",
                "matching_candidates": [
                    {"name": "ANADARKO PETROLEUM CORP", "shareClassFIGI": "OLD", "compositeFIGI": "OLD-C", "securityType2": "Common Stock"},
                    {"name": "ARKO PETROLEUM CORP", "shareClassFIGI": "NEW", "compositeFIGI": "NEW-C", "securityType2": "Common Stock"},
                ],
            }
        ],
    }
    sec = {"0": {"ticker": "APC", "title": "ARKO Petroleum Corp.", "cik_str": 1}}

    result = resolve_identifiers(
        plan_sha256="a" * 64,
        openfigi_body=json.dumps(openfigi).encode(),
        wikipedia_body=json.dumps(wiki).encode(),
        wikipedia_revision=1,
        sec_body=json.dumps(sec).encode(),
    )

    assert result["resolved_ticker_count"] == 1
    assert result["mappings"][0]["share_class_figi"] == "OLD"
    assert result["mappings"][0]["cik"] is None
