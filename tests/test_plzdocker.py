import json
import re
from io import BytesIO
from pathlib import PurePosixPath
from shutil import rmtree
from uuid import uuid4

import pytest
from docker import APIClient
from docker.errors import APIError, ImageNotFound

from tests.helpers.util import cleanup_image, requires_docker, update_json


@requires_docker
def test_build_docker_image():
    from plz.plzdocker import build_docker_image, run_docker_command

    client = APIClient()
    with cleanup_image() as image_name:

        build_docker_image(client, image_name, python_version="3.7")
        assert len(client.images(name=image_name)) == 1

        info = client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        container_id = info["Id"]
        client.start(container_id)
        assert run_docker_command(
            client, container_id, ("python", "--version")
        ).startswith("Python 3.7")

        with cleanup_image() as other_image_name:
            build_docker_image(client, other_image_name, python_version="3.8")

            assert len(client.images(name=image_name)) == 1
            assert len(client.images(name=other_image_name)) == 1

            info = client.create_container(
                other_image_name, "/bin/bash", detach=True, tty=True
            )
            container_id = info["Id"]
            client.start(container_id)
            assert run_docker_command(
                client, container_id, ("python", "--version")
            ).startswith("Python 3.8")


@requires_docker
def test_delete_docker_image():
    from plz.plzdocker import build_docker_image, delete_docker_image

    client = APIClient()
    with cleanup_image() as image_name, cleanup_image() as other_image_name:
        build_docker_image(client, image_name)
        # add a non-running container
        client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        # add a running container
        container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]
        client.start(container_id)

        build_docker_image(client, other_image_name)
        client.create_container(other_image_name, "/bin/bash", detach=True, tty=True)
        delete_docker_image(client, other_image_name, stop_containers=False)

        assert 0 == len(
            client.containers(filters={"ancestor": other_image_name}, all=True)
        )
        assert len(client.images(name=other_image_name)) == 0

        # shouldn't have affected other images
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) > 0
        assert len(client.images(name=image_name)) == 1

        # stop_containers should force the image to delete
        delete_docker_image(client, image_name, stop_containers=True)
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 0
        assert len(client.images(name=image_name)) == 0


