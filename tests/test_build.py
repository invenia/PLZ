"""
Tests for plz.build
"""
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import docker
import pytest

from tests.helpers.util import (
    MockAPIClient,
    cleanup_image,
    hash_file,
    requires_docker,
    update_json,
)


@pytest.fixture()
def mock_api_client(monkeypatch):
    monkeypatch.setattr(docker, "APIClient", MockAPIClient)


def test_zip_package_no_prefix(tmpdir):
    from plz.build import zip_package

    package_path = Path(tmpdir / "package")
    zip_path = tmpdir / "package.zip"

    package_path.mkdir()
    with (package_path / "test1.py").open("w") as f:
        f.write("#test 1")
    (package_path / "testdir").mkdir()
    with (package_path / "testdir" / "testfile.py").open("w") as f:
        f.write("#test 2")

    (package_path / "pg8000").mkdir()
    (package_path / "pg8000" / "__pycache__").mkdir()
    (package_path / "pg8000.dist-info").mkdir()
    (package_path / "pg8000" / "test-x.py").write_text("# test")

    zip_package(zip_path, package_path)

    assert zip_path.exists()

    with ZipFile(zip_path, "r") as z:
        assert set(z.namelist()) == {
            "test1.py",
            "pg8000/test-x.py",
            "testdir/testfile.py",
        }


def test_zip_package_with_prefix(tmpdir):
    from plz.build import zip_package

    package_path = Path(tmpdir / "package")
    zip_path = tmpdir / "package.zip"
    prefix = Path("prefix")

    package_path.mkdir()
    with (package_path / "test1.py").open("w") as f:
        f.write("#test 1")
    (package_path / "testdir").mkdir()
    with (package_path / "testdir" / "testfile.py").open("w") as f:
        f.write("#test 2")

    zip_package(zip_path, package_path, Path(__file__), zipped_prefix=prefix)

    assert zip_path.exists()

    with ZipFile(zip_path, "r") as z:
        assert set(z.namelist()) == {
            "prefix/test1.py",
            "prefix/testdir/testfile.py",
            "prefix/test_build.py",
        }


