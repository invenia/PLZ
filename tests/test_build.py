"""
Tests for plz.build
"""
import json
import os
from pathlib import Path
from zipfile import ZipFile

import docker

from tests.helpers.util import (
    cleanup_image,
    hash_file,
    requires_docker,
    run_docker_command,
    update_json,
)


# On macOS and Windows, overwriting a file with a file with the same
# contents updates the mtime. On linux (or at least on the test-runners)
# this doesn't seem to be the case. This skips all tests that mtimes are
# different. We are still doing the checks that mtime is the same when
# the file shouldn't have been touched so this doesn't seem to mask any
# bugs. A previous attempt was made to just sleep between rerun on linux
# but even that was not consistent. We would need to force a sleep in
# after the old zip is deleted to be sure probably and that seems overly
# invasive.
SKIP_MTIME_CHECKS = os.environ.get("SKIP_MTIME_CHECKS") == "1"


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
            "package/test1.py",
            "package/pg8000/test-x.py",
            "package/testdir/testfile.py",
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

    zip_package(zip_path, *package_path.iterdir(), Path(__file__), zipped_prefix=prefix)

    assert zip_path.exists()

    with ZipFile(zip_path, "r") as z:
        assert set(z.namelist()) == {
            "prefix/test1.py",
            "prefix/testdir/testfile.py",
            "prefix/test_build.py",
        }


def test_build_zip_files_only(tmp_path):
    from plz.build import PACKAGE_INFO_VERSION, build_zip

    build_path = tmp_path / "build"
    files_path = tmp_path / "files"
    zip_info = build_path / "zip-info.json"

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for path in files:
        if path.suffix == ".py":
            with path.open("w") as stream:
                stream.write("# test")
        else:
            path.mkdir()

    zipfile = build_zip(build_path, *files)
    assert zipfile == build_path / "package.zip"

    file_hashes = {
        str(path.absolute()): hash_file(path) for path in files if path.is_file()
    }

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
    assert info["prefix"] is None
    assert info["zip"] == hash_file(zipfile)

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"file1.py", "file2.py"}

    # rerunning should not force a rezip
    mtime = zipfile.stat().st_mtime
    contents = hash_file(zipfile)
    assert zipfile == build_zip(build_path, *files)
    assert hash_file(zipfile) == contents
    assert zipfile.stat().st_mtime == mtime

    # rebuild should force a rezip
    assert zipfile == build_zip(build_path, *files, rebuild=True)
    assert hash_file(zipfile) == contents
    if not SKIP_MTIME_CHECKS:
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
    contents = hash_file(zipfile)

    assert zipfile == build_zip(build_path, *files)
    assert hash_file(zipfile) != contents
    if not SKIP_MTIME_CHECKS:
        assert zipfile.stat().st_mtime != mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
    assert info["prefix"] is None

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"file1.py", "file2.py"}
        for name in z.namelist():
            with z.open(name) as zstream, (files_path / name).open("rb") as stream:
                assert zstream.read() == stream.read()

    # adding a prefix should force a rezip
    mtime = zipfile.stat().st_mtime
    contents = hash_file(zipfile)
    assert zipfile == build_zip(build_path, *files, zipped_prefix="lambda")
    assert hash_file(zipfile) != contents
    if not SKIP_MTIME_CHECKS:
        assert zipfile.stat().st_mtime != mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
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
    contents = hash_file(zipfile)
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_zip(build_path, *files, zipped_prefix="lambda")
    assert hash_file(zipfile) != contents
    if not SKIP_MTIME_CHECKS:
        assert zipfile.stat().st_mtime != mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
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
    contents = hash_file(zipfile)

    assert zipfile == build_zip(build_path, *files, zipped_prefix="lambda")
    assert hash_file(zipfile) != contents
    if not SKIP_MTIME_CHECKS:
        assert zipfile.stat().st_mtime != mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # modifying the zipfile should force a rezip
    contents = hash_file(zipfile)

    with zipfile.open("w") as stream:
        stream.write("fake!")

    mtime = zipfile.stat().st_mtime

    assert zipfile == build_zip(build_path, *files, zipped_prefix="lambda")
    assert hash_file(zipfile) == contents
    if not SKIP_MTIME_CHECKS:
        assert zipfile.stat().st_mtime != mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # changing python shouldn't affect file-only zips
    mtime = zipfile.stat().st_mtime

    assert zipfile == build_zip(
        build_path, *files, zipped_prefix="lambda", python_version="2.7"
    )
    assert hash_file(zipfile) == contents
    assert zipfile.stat().st_mtime == mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}

    # invalid package info version should force a rezip
    with update_json(zip_info) as info:
        info["version"] = "fake"

    mtime = zipfile.stat().st_mtime

    assert zipfile == build_zip(build_path, *files, zipped_prefix="lambda")
    assert hash_file(zipfile) == contents
    if not SKIP_MTIME_CHECKS:
        assert zipfile.stat().st_mtime != mtime

    with zip_info.open("r") as stream:
        info = json.load(stream)

    assert info["version"] == PACKAGE_INFO_VERSION
    assert info["files"] == file_hashes
    assert info["image_id"] is None
    assert info["prefix"] == "lambda"

    with ZipFile(zipfile, "r") as z:
        assert set(z.namelist()) == {"lambda/file1.py", "lambda/file2.py"}


