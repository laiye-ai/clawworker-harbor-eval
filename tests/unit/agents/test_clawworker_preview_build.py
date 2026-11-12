"""Build inputs come from the pinned Host commit, not a dirty checkout."""

import subprocess

import pytest


def test_export_uses_committed_host_inputs(tmp_path):
    from examples.agents.clawworker_preview.build import export_host

    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "tracked.txt").write_text("committed\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    (repo / "tracked.txt").write_text("uncommitted\n")
    (repo / "private.env").write_text("not a build input\n")
    exported = tmp_path / "exported"
    export_host(repo, commit, exported)
    assert (exported / "tracked.txt").read_text() == "committed\n"
    assert not (exported / "private.env").exists()
    assert (repo / "tracked.txt").read_text() == "uncommitted\n"


def test_export_refuses_to_overwrite_an_existing_directory(tmp_path):
    from examples.agents.clawworker_preview.build import export_host

    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep").write_text("keep")
    with pytest.raises(FileExistsError):
        export_host(tmp_path / "unused", "a" * 40, target)
    assert (target / "keep").read_text() == "keep"