@requires_docker
def test_process_requirements(tmp_path):
    from plz.build import process_requirements

    build_path = Path(tmp_path / "build")
    requirements = tmp_path / "requirements.txt"
    system_requirements = tmp_path / "yum.yaml"

    with requirements.open("w") as stream:
        stream.write("pyyaml==5.3.1")
    with system_requirements.open("w") as stream:
        stream.write("version: 0.1.0\nfiletypes:\n  - .so\npackages:\n  - libpng")

    build = tmp_path / "build"

    with cleanup_image() as image_name:
        container_name = f"{image_name}-container"

        process_requirements(
            (requirements,),
            system_requirements,
            build,
            image_name,
            container_name,
            python_version="3.7",
            no_secrets=True,
        )

        # Image and container should still exist. the container should be stopped
        client = docker.APIClient()
        assert 1 == len(client.images(name=image_name, quiet=True))
        assert 0 == len(
            client.containers(
                filters={"ancestor": image_name, "name": container_name}, quiet=True
            )
        )
        assert 1 == len(
            client.containers(
                filters={"ancestor": image_name, "name": container_name},
                all=True,
                quiet=True,
            )
        )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["python-version"] == "3.7"
        assert (
            info["container-id"]
            == client.containers(
                filters={"ancestor": image_name, "name": container_name},
                all=True,
                quiet=True,
            )[0]["Id"]
        )
        assert "5.3.1" in info["python-packages"]["pyyaml"]
        assert "libpng" in info["system-packages"]
        assert 0 < len(info["system-packages"]["libpng"]["files"])

        assert build_path.exists()
        assert (build_path / "python" / "yaml").exists()
        for file in info["system-packages"]["libpng"]["files"]:
            path = PurePosixPath(file)
            assert (build_path / "system" / path.name).exists()

        # removing all dependency files should remove all dependencies
        process_requirements(
            (),
            None,
            build,
            image_name,
            container_name,
            python_version="3.7",
            no_secrets=True,
        )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {} == info["python-packages"]
        assert {} == info["system-packages"]
        assert not (build_path / "python").exists()
        assert not (build_path / "system").exists()

        # new dependencies
        with requirements.open("w") as stream:
            stream.write("pyyaml==5.3.1\nxlrd!=1.2.0,<2.0,>=1.0.1")
        with system_requirements.open("w") as stream:
            stream.write(
                "version: 0.1.0\n"
                "filetypes:\n"
                "  - .so\n"
                "packages:\n"
                "  - libpng\n"
                "  - libwebp\n"
            )

        print("---------------")
        process_requirements(
            (requirements,),
            system_requirements,
            build,
            image_name,
            container_name,
            python_version="3.7",
            no_secrets=True,
            reinstall_system=True,
        )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        print(info["system-packages"])
        print("libwebp" in info["base-packages"])

        assert {"pyyaml", "xlrd"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["pyyaml"] == "5.3.1"
        assert info["python-packages"]["xlrd"] == "1.1.0"
        assert "libpng" in info["system-packages"]
        assert "libwebp" in info["system-packages"]
        assert (build_path / "python" / "yaml").exists()
        assert (build_path / "python" / "xlrd").exists()
        for package in ("libpng", "libwebp"):
            for file in info["system-packages"][package]["files"]:
                path = PurePosixPath(file)
                assert (build_path / "system" / path.name).exists()

        # deleting dependencies
        libpng_files = info["system-packages"]["libpng"]["files"]
        with requirements.open("w") as stream:
            stream.write(
                "# comments should be allowed in files\n"
                "xlrd!=1.2.0,<2.0,>=1.0.1\n"
                "\n\n\n\n"
                "# blank lines too!"
            )
        with system_requirements.open("w") as stream:
            stream.write(
                "version: 0.1.0\n"
                "filetypes:\n"
                "  - .so\n"
                "packages:\n"
                "  - libpng\n"
                "  - libwebp\n"
                "skip:\n"
                "  - libpng"
            )

        process_requirements(
            (requirements,),
            system_requirements,
            build,
            image_name,
            container_name,
            python_version="3.7",
            no_secrets=True,
        )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {"pyyaml", "xlrd"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["pyyaml"] == "5.3.1"
        assert info["python-packages"]["xlrd"] == "1.1.0"
        assert {"libwebp"} == set(info["system-packages"].keys())
        assert (build_path / "python" / "yaml").exists()
        assert (build_path / "python" / "xlrd").exists()
        for file in info["system-packages"]["libwebp"]["files"]:
            path = PurePosixPath(file)
            assert (build_path / "system" / path.name).exists()
        for file in libpng_files:
            path = PurePosixPath(file)
            assert not (build_path / "system" / path.name).exists()

        # rebuilding shouldn't affect python dependencies
        filepath = info["system-packages"]["libwebp"]["files"][0]
        with system_requirements.open("w") as stream:
            stream.write(
                "version: 0.1.0\n"
                "filetypes:\n"
                "  - .so\n"
                "packages:\n"
                f"  libwebp: {filepath}"
            )

        process_requirements(
            (requirements,),
            system_requirements,
            build,
            image_name,
            container_name,
            python_version="3.7",
            no_secrets=True,
            rebuild_image=True,
        )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {"xlrd"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["xlrd"] == "1.1.0"
        assert {"libwebp"} == set(info["system-packages"].keys())
        assert [filepath] == info["system-packages"]["libwebp"]["files"]
        assert (build_path / "python" / "yaml").exists()
        assert (build_path / "python" / "xlrd").exists()
        assert (build_path / "system" / PurePosixPath(filepath).name).exists()

        # private dependency
        with requirements.open("w") as stream:
            stream.write("git+ssh://git@gitlab.invenia.ca/invenia/plz.git@1.1.2")

        process_requirements(
            (requirements,),
            system_requirements,
            build,
            image_name,
            container_name,
            python_version="3.7",
            no_secrets=True,
            reinstall_python=True,
        )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {"plz"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["plz"] == "1.1.2"
        assert {"libwebp"} == set(info["system-packages"].keys())
        assert [filepath] == info["system-packages"]["libwebp"]["files"]
        assert (build_path / "python" / "plz").exists()
        assert (build_path / "system" / PurePosixPath(filepath).name).exists()

        # invalid image
        with pytest.raises(ValueError):
            process_requirements(
                (requirements,),
                system_requirements,
                build,
                image_name,
                container_name,
                python_version="3.8",
                no_secrets=True,
            )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {"plz"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["plz"] == "1.1.2"
        assert {"libwebp"} == set(info["system-packages"].keys())
        assert [filepath] == info["system-packages"]["libwebp"]["files"]
        assert (build_path / "python" / "plz").exists()
        assert (build_path / "system" / PurePosixPath(filepath).name).exists()

        # invalid system_requirements
        invalid_system_requirements = tmp_path / "invalid.yaml"
        with invalid_system_requirements.open("w") as stream:
            stream.write(
                "version: 0.2.0\n"
                "filetypes:\n"
                "  - .so\n"
                "packages:\n"
                f"  libwebp: {filepath}"
            )

        with pytest.raises(ValueError):
            process_requirements(
                (requirements,),
                invalid_system_requirements,
                build,
                image_name,
                container_name,
                python_version="3.7",
                no_secrets=True,
            )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {"plz"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["plz"] == "1.1.2"
        assert {"libwebp"} == set(info["system-packages"].keys())
        assert [filepath] == info["system-packages"]["libwebp"]["files"]
        assert (build_path / "python" / "plz").exists()
        assert (build_path / "system" / PurePosixPath(filepath).name).exists()

        # non-existent requirement
        with requirements.open("w") as stream:
            stream.write("git+ssh://git@gitlab.invenia.ca/invenia/plz.git@0.0.0")

        with pytest.raises(Exception):
            process_requirements(
                (requirements,),
                system_requirements,
                build,
                image_name,
                container_name,
                python_version="3.7",
                no_secrets=True,
            )

        build_info = build_path / "build-info.json"
        assert build_info.exists()
        with build_info.open("r") as stream:
            info = json.load(stream)

        assert {"plz"} <= set(info["python-packages"].keys())
        assert info["python-packages"]["plz"] == "1.1.2"
        assert {"libwebp"} == set(info["system-packages"].keys())
        assert [filepath] == info["system-packages"]["libwebp"]["files"]
        assert (build_path / "python" / "plz").exists()
        assert (build_path / "system" / PurePosixPath(filepath).name).exists()


def test_build_package_only_files(mock_api_client, tmp_path):
    from plz.build import build_package

    build_path = Path(tmp_path / "build")
    files_path = Path(tmp_path / "files")
    package_info = build_path / "package-info.json"

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for path in files:
        if path.suffix == ".py":
            with path.open("w") as stream:
                stream.write("# test")
        else:
            path.mkdir()

    zipfile = build_package(build_path, *files, python_version="3.7")
    assert zipfile == build_path / "package.zip"

    file_hashes = {
        str(path.absolute()): hash_file(path) for path in files if path.is_file()
    }

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] is None

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"file1.py", "file2.py"}

    # rerunning should not force a rezip
    mtime = zipfile.stat().st_mtime
    assert zipfile == build_package(build_path, *files, python_version="3.7")
    assert zipfile.stat().st_mtime == mtime

    # rezip should force a rezip
    assert zipfile == build_package(
        build_path, *files, python_version="3.7", rezip=True
    )
    assert zipfile.stat().st_mtime != mtime

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"file1.py", "file2.py"}

    # updating a file should force a rezip
    with files[0].open("w") as stream:
        stream.write("print('hello')")
    file_hashes = {
        str(path.absolute()): hash_file(path) for path in files if path.is_file()
    }
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(build_path, *files, python_version="3.7")
    assert zipfile.stat().st_mtime != mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] != "fake"
    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] is None

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"file1.py", "file2.py"}
        for name in z.namelist():
            with z.open(name) as zstream, (files_path / name).open("rb") as stream:
                assert zstream.read() == stream.read()

    # adding a prefix should force a rezip
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(
        build_path, *files, zipped_prefix="lambda", python_version="3.7"
    )
    assert zipfile.stat().st_mtime != mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # adding a file should force a rezip
    new_file = files_path / "new.py"
    with new_file.open("w") as stream:
        stream.write("print('hello')")
    files.append(new_file)
    file_hashes = {
        str(path.absolute()): hash_file(path) for path in files if path.is_file()
    }
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(
        build_path, *files, zipped_prefix="lambda", python_version="3.7"
    )
    assert zipfile.stat().st_mtime != mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {
            "lambda/file1.py",
            "lambda/file2.py",
            "lambda/new.py",
        }

    # removing a file should force a rezip
    del files[-1]
    file_hashes = {
        str(path.absolute()): hash_file(path) for path in files if path.is_file()
    }
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(
        build_path, *files, zipped_prefix="lambda", python_version="3.7"
    )
    assert zipfile.stat().st_mtime != mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # modifying the zipfile should force a rezip
    with zipfile.open("w") as stream:
        stream.write("fake!")

    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(
        build_path, *files, zipped_prefix="lambda", python_version="3.7"
    )
    assert zipfile.stat().st_mtime != mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # changing python shouldn't affect file-only zips
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(
        build_path, *files, zipped_prefix="lambda", python_version="2.7"
    )
    assert zipfile.stat().st_mtime == mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # invalid package info version should force a rezip
    with update_json(package_info) as info:
        info["version"] = "fake"

    mtime = zipfile.stat().st_mtime

    assert zipfile == build_package(
        build_path, *files, zipped_prefix="lambda", python_version="3.7"
    )
    assert zipfile.stat().st_mtime != mtime

    with package_info.open("r") as stream:
        info = json.load(stream)

    assert info.get("system", {}) == {}
    assert info.get("python", {}) == {}
    assert info["files"] == file_hashes
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}