@requires_docker
def test_build_image(tmp_path):
    from plz.build import DEFAULT_PYTHON, build_image
    from plz.docker import INSTALLED_SYSTEM, WORKING_DIRECTORY

    if os.environ.get("SSH_AUTH_SOCK"):
        ci_build_token = os.environ.get("CI_BUILD_TOKEN")
        if ci_build_token:
            url = (
                f"git+https://gitlab-ci-token:{ci_build_token}@gitlab.invenia.ca"
                "/invenia/plz.git"
            )
        else:
            url = "git+ssh://git@gitlab.invenia.ca/invenia/plz.git"
    else:
        url = "plz"  # fall back to pypi

    client = docker.APIClient()

    with cleanup_image() as image_name:
        # empty image
        image = build_image(tmp_path, image=image_name)

        assert image == image_name

        info = client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        container_id = info["Id"]
        client.start(container_id)
        assert run_docker_command(
            client, container_id, ("python", "--version")
        ).startswith(f"Python {DEFAULT_PYTHON}")
        client.remove_container(container_id, force=True)
        # all 3 layers should be the same
        image_id = client.images(image_name)[0]["Id"]
        assert client.images(f"{image_name}-system")[0]["Id"] == image_id
        assert client.images(f"{image_name}-python")[0]["Id"] == image_id

        # add file
        file_1 = tmp_path / "file.py"
        file_1.write_text("print('hi')")

        file_2 = tmp_path / "requirements.txt"
        file_2.write_text("fake999999999999")

        image = build_image(
            tmp_path, file_1, file_2, image=image_name, location=tmp_path
        )

        assert image == image_name

        info = client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        container_id = info["Id"]
        client.start(container_id)
        assert (
            run_docker_command(
                client,
                container_id,
                ("cat", f"{WORKING_DIRECTORY / 'file.py'}"),
            )
            == "print('hi')"
        )
        assert (
            run_docker_command(
                client,
                container_id,
                ("cat", f"{WORKING_DIRECTORY / 'requirements.txt'}"),
            )
            == "fake999999999999"
        )
        client.remove_container(container_id, force=True)
        # top layer should be different
        top_id = client.images(image_name)[0]["Id"]
        assert top_id != image_id
        assert client.images(f"{image_name}-system")[0]["Id"] == image_id
        assert client.images(f"{image_name}-python")[0]["Id"] == image_id

        requirements_file = tmp_path / "requirements" / "requirements.txt"
        (tmp_path / "requirements").mkdir()

        with requirements_file.open("w") as stream:
            stream.write("\n".join((url, "cloudspy", "pytz")))

        image = build_image(
            tmp_path,
            file_1,
            file_2,
            requirements=requirements_file,
            image=image_name,
            location=tmp_path,
        )

        assert image == image_name

        info = client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        container_id = info["Id"]
        client.start(container_id)
        assert (
            run_docker_command(
                client,
                container_id,
                ("cat", f"{WORKING_DIRECTORY / 'file.py'}"),
            )
            == "print('hi')"
        )
        assert (
            run_docker_command(
                client,
                container_id,
                ("cat", f"{WORKING_DIRECTORY / 'requirements.txt'}"),
            )
            == "fake999999999999"
        )
        installed = {
            item["name"]
            for item in json.loads(
                run_docker_command(
                    client, container_id, ("pip", "list", "--format", "json")
                )
            )
        }
        assert "plz" in installed
        assert "cloudspy" in installed
        assert "pytz" in installed
        client.remove_container(container_id, force=True)
        # top and layer should be different
        new_top_id = client.images(image_name)[0]["Id"]
        assert new_top_id != top_id
        python_id = client.images(f"{image_name}-python")[0]["Id"]
        assert python_id != new_top_id
        assert python_id != image_id
        assert client.images(f"{image_name}-system")[0]["Id"] == image_id

        image = build_image(
            tmp_path,
            file_1,
            file_2,
            requirements=requirements_file,
            system_packages=["rsync"],
            image=image_name,
            location=tmp_path,
        )

        assert image == image_name

        info = client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        container_id = info["Id"]
        client.start(container_id)
        assert (
            run_docker_command(
                client,
                container_id,
                ("cat", f"{WORKING_DIRECTORY / 'file.py'}"),
            )
            == "print('hi')"
        )
        assert (
            run_docker_command(
                client,
                container_id,
                ("cat", f"{WORKING_DIRECTORY / 'requirements.txt'}"),
            )
            == "fake999999999999"
        )
        installed = {
            item["name"]
            for item in json.loads(
                run_docker_command(
                    client, container_id, ("pip", "list", "--format", "json")
                )
            )
        }
        assert "plz" in installed
        assert "cloudspy" in installed
        assert "pytz" in installed
        assert run_docker_command(
            client, container_id, ("grep", "^rsync", str(INSTALLED_SYSTEM))
        ).strip()
        client.remove_container(container_id, force=True)
        # top and layer should be different
        new_new_top_id = client.images(image_name)[0]["Id"]
        assert new_new_top_id != new_top_id
        new_python_id = client.images(f"{image_name}-python")[0]["Id"]
        assert new_python_id != python_id
        assert new_python_id != new_new_top_id
        system_id = client.images(f"{image_name}-system")[0]["Id"]
        assert system_id != image_id
        assert system_id != new_python_id


