import subprocess
from pathlib import Path

from pipelines._common import (
    DATA_ROOT_ENV,
    configured_data_root,
    get_code_revision,
    repository_relative_path,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_code_revision_identifies_clean_and_dirty_source_deterministically(tmp_path):
    git(tmp_path, "init", "-q")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )

    clean = get_code_revision(repository_root=tmp_path)
    tracked.write_text("dirty\n", encoding="utf-8")
    dirty = get_code_revision(repository_root=tmp_path)

    assert clean is not None and "-dirty-" not in clean
    assert dirty is not None and dirty.startswith(clean + "-dirty-")
    assert get_code_revision(repository_root=tmp_path) == dirty


def test_repository_relative_path_removes_machine_prefix():
    path = Path(__file__).resolve()

    rendered = repository_relative_path(path)

    assert rendered == "packages/research/tests/test_pipeline_common.py"
    assert not rendered.startswith("/")


def test_configured_data_root_defaults_to_repository_data(tmp_path):
    assert configured_data_root(
        environment={}, repository_root=tmp_path
    ) == tmp_path / "data"


def test_configured_data_root_accepts_external_location(tmp_path):
    external = tmp_path / "restored-quantfore-data"

    assert configured_data_root(
        environment={DATA_ROOT_ENV: str(external)}, repository_root=tmp_path
    ) == external
