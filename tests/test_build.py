"""
Tests for plz.build
"""
import json
from pathlib import Path
from zipfile import ZipFile

import docker
import pytest
from docker.errors import APIError

from helpers.util import MockAPIClient, MockAPIClientError
from plz.build import (
    build_package,
    copy_included_files,
    process_requirements,
    zip_package,
)

TEST_INFO = {
    "files": {"file1.py": 1},
    "requirements": {"requirements.txt": 1},
    "zipped_prefix": "prefix",
}


def test_copy_included_files_fresh_package_path(tmpdir):
    build_path = Path(tmpdir / "build")
    build_info = build_path / "build-info.json"
    package_path = build_path / "package"
    files_path = Path(tmpdir / "files")

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    copy_included_files(build_path, build_info, TEST_INFO, package_path, *files)

    assert build_path.exists()
    assert build_info.exists()
    assert package_path.exists()
    assert files_path.exists()

    assert (package_path / "file1.py").exists()
    assert (package_path / "file2.py").exists()
    assert (package_path / "testpath").exists()


def test_copy_included_files_existing_package_path(tmpdir):
    build_path = Path(tmpdir / "build")
    build_info = build_path / "build-info.json"
    package_path = build_path / "package"
    files_path = Path(tmpdir / "files")

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    build_path.mkdir()
    package_path.mkdir()
    copy_included_files(build_path, build_info, TEST_INFO, package_path, *files)

    assert build_path.exists()
    assert build_info.exists()
    assert package_path.exists()
    assert files_path.exists()

    assert (package_path / "file1.py").exists()
    assert (package_path / "file2.py").exists()
    assert (package_path / "testpath").exists()


@pytest.fixture()
def mock_api_client(monkeypatch):
    monkeypatch.setattr(docker, "APIClient", MockAPIClient)


@pytest.fixture()
def mock_api_client_error(monkeypatch):
    monkeypatch.setattr(docker, "APIClient", MockAPIClientError)


def test_process_requirements(mock_api_client, tmpdir):
    build_path = Path(tmpdir / "build")
    package_path = build_path / "package"
    requirements = tmpdir / "requirements.txt"
    env = build_path / "package-env"

    build_path.mkdir()
    package_path.mkdir()
    with requirements.open("w") as f:
        f.write("pg8000")

    # Create some env files
    env.mkdir()
    (env / "pg8000").mkdir()
    (env / "pg8000" / "__pycache__").mkdir()
    (env / "pg8000.dist-info").mkdir()
    with (env / "file.py").open("w") as f:
        f.write("# test")

    process_requirements((requirements,), package_path, env)

    assert build_path.exists()
    assert package_path.exists()
    assert (package_path / "file.py").exists()
    assert (package_path / "pg8000").exists()


def test_zip_package_no_prefix(tmpdir):
    package_path = Path(tmpdir / "package")
    zip_path = tmpdir / "package.zip"

    package_path.mkdir()
    with (package_path / "test1.py").open("w") as f:
        f.write("#test 1")
    (package_path / "testdir").mkdir()
    with (package_path / "testdir" / "testfile.py").open("w") as f:
        f.write("#test 2")

    zip_package(zip_path, package_path)

    assert zip_path.exists()

    with ZipFile(zip_path, "r") as z:
        assert set(z.namelist()) == {"test1.py", "testdir/testfile.py"}


def test_zip_package_with_prefix(tmpdir):
    package_path = Path(tmpdir / "package")
    zip_path = tmpdir / "package.zip"
    prefix = Path("prefix")

    package_path.mkdir()
    with (package_path / "test1.py").open("w") as f:
        f.write("#test 1")
    (package_path / "testdir").mkdir()
    with (package_path / "testdir" / "testfile.py").open("w") as f:
        f.write("#test 2")

    zip_package(zip_path, package_path, zipped_prefix=prefix)

    assert zip_path.exists()

    with ZipFile(zip_path, "r") as z:
        assert set(z.namelist()) == {"prefix/test1.py", "prefix/testdir/testfile.py"}


def test_build_package_only_files_build_info_exists(mock_api_client, tmpdir):
    build_path = Path(tmpdir / "build")
    files_path = Path(tmpdir / "files")
    build_info = build_path / "build-info.json"

    build_path.mkdir()
    with build_info.open("w") as stream:
        json.dump(TEST_INFO, stream)

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    zipfile = build_package(build_path, *files)
    assert zipfile == build_path / "package.zip"


def test_build_package_only_files_no_build_info(mock_api_client, tmpdir):
    build_path = Path(tmpdir / "build")
    files_path = Path(tmpdir / "files")

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    zipfile = build_package(build_path, *files)
    assert zipfile == build_path / "package.zip"


def test_build_package_only_files_same_build_info(mock_api_client, tmpdir):
    build_path = Path(tmpdir / "build")
    files_path = Path(tmpdir / "files")

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    zipfile = build_package(build_path, *files)
    assert zipfile == build_path / "package.zip"

    # This invocation will return early since build_info will be the same
    zipfile = build_package(build_path, *files)
    assert zipfile == build_path / "package.zip"


def test_build_package_with_requirements_force_and_prefix(mock_api_client, tmpdir):
    build_path = Path(tmpdir / "build")
    files_path = Path(tmpdir / "files")

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    requirements = Path(tmpdir / "requirements.txt")
    with requirements.open("w") as f:
        f.write("pg8000")

    zipfile = build_package(
        build_path,
        *files,
        requirements=requirements,
        zipped_prefix=Path("prefix"),
        force=True,
    )
    assert zipfile == build_path / "package.zip"


def test_build_package_failure(mock_api_client_error, tmpdir):
    build_path = Path(tmpdir / "build")
    files_path = Path(tmpdir / "files")

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for f in files:
        if str(f).endswith(".py"):
            with f.open("w") as x:
                x.write("# test")
        else:
            f.mkdir()

    requirements = Path(tmpdir / "requirements.txt")
    with requirements.open("w") as f:
        f.write("pg8000")

    with pytest.raises(APIError):
        build_package(build_path, *files, requirements=requirements)
