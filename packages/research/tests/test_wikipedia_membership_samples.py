from pipelines.acquire_wikipedia_membership_samples import (
    _canonical_set,
    extract_constituent_tickers,
)


def test_extracts_supported_exchange_templates_from_first_table():
    text = """== Components ==
{| class="wikitable"
|-
|{{NYSE|BRK.B}}||Berkshire
|-
|{{BZX link|CBOE}}||Cboe
|}
== Changes ==
{| class="wikitable"
|{{NYSE|OLD}}
|}
"""
    # Production revisions are plausibility-checked; pad the fixture to that range.
    rows = "\n".join(f"|{{{{NYSE|T{i}}}}}||Company" for i in range(448))
    text = text.replace("|}\n== Changes", rows + "\n|}\n== Changes")
    tickers = extract_constituent_tickers(text)
    assert {"BRK-B", "CBOE"}.issubset(tickers)
    assert "OLD" not in tickers


def test_sec_verified_rename_is_canonicalized_for_membership_comparison():
    assert _canonical_set({"KORS"}) == _canonical_set({"CPRI"})