@requires_docker
def test_start_docker_container(tmp_path):
    from plz.plzdocker import (
        BUILD_INFO_FILENAME,
        BUILD_INFO_VERSION,
        DOCKER_IMAGE_INFO,
        DOCKER_IMAGE_VERSION,
        build_docker_image,
        run_docker_command,
        start_docker_container,
    )

    client = APIClient()
    with cleanup_image() as image_name, cleanup_image() as image_38:
        container_name = f"{image_name}-container"

        build_docker_image(client, image_name, python_version="3.7")

        # new container
        container_id = start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=False,
        )

        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        build_info = tmp_path / BUILD_INFO_FILENAME

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.7"
        assert info["container-id"] == container_id
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}

        # We said no secrets!
        root_files = run_docker_command(client, container_id, ("ls", "-a", "/root"))
        assert ".ssh" not in root_files
        assert ".pip" not in root_files

        # these checks are more image checks but oh well
        image_info = json.loads(
            run_docker_command(client, container_id, ("cat", DOCKER_IMAGE_INFO))
        )

        assert image_info == {"version": DOCKER_IMAGE_VERSION, "python": "3.7"}
        assert run_docker_command(
            client, container_id, ("python", "--version")
        ).startswith("Python 3.7")

        # starting again should be safe
        assert container_id == start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=False,
        )

        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        # Fresh should force a new container to be made
        # build info should be overwritten so let's make sure
        with update_json(build_info) as info:
            info["extra-key"] = "shouldn't exist"
            info["system-packages"]["fake"] = {
                "package": "fake.no_arch",
                "version": "1.0.0",
                "files": [],
            }

        old_id = container_id
        container_id = start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=True,
        )

        assert old_id != container_id
        # old container shouldn't exist!
        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.7"
        assert info["container-id"] == container_id
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}
        assert "extra-key" not in info

        # A missing build_info file should force a rebuild
        build_info.unlink()

        old_id = container_id
        container_id = start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=False,
        )

        assert old_id != container_id
        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.7"
        assert info["container-id"] == container_id
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}

        # A mismatched container_id should force a rebuild
        with update_json(build_info) as info:
            info["container-id"] = old_id
            info["extra-key"] = "shouldn't exist"
            info["system-packages"]["fake"] = {
                "package": "fake.no_arch",
                "version": "1.0.0",
                "files": [],
            }

        old_id = container_id
        container_id = start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=False,
        )

        assert old_id != container_id
        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.7"
        assert info["container-id"] == container_id
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}
        assert "extra-key" not in info

        # A mismatched creation_date should force a rebuild
        with update_json(build_info) as info:
            info["creation-date"] = "fake time"
            info["extra-key"] = "shouldn't exist"
            info["system-packages"]["fake"] = {
                "package": "fake.no_arch",
                "version": "1.0.0",
                "files": [],
            }

        old_id = container_id
        container_id = start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=False,
        )

        assert old_id != container_id
        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.7"
        assert info["container-id"] == container_id
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}
        assert "extra-key" not in info

        # A mismatched version should force a rebuild
        with update_json(build_info) as info:
            info["version"] = "0.0.1"
            info["extra-key"] = "shouldn't exist"
            info["system-packages"]["fake"] = {
                "package": "fake.no_arch",
                "version": "1.0.0",
                "files": [],
            }

        old_id = container_id
        container_id = start_docker_container(
            client,
            container_name,
            tmp_path,
            image_name,
            no_secrets=True,
            python_version="3.7",
            fresh=False,
        )

        assert old_id != container_id
        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.7"
        assert info["container-id"] == container_id
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}
        assert "extra-key" not in info

        # A mismatched image version should error
        with pytest.raises(ValueError):
            start_docker_container(
                client,
                container_name,
                tmp_path,
                image_name,
                no_secrets=True,
                python_version="3.8",
                fresh=False,
            )

        # Making a 3.8 image shouldn't break 3.7
        build_docker_image(client, image_38, python_version="3.8")
        name_38 = f"{image_38}-container"
        container_38 = start_docker_container(
            client,
            name_38,
            tmp_path,
            image_38,
            no_secrets=True,
            python_version="3.8",
            fresh=False,
        )

        assert [{"Id": container_id}] == client.containers(
            filters={"name": container_name, "ancestor": image_name}, quiet=True
        )
        assert [{"Id": container_38}] == client.containers(
            filters={"name": name_38, "ancestor": image_38}, quiet=True
        )

        assert build_info.exists()

        with build_info.open("r") as stream:
            info = json.load(stream)

        assert info["version"] == BUILD_INFO_VERSION
        assert info["python-version"] == "3.8"
        assert info["container-id"] == container_38
        assert len(info["base-packages"]) > 0
        assert info["system-packages"] == {}
        assert info["python-packages"] == {}

        image_info = json.loads(
            run_docker_command(client, container_38, ("cat", DOCKER_IMAGE_INFO))
        )

        assert image_info == {"version": DOCKER_IMAGE_VERSION, "python": "3.8"}
        assert run_docker_command(
            client, container_38, ("python", "--version")
        ).startswith("Python 3.8")

    # Unknown image type
    with cleanup_image() as incompatible:
        list(
            client.build(
                fileobj=BytesIO(b"FROM lambci/lambda:build-python3.7\nENV HOME /root"),
                tag=incompatible,
                rm=True,
            )
        )
        with pytest.raises(ValueError):
            start_docker_container(
                client,
                f"{incompatible}-container",
                tmp_path,
                incompatible,
                no_secrets=True,
                python_version="3.8",
                fresh=False,
            )

    # Incompatible image version
    with cleanup_image() as incompatible:
        list(
            client.build(
                fileobj=BytesIO(
                    "FROM lambci/lambda:build-python3.7\n"
                    "RUN echo '{}' > /image-info.json\n"
                    "ENV HOME /root".format(
                        json.dumps({"version": "0.0.1", "python": "3.7"})
                    ).encode("utf-8")
                ),
                tag=incompatible,
                rm=True,
            )
        )
        with pytest.raises(ValueError):
            start_docker_container(
                client,
                f"{incompatible}-container",
                tmp_path,
                incompatible,
                no_secrets=True,
                python_version="3.7",
                fresh=False,
            )

    # Incompatible python
    with cleanup_image() as incompatible:
        list(
            client.build(
                fileobj=BytesIO(
                    "FROM lambci/lambda:build-python3.7\n"
                    "RUN echo {} > /image-info.json\n"
                    "ENV HOME /root".format(
                        json.dumps({"version": "0.0.1", "python": "3.7"})
                    ).encode("utf-8")
                ),
                tag=incompatible,
                rm=True,
            )
        )
        with pytest.raises(ValueError):
            start_docker_container(
                client,
                f"{incompatible}-container",
                tmp_path,
                incompatible,
                no_secrets=True,
                python_version="3.8",
                fresh=False,
            )

    # Missing image should be an error
    with pytest.raises(ImageNotFound):
        start_docker_container(
            client,
            f"{incompatible}-container",
            tmp_path,
            incompatible,
            no_secrets=True,
            python_version="3.8",
            fresh=False,
        )