@requires_docker
def test_build_zip(tmp_path):
    from plz.build import build_zip

    if os.environ.get("SSH_AUTH_SOCK"):
        ci_build_token = os.environ.get("CI_BUILD_TOKEN")
        if ci_build_token:
            url = (
                f"git+https://gitlab-ci-token:{ci_build_token}@gitlab.invenia.ca"
                "/invenia/plz.git"
            )
        else:
            url = "git+ssh://git@gitlab.invenia.ca/invenia/plz.git"
    else:
        url = "plz"  # fall back to pypi

    build_path = tmp_path / "build"
    files_path = tmp_path / "files"

    files = [files_path / "file1.py", files_path / "file2.py", files_path / "testpath"]

    files_path.mkdir()
    for path in files:
        if path.suffix == ".py":
            with path.open("w") as stream:
                stream.write("# test")
        else:
            path.mkdir()

    requirements_1 = tmp_path / "requirements.txt"
    requirements_1.write_text(url)
    requirements_2 = tmp_path / "requirements-1.txt"
    requirements_2.write_text("cloudspy\npytz")
    requirements = [requirements_1, requirements_2]

    with cleanup_image() as image_name:
        zipfile = build_zip(
            build_path,
            *files,
            requirements=requirements,
            location=tmp_path,
            image=image_name,
        )
        assert zipfile == build_path / "package.zip"

        with ZipFile(zipfile, "r") as z:
            all_files = set(z.namelist())

        assert "file1.py" in all_files
        assert "file2.py" in all_files
        assert "plz/__init__.py" in all_files
        assert "cloudspy/__init__.py" in all_files
        assert "pytz/__init__.py" in all_files
        assert "boto3/__init__.py" not in all_files

        # remove requirement
        zipfile = build_zip(
            build_path,
            *files,
            requirements=requirements_2,
            location=tmp_path,
            image=image_name,
        )
        assert zipfile == build_path / "package.zip"

        with ZipFile(zipfile, "r") as z:
            all_files = set(z.namelist())

        assert "file1.py" in all_files
        assert "file2.py" in all_files
        assert "plz/__init__.py" not in all_files
        assert "cloudspy/__init__.py" in all_files
        assert "pytz/__init__.py" in all_files
        assert "boto3/__init__.py" not in all_files

        zipfile = build_zip(
            build_path,
            *files,
            location=tmp_path,
            image=image_name,
        )
        assert zipfile == build_path / "package.zip"

        with ZipFile(zipfile, "r") as z:
            all_files = set(z.namelist())

        assert "file1.py" in all_files
        assert "file2.py" in all_files
        assert "plz/__init__.py" not in all_files
        assert "cloudspy/__init__.py" not in all_files
        assert "pytz/__init__.py" not in all_files
        assert "boto3/__init__.py" not in all_files
