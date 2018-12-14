"""
Tests for plz.build
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile


def test_build():
    """
    Tests for plz.build.build_package
    """
    from plz.build import build_package

    with TemporaryDirectory() as name:
        directory = Path(name)
        build = directory / "build"

        package = build_package(build)

        assert package == build / "package.zip"

        with ZipFile(package, "r") as archive:
            assert archive.namelist() == []

        # rerunning shouldn't remake, settings are the same
        package_directory = build / "package"
        package_directory.rmdir()  # should be empty

        package = build_package(build)
        assert package == build / "package.zip"
        assert not package_directory.exists()

        # force should cuse a rebuild
        package = build_package(build, force=True)
        assert package == build / "package.zip"
        assert package_directory.exists()

        # build_info should be deleted on error
        file1 = directory / "file1.py"

        try:
            package = build_package(build, file1)
        except IOError:
            pass  # this should fail
        else:  # uh-oh
            raise AssertionError("build_package didn't raise IOError")

        assert not (build / "build-info.json").exists()

        # will need to rebuild
        file1.write_text("# file 1")

        package = build_package(build, file1)
        assert package == build / "package.zip"

        with ZipFile(package, "r") as archive:
            assert archive.namelist() == ["file1.py"]
            assert archive.read("file1.py") == file1.read_bytes()

        # directory should not be copied
        lambda_directory = directory / "lambda"
        lambda_directory.mkdir()
        file2 = lambda_directory / "file2.py"
        file2.write_text("# file 2")

        package = build_package(build, file1, file2)
        assert package == build / "package.zip"

        with ZipFile(package, "r") as archive:
            assert set(archive.namelist()) == {"file1.py", "file2.py"}
            assert archive.read("file1.py") == file1.read_bytes()
            assert archive.read("file2.py") == file2.read_bytes()

        # directory should be copied
        package = build_package(build, file1, lambda_directory)
        assert package == build / "package.zip"

        with ZipFile(package, "r") as archive:
            assert set(archive.namelist()) == {"file1.py", "lambda/file2.py"}
            assert archive.read("file1.py") == file1.read_bytes()
            assert archive.read("lambda/file2.py") == file2.read_bytes()

        # add requirements
        requirements = directory / "requirements.txt"
        requirements.write_text("pg8000==1.11\n")  # last version, should be stable

        package = build_package(
            build, file1, lambda_directory, requirements=[requirements]
        )
        assert package == build / "package.zip"

        with ZipFile(package, "r") as archive:
            assert set(archive.namelist()) == {
                "file1.py",
                "lambda/file2.py",
                "pg8000/__init__.py",
                "pg8000/_version.py",
                "pg8000/core.py",
                "six.py",
            }