@requires_docker
def test_stop_docker_container():
    from plz.plzdocker import build_docker_image, stop_docker_container

    client = APIClient()
    with cleanup_image() as image_name:
        build_docker_image(client, image_name)
        container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]
        other_container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]

        # can't stop a nonexistent container:
        with pytest.raises(APIError):
            stop_docker_container(client, f"plz-fake-container-{uuid4()}")

        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 0
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        # stopping a stopped container is a no-op
        stop_docker_container(client, container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 0
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        client.start(container_id)
        client.start(other_container_id)

        # should only stop the one
        stop_docker_container(client, container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 1
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2


@requires_docker
def test_delete_docker_container():
    from plz.plzdocker import (
        build_docker_image,
        delete_docker_container,
        stop_docker_container,
    )

    client = APIClient()
    with cleanup_image() as image_name:
        build_docker_image(client, image_name)
        container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]
        other_container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]

        # can't delete a nonexistent container:
        with pytest.raises(APIError):
            delete_docker_container(client, f"plz-fake-container-{uuid4()}")

        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 0
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        client.start(container_id)
        client.start(other_container_id)

        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 2

        # deleting a running container is an error
        with pytest.raises(APIError):
            delete_docker_container(client, container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 2
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        # should only delete the one
        stop_docker_container(client, container_id)
        delete_docker_container(client, container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 1
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 1


@requires_docker
def test_pip_install(tmp_path):
    from plz.plzdocker import build_docker_image, pip_install, start_docker_container

    # NOTE: we'll be testing private repos and downloaded packages in build
    # TODO: test pip_args
    with cleanup_image() as image_name:
        container_name = f"{image_name}-container"
        client = APIClient()
        build_docker_image(client, image_name)
        container_id = start_docker_container(
            client, container_name, tmp_path, image_name, no_secrets=True
        )

        assert "5.3.1" == pip_install(
            client, container_id, "pyyaml==5.3.1", name="pyyaml"
        )
        assert "1.1.0" == pip_install(
            client, container_id, "xlrd!=1.2.0,<2.0,>=1.0.1", name="xlrd"
        )

        # no constraints
        requests_version = pip_install(client, container_id, "requests")
        assert requests_version is not None

        # updated constraint
        other_requests = pip_install(
            client, container_id, f"requests!={requests_version}", name="requests"
        )
        assert requests_version != other_requests
        assert other_requests is not None

        assert requests_version == pip_install(client, container_id, "requests")

        # make sure it doesn't error if it doesn't know a name to search pip show
        assert pip_install(client, container_id, "openpyxl>1.0.0") is None

        python_directory = tmp_path / "python"
        assert python_directory.is_dir()
        assert (python_directory / "yaml").exists()
        assert (python_directory / "xlrd-1.1.0.dist-info").exists()
        assert (python_directory / "requests").exists()
        assert (python_directory / "openpyxl").exists()


@requires_docker
def test_system_install(tmp_path):
    """
    Tests for ym_install, list_system_packages, and copy_system_packages
    at the same time because it saves a bunch of work
    """

    from plz.plzdocker import (
        build_docker_image,
        copy_system_packages,
        list_system_packages,
        run_docker_command,
        start_docker_container,
        yum_install,
    )

    system_directory = tmp_path / "system"
    system_directory.mkdir(exist_ok=True)

    client = APIClient()
    with cleanup_image() as image_name:
        build_docker_image(client, image_name)
        container_name = f"{image_name}-container"
        container_id = start_docker_container(
            client, container_name, tmp_path, image_name, no_secrets=True
        )

        build_info = tmp_path / "build-info.json"
        with build_info.open("r") as stream:
            info = json.load(stream)

        base = info["base-packages"]

        # this test is tautological
        assert base == list_system_packages(client, container_id)

        # copy should produce nothing when nothing's been installed
        installed = {}
        assert [] == copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_filetypes={".so"},
        )
        assert installed == {}
        assert list(system_directory.iterdir()) == []

        yum_install(client, container_id, "libpng")

        assert "libpng" in run_docker_command(
            client, container_id, ("yum", "list", "installed")
        )

        # only libpng should be added
        installed_packages = list_system_packages(client, container_id)
        assert set(installed_packages) - set(base) == {"libpng"}

        # skip ignored files
        copied_files = copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_filetypes={".so"},
            skip={"libpng"},
        )
        assert len(copied_files) == 0
        assert installed == {}
        assert {path.name for path in system_directory.iterdir()} == set()

        # should copy files
        copied_files = copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_filetypes={".so"},
        )
        assert len(copied_files) > 0
        assert set(installed) == {"libpng"}
        assert installed["libpng"]["files"] == copied_files
        assert {path.name for path in system_directory.iterdir()} == {
            PurePosixPath(path).name for path in copied_files
        }

        # running again should copy nothing
        rmtree(system_directory)
        system_directory.mkdir()
        assert [] == copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_filetypes={".so"},
        )
        assert set(installed) == {"libpng"}
        assert installed["libpng"]["files"] == copied_files
        assert {path.name for path in system_directory.iterdir()} == set()

        # copy nothing if you didn't ask for anything
        rmtree(system_directory)
        system_directory.mkdir()
        installed = {}
        assert [] == copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
        )
        assert set(installed) == set()
        assert {path.name for path in system_directory.iterdir()} == set()

        # ask for specific files
        assert set(copied_files) == set(
            copy_system_packages(
                client,
                container_id,
                base,
                installed_packages=installed,
                desired_files={"libpng": copied_files},
            )
        )
        assert set(installed) == {"libpng"}
        assert installed["libpng"]["files"] == copied_files
        assert {path.name for path in system_directory.iterdir()} == {
            PurePosixPath(path).name for path in copied_files
        }

        # ask for specific file/make sure undesired files are deleted
        # we need to mark this as an update
        installed["libpng"]["version"] = f'{installed["libpng"]["version"]}-old'
        assert [copied_files[0]] == copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_files={"libpng": copied_files[0]},
        )
        assert set(installed) == {"libpng"}
        assert installed["libpng"]["files"] == [copied_files[0]]
        assert {path.name for path in system_directory.iterdir()} == {
            PurePosixPath(copied_files[0]).name
        }

        # updated base package
        base2 = dict(base)
        base2["libgomp"] = "fake"
        assert 0 < len(
            copy_system_packages(
                client,
                container_id,
                base2,
                installed_packages=installed,
                desired_filetypes={".so"},
            )
        )
        del installed["libgomp"]
        for path in system_directory.iterdir():
            if path.name.startswith("libgomp"):
                path.unlink()

        # make sure files get deleted when they're no longer wanted
        installed["fake"] = {"version": "fake", "files": []}
        for index in range(5):
            name = f"file{index}.so"
            installed["fake"]["files"].append(
                str(PurePosixPath("/root/dependencies/system") / name)
            )
            installed["fake"]["files"].append(
                str(PurePosixPath("/root/dependencies/system") / f"missing-{name}")
            )
            with (system_directory / name).open("w") as stream:
                stream.write("fake!")

        assert [] == copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_files={"libpng": copied_files[0]},
            skip={"fake"},
        )
        assert set(installed) == {"libpng"}
        assert installed["libpng"]["files"] == [copied_files[0]]
        assert {path.name for path in system_directory.iterdir()} == {
            PurePosixPath(copied_files[0]).name
        }

        assert [] == copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_files={"libpng": copied_files[0]},
            skip={"libpng"},
        )
        assert set(installed) == set()
        assert {path.name for path in system_directory.iterdir()} == set()

        # real-world test. what datafeeds needs
        rmtree(system_directory)
        system_directory.mkdir()
        container_id = start_docker_container(
            client, container_name, tmp_path, image_name, no_secrets=True, fresh=True
        )

        with build_info.open("r") as stream:
            info = json.load(stream)

        base = info["base-packages"]

        yum_install(client, container_id, "grib_api-devel")
        packages = list_system_packages(client, container_id)

        # not exclusive
        expected_packages = {
            "eccodes",
            "eccodes-data",
            "eccodes-devel",
            "libgomp",
            "libjpeg-turbo",
            "libpng",
            "openjpeg2",
        }

        for dependency in expected_packages:
            assert dependency in packages

        installed = info["system-packages"]
        copied_files = copy_system_packages(
            client,
            container_id,
            base,
            installed_packages=installed,
            desired_filetypes={".so"},
            desired_files={"eccodes-data": ["/usr/share/eccodes"]},
            include={"libgomp"},
        )

        for dependency in expected_packages:
            assert dependency in installed

        assert len(copied_files) > len(expected_packages)
        assert (
            system_directory / PurePosixPath(installed["libgomp"]["files"][0]).name
        ).exists()
        assert (system_directory / "eccodes" / "definitions").is_dir()