@requires_docker
def test_build_package(tmp_path):
    from plz.build import build_package

    with cleanup_image() as image_name:
        build_path = Path(tmp_path / "build")
        files_path = Path(tmp_path / "files")
        package_info = build_path / "package-info.json"

        files = [
            files_path / "file1.py",
            files_path / "file2.py",
            files_path / "testpath",
        ]

        files_path.mkdir()
        for path in files:
            if path.suffix == ".py":
                with path.open("w") as stream:
                    stream.write("print('hi')")
            else:
                path.mkdir()

        requirements = tmp_path / "requirements.txt"
        with requirements.open("w") as stream:
            stream.write(
                "pyyaml\ngit+ssh://git@gitlab.invenia.ca/invenia/plz.git@1.1.2"
            )

        zipfile = build_package(
            build_path,
            *files,
            image_name=image_name,
            requirements=requirements,
            python_version="3.7",
            no_secrets=True,
        )
        assert zipfile == build_path / "package.zip"

        file_hashes = {
            str(path.absolute()): hash_file(path) for path in files if path.is_file()
        }
        requirements_hashes = {str(requirements.absolute()): hash_file(requirements)}

        with package_info.open("r") as stream:
            info = json.load(stream)

        assert info.get("system", {}) == {}
        assert info.get("python", {}) == requirements_hashes
        assert info["files"] == file_hashes
        assert info["prefix"] is None

        with ZipFile(zipfile, "r") as z:
            assert set(z.namelist()) >= {
                "file1.py",
                "file2.py",
                "yaml/__init__.py",
                "plz/__init__.py",
            }

        # rerun shouldn't touch zip
        mtime = zipfile.stat().st_mtime
        zipfile = build_package(
            build_path,
            *files,
            image_name=image_name,
            requirements=requirements,
            python_version="3.7",
            no_secrets=True,
        )
        assert zipfile == build_path / "package.zip"
        assert zipfile.stat().st_mtime == mtime
        with ZipFile(zipfile, "r") as z:
            assert set(z.namelist()) >= {
                "file1.py",
                "file2.py",
                "yaml/__init__.py",
                "plz/__init__.py",
            }

        # force with a different python
        mtime = zipfile.stat().st_mtime
        zipfile = build_package(
            build_path,
            *files,
            image_name=image_name,
            requirements=requirements,
            python_version="3.8",
            no_secrets=True,
            force=True,
        )
        assert zipfile == build_path / "package.zip"
        assert zipfile.stat().st_mtime != mtime
        with ZipFile(zipfile, "r") as z:
            assert set(z.namelist()) >= {
                "file1.py",
                "file2.py",
                "yaml/__init__.py",
                "plz/__init__.py",
            }

        # non-existent requirement
        with requirements.open("w") as stream:
            stream.write("git+ssh://git@gitlab.invenia.ca/invenia/plz.git@0.0.0")

        with pytest.raises(Exception):
            build_package(
                build_path,
                *files,
                image_name=image_name,
                requirements=requirements,
                python_version="3.7",
                no_secrets=True,
            )

        with package_info.open("r") as stream:
            info = json.load(stream)

        assert info.get("system", {}) == {}
        assert info.get("python", {}) == {}
        assert info["files"] == {}
        assert info["prefix"] is None
        assert info["zip"] is None
        assert not zipfile.exists()

        # real-world test. what datafeeds needs
        with requirements.open("w") as stream:
            stream.write("pygrib")

        system_requirements = tmp_path / "system.yaml"
        with system_requirements.open("w") as stream:
            stream.write(
                "version: 0.1.0\n"
                "filetypes:\n"
                "  - .so\n"
                "packages:\n"
                "  libgomp: []\n"
                "  grib_api-devel: []\n"
                "  eccodes-data: /usr/share/eccodes"
            )

        zipfile = build_package(
            build_path,
            *files,
            image_name=image_name,
            requirements=requirements,
            system_requirements=system_requirements,
            python_version="3.8",
            no_secrets=True,
            force=True,
        )

        directory = tmp_path / "package"
        ZipFile(zipfile).extractall(directory)

        print(list(directory.iterdir()))

        for file in files:
            if file.is_file():
                assert (directory / file.relative_to(files_path)).exists()

        assert (directory / "eccodes").exists()

        for library in ("libgomp", "libjpeg", "libgfortran", "libeccodes"):
            so_file = None
            for path in directory.iterdir():
                if path.name.startswith(library) and path.suffix.startswith(".so"):
                    so_file = path
                    break
            else:
                print(library)

            assert so_file is not None
