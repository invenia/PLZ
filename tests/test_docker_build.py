import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from plz.build import build_package


@pytest.mark.skipif(
    os.environ.get("NODOCKER") == "1", reason="This test needs docker to run"
)
def test_docker_build(tmpdir):
    build = Path(tmpdir) / "build"
    zip_path = build / "package.zip"
    package_dir = build / "package"

    package = build_package(build)
    assert package == zip_path

    with ZipFile(package, "r") as archive:
        assert archive.namelist() == []

    # Rerunning shouldn't remake, settings are the same
    package_dir.rmdir()  # Should be empty

    package = build_package(build)
    assert package == zip_path
    assert not package_dir.exists()

    # Force should cause a rebuild
    package = build_package(build, force=True)
    assert package == zip_path
    assert package_dir.exists()

    # Build info should be deleted on error
    file1 = Path(tmpdir) / "file1.py"

    try:
        package = build_package(build, file1)
    except IOError:
        pass  # This should fail
    else:  # Uh-oh
        raise AssertionError("build_package didn't raise IO Error")

    assert not (build / "build-info.json").exists()

    # Will need to rebuild
    file1.write_text("# file 1")

    package = build_package(build, file1)
    assert package == zip_path

    with ZipFile(package, "r") as archive:
        assert archive.namelist() == ["file1.py"]
        assert archive.read("file1.py") == file1.read_bytes()

    # Directory should not be copied
    lambda_dir = Path(tmpdir) / "lambda"
    lambda_dir.mkdir()
    file2 = lambda_dir / "file2.py"
    file2.write_text("# file 2")

    package = build_package(build, file1, file2)
    assert package == zip_path

    with ZipFile(package, "r") as archive:
        assert set(archive.namelist()) == {"file1.py", "file2.py"}
        assert archive.read("file1.py") == file1.read_bytes()
        assert archive.read("file2.py") == file2.read_bytes()

    # Directory should be copied
    package = build_package(build, file1, lambda_dir)
    assert package == zip_path

    with ZipFile(package, "r") as archive:
        assert set(archive.namelist()) == {"file1.py", "lambda/file2.py"}
        assert archive.read("file1.py") == file1.read_bytes()
        assert archive.read("lambda/file2.py") == file2.read_bytes()

    # Add requirements and directory prefix
    requirements = Path(tmpdir) / "requirements.txt"
    requirements.write_text("pg8000==1.11\n")  # Last version, should be stable
    yum_requirements = Path(tmpdir) / "yum.yaml"
    yum_requirements.write_text("libpng:\n  - /usr/lib64/libpng15.so.15\n")

    package = build_package(
        build,
        file1,
        lambda_dir,
        requirements=requirements,
        yum_requirements=yum_requirements,
        zipped_prefix=Path("python"),
    )
    assert package == zip_path

    with ZipFile(package, "r") as archive:
        assert set(archive.namelist()) == {
            "python/file1.py",
            "python/lambda/file2.py",
            "python/libpng15.so.15",
            "python/pg8000/__init__.py",
            "python/pg8000/_version.py",
            "python/pg8000/core.py",
            "python/six.py",
        }

    # with a specific Python version
    requirements.write_text("psycopg2-binary==2.8\n")  # Last version, should be stable

    package = build_package(
        build, file1, lambda_dir, requirements=requirements, python_version="3.6"
    )
    assert package == zip_path

    with ZipFile(package, "r") as archive:
        assert (
            "psycopg2/_psycopg.cpython-36m-x86_64-linux-gnu.so" in archive.namelist()
            or "psycopg2/_psycopg.cpython-36-x86_64-linux-gnu.so" in archive.namelist()
        )
