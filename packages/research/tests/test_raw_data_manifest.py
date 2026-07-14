import json

import pytest

from pipelines.verify_raw_data_manifest import create_manifest, verify_manifest


def test_raw_data_manifest_round_trip(tmp_path):
    raw_root = tmp_path / "data" / "raw"
    (raw_root / "source-a").mkdir(parents=True)
    (raw_root / "source-a" / "one.json").write_bytes(b'{"one":1}\n')
    (raw_root / "two.csv").write_bytes(b"date,value\n2026-01-01,2\n")
    manifest = tmp_path / "manifests" / "raw-data.jsonl"

    created = create_manifest(raw_root=raw_root, output=manifest)
    verified = verify_manifest(raw_root=raw_root, manifest=manifest)

    assert created["file_count"] == 2
    assert verified == {**created, "verified": True}
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["path"] for line in lines[1:]] == [
        "source-a/one.json",
        "two.csv",
    ]


def test_raw_data_manifest_supports_deterministic_gzip(tmp_path):
    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    (raw_root / "source.json").write_bytes(b"source")
    manifest = tmp_path / "raw-data.jsonl.gz"

    create_manifest(raw_root=raw_root, output=manifest)
    first = manifest.read_bytes()
    create_manifest(raw_root=raw_root, output=manifest)

    assert manifest.read_bytes() == first
    assert verify_manifest(raw_root=raw_root, manifest=manifest)["verified"] is True


def test_raw_data_manifest_rejects_changed_bytes(tmp_path):
    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    target = raw_root / "source.json"
    target.write_bytes(b"original")
    manifest = tmp_path / "raw-data.jsonl"
    create_manifest(raw_root=raw_root, output=manifest)
    target.write_bytes(b"modified")

    with pytest.raises(ValueError, match="SHA-256 differs"):
        verify_manifest(raw_root=raw_root, manifest=manifest)


def test_raw_data_manifest_rejects_missing_or_unexpected_files(tmp_path):
    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    (raw_root / "source.json").write_bytes(b"source")
    manifest = tmp_path / "raw-data.jsonl"
    create_manifest(raw_root=raw_root, output=manifest)
    (raw_root / "extra.json").write_bytes(b"extra")

    with pytest.raises(ValueError, match="file membership differs"):
        verify_manifest(raw_root=raw_root, manifest=manifest)


def test_raw_data_manifest_must_live_outside_raw_tree(tmp_path):
    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="outside the raw-data root"):
        create_manifest(raw_root=raw_root, output=raw_root / "manifest.jsonl")
