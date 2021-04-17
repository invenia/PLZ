import logging
from pathlib import Path

import docker
import pytest

from tests.helpers.util import MockAPIClient


def test_parse_args():
    from plz.cli import parse_args

    args = parse_args(["-r", "requirements.txt", "-p", "3.6", "test1.py", "testpath"])
    assert args.build == Path("./build")
    assert args.files == [Path("test1.py"), Path("testpath")]
    assert not args.force
    assert args.requirements == [Path("requirements.txt")]
    assert args.python_version == "3.6"
    assert args.zipped_prefix is None
    assert args.log_level == logging.ERROR


@pytest.fixture()
def mock_api_client(monkeypatch):
    monkeypatch.setattr(docker, "APIClient", MockAPIClient)


def test_main(mock_api_client, tmpdir):
    from plz.cli import main

    build_path = Path(tmpdir / "build")
    build_path.mkdir()

    file_path = Path(tmpdir / "test.py")
    with file_path.open("w") as f:
        f.write("#test")

    main(["--build", str(build_path), "--", str(file_path)])

    assert (build_path / "package.zip").exists()