@requires_docker
def test_fix_file_permissions(tmp_path):
    from plz.plzdocker import (
        build_docker_image,
        fix_file_permissions,
        run_docker_command,
        start_docker_container,
    )

    client = APIClient()
    with cleanup_image() as image_name:
        container_name = f"{image_name}-container"
        build_docker_image(client, image_name)
        container_id = start_docker_container(
            client, container_name, tmp_path, image_name, no_secrets=True
        )
        run_docker_command(client, container_id, ["mkdir", "/root/dependencies/python"])
        run_docker_command(
            client,
            container_id,
            ["mkdir", "-p", "/root/dependencies/system/subdirectory"],
        )
        run_docker_command(
            client,
            container_id,
            ["touch", "/root/dependencies/python/file"],
        )
        run_docker_command(
            client,
            container_id,
            ["touch", "/root/dependencies/system/file"],
        )
        run_docker_command(
            client,
            container_id,
            ["touch", "/root/dependencies/system/subdirectory/file"],
        )
        run_docker_command(
            client,
            container_id,
            [
                "ln",
                "-s",
                "/root/dependencies/system/subdirectory/file",
                "/root/dependencies/system/link",
            ],
        )
        run_docker_command(
            client,
            container_id,
            ["find", "/root/dependencies", "-exec", "chmod", "u=rwx,go=", "{}", ";"],
        )

        # don't make changes on mac
        fix_file_permissions(client, container_id, platform="darwin")
        for directory in ("python", "system", "system/subdirectory"):
            assert "drwx------\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/{directory}"],
            )

        for file in ("python/file", "system/file", "system/subdirectory/file"):
            assert "-rwx------\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/{file}"],
            )
        assert re.search(
            r"^l(?:r[w-][x-]){3}$",
            run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", "/root/dependencies/system/link"],
            ),
        )

        # mark things readable on linux
        fix_file_permissions(client, container_id, platform="linux")
        for directory in ("python", "system", "system/subdirectory"):
            assert "drwxrw-rw-\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/{directory}"],
            )

        for file in ("python/file", "system/file", "system/subdirectory/file"):
            assert "-rwxrw-rw-\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/{file}"],
            )
        assert re.search(
            r"^l(?:r[w-][x-]){3}$",
            run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", "/root/dependencies/system/link"],
            ),
        )

        run_docker_command(
            client,
            container_id,
            ["find", "/root/dependencies", "-exec", "chmod", "u=rwx,go=", "{}", ";"],
        )

        # copy things to a new directory on windows
        fix_file_permissions(client, container_id, platform="win32")
        for directory in ("python", "system", "system/subdirectory"):
            assert "drwxrw-rw-\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/{directory}"],
            )

        for file in ("python/file", "system/file", "system/subdirectory/file"):
            assert "-rwxrw-rw-\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/{file}"],
            )
        assert re.search(
            r"^l(?:r[w-][x-]){3}$",
            run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", "/root/dependencies/system/link"],
            ),
        )

        for directory in ("python", "system", "system/subdirectory"):
            assert "drwxrw-rw-\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/win32-{directory}"],
            )
        for file in (
            "python/file",
            "system/file",
            "system/link",
            "system/subdirectory/file",
        ):
            assert "-rwxrw-rw-\n" == run_docker_command(
                client,
                container_id,
                ["stat", "--format=%A", f"/root/dependencies/win32-{file}"],
            )


@requires_docker
def test_run_docker_command(tmp_path):
    from plz.plzdocker import (
        build_docker_image,
        run_docker_command,
        start_docker_container,
    )

    client = APIClient()
    with cleanup_image() as image_name:
        container_name = f"{image_name}-container"
        build_docker_image(client, image_name)
        container_id = start_docker_container(
            client, container_name, tmp_path, image_name, no_secrets=True
        )
        assert "hello\nhi" == run_docker_command(
            client, container_id, ("echo", "-n", "hello\nhi")
        )

        # TODO: test that checks that buffered output is being read correctly

        # invalid command
        with pytest.raises(RuntimeError):
            run_docker_command(client, container_id, ("python", "-c", "1(2)"))

        client.stop(container_id)

        # stopped command
        with pytest.raises(APIError):
            run_docker_command(client, container_id, ("echo", "-n", "hello\nhi"))
