import logging
from pathlib import Path

import docker
import pytest

from helpers.util import MockAPIClient
from plz.cli import main as plz_main, parse_args


def test_parse_args():
    args = parse_args(["-r", "requirements.txt", "test1.py", "testpath"])
    assert args.build == Path("./build")
    assert args.files == [Path("test1.py"), Path("testpath")]
    assert not args.force
    assert args.requirements == [Path("requirements.txt")]
    assert args.zipped_prefix is None
    assert args.log_level == logging.ERROR


@pytest.fixture()
def mock_api_client(monkeypatch):
    monkeypatch.setattr(docker, "APIClient", MockAPIClient)


def test_main(mock_api_client, tmpdir):
    build_path = Path(tmpdir / "build")
    build_path.mkdir()

    file_path = Path(tmpdir / "test.py")
    with file_path.open("w") as f:
        f.write("#test")

    plz_main(["--build", str(build_path), "--", str(file_path)])

    assert (build_path / "package.zip").exists()
