"""Create or verify a portable SHA-256 manifest for the private raw-data tree."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence

try:
    from _common import DEFAULT_RAW_DIR
except ModuleNotFoundError:  # Imported through the pipelines package.
    from pipelines._common import DEFAULT_RAW_DIR  # type: ignore


SCHEMA_VERSION = "raw_data_sha256_manifest_v1"
CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(raw_root: Path) -> list[tuple[str, Path]]:
    if not raw_root.is_dir():
        raise ValueError(f"raw-data root is not a directory: {raw_root}")
    rows = []
    for path in raw_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"raw-data manifests do not permit symlinks: {path}")
        if path.is_file():
            rows.append((path.relative_to(raw_root).as_posix(), path))
    rows.sort(key=lambda row: row[0])
    return rows


def _json_line(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _assert_outside_raw_root(raw_root: Path, output: Path) -> None:
    try:
        output.resolve().relative_to(raw_root.resolve())
    except ValueError:
        return
    raise ValueError("checksum manifest must be stored outside the raw-data root")


def create_manifest(*, raw_root: Path, output: Path) -> dict[str, Any]:
    """Hash every regular file and atomically write a portable JSONL manifest."""

    _assert_outside_raw_root(raw_root, output)
    files = _relative_files(raw_root)
    header = {
        "file_count": len(files),
        "schema_version": SCHEMA_VERSION,
        "total_size_bytes": sum(path.stat().st_size for _, path in files),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with temporary.open("wb") as raw_handle:
            compressed_handle = (
                gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0)
                if output.suffix == ".gz"
                else None
            )
            handle = compressed_handle or raw_handle
            try:
                handle.write(_json_line(header))
                for relative_path, path in files:
                    handle.write(
                        _json_line(
                            {
                                "path": relative_path,
                                "sha256": _sha256_file(path),
                                "size_bytes": path.stat().st_size,
                            }
                        )
                    )
            finally:
                if compressed_handle is not None:
                    compressed_handle.close()
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return header


def _manifest_rows(manifest: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    opener = gzip.open if manifest.suffix == ".gz" else open
    with opener(manifest, "rt", encoding="utf-8") as handle:
        try:
            header = json.loads(next(handle))
        except (StopIteration, json.JSONDecodeError) as exc:
            raise ValueError("raw-data manifest has no valid header") from exc
        rows = []
        for line_number, line in enumerate(handle, start=2):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"raw-data manifest line {line_number} is not valid JSON"
                ) from exc
            rows.append(row)
    if not isinstance(header, dict) or header.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("raw-data manifest schema version is invalid")
    return header, rows


def _safe_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("raw-data manifest path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"raw-data manifest path is unsafe: {value!r}")
    return path


def verify_manifest(*, raw_root: Path, manifest: Path) -> dict[str, Any]:
    """Verify file membership, sizes, and SHA-256 values against a manifest."""

    header, rows = _manifest_rows(manifest)
    expected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("raw-data manifest file record must be an object")
        relative = _safe_relative_path(row.get("path")).as_posix()
        if relative in expected:
            raise ValueError(f"raw-data manifest repeats path: {relative}")
        if not isinstance(row.get("size_bytes"), int) or row["size_bytes"] < 0:
            raise ValueError(f"raw-data manifest size is invalid: {relative}")
        sha256 = row.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise ValueError(f"raw-data manifest SHA-256 is invalid: {relative}")
        expected[relative] = row

    actual_files = dict(_relative_files(raw_root))
    missing = sorted(set(expected) - set(actual_files))
    unexpected = sorted(set(actual_files) - set(expected))
    if missing or unexpected:
        raise ValueError(
            "raw-data file membership differs from manifest: "
            f"missing={missing[:5]!r}, unexpected={unexpected[:5]!r}"
        )

    for relative, row in expected.items():
        path = actual_files[relative]
        if path.stat().st_size != row["size_bytes"]:
            raise ValueError(f"raw-data file size differs: {relative}")
        if _sha256_file(path) != row["sha256"]:
            raise ValueError(f"raw-data file SHA-256 differs: {relative}")

    actual_total = sum(path.stat().st_size for path in actual_files.values())
    if header.get("file_count") != len(rows):
        raise ValueError("raw-data manifest file count does not match its records")
    if header.get("total_size_bytes") != actual_total:
        raise ValueError("raw-data manifest total size does not match restored files")
    return {
        "file_count": len(rows),
        "schema_version": SCHEMA_VERSION,
        "total_size_bytes": actual_total,
        "verified": True,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_DIR)
    create_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_DIR)
    verify_parser.add_argument("--manifest", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "create":
        result = create_manifest(raw_root=args.raw_root, output=args.output)
    else:
        result = verify_manifest(raw_root=args.raw_root, manifest=args.manifest)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
