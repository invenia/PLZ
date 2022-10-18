from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from docker import APIClient

from tests.helpers.util import cleanup_image, requires_docker, run_docker_command


def test_name_image():
    from plz.docker import name_image

    # look, if this breaks because you went out of your way to name your
    # copy of this repo something weird, this is on you.
    assert name_image() == f"plz-{Path.cwd().name}"
    assert name_image("") == name_image()

    # check that symbols get removed as necessary and that segments get
    # prioritized since they are somewhat meaningful
    assert name_image("a--b-/c..d./_e__f/") == "plz-a--b/c.d/e_f"

    # truncate long names correctly
    assert name_image("x" * 500) == f"plz-{'x' * 124}"
    assert name_image(f"a--b{'-' * 500}c") == "plz-a--b"
    assert name_image(f"{'-' * 500}b") == "plz"


def test_validate_image_name():
    from plz.docker import validate_image_name

    for name in ("", "a-", "b..c", "a//b", "a/", "x" * 129):
        with pytest.raises(ValueError):
            validate_image_name(name)
            print(name)

    for name in ("a", "a--b", "b.c", "a/b/c/d", "x" * 128):
        validate_image_name(name)


def test_validate_tag_name():
    from plz.docker import validate_tag_name

    for name in ("a-", "b..c", "a//b", "a/", "x" * 129):
        with pytest.raises(ValueError):
            validate_tag_name(name)
            print(name)

    for name in (None, "", "a", "a--b", "b.c", "x" * 128):
        validate_tag_name(name)


@requires_docker
def test_build_image(tmp_path):
    from plz.build import DEFAULT_PYTHON, MAX_PYTHON_VERSION, MIN_PYTHON_VERSION
    from plz.docker import build_image, build_system_docker_file

    client = APIClient()
    with cleanup_image() as image_name:
        docker_file = tmp_path / "DockerFile"

        build_system_docker_file(docker_file, [], DEFAULT_PYTHON)
        image_id = build_image(image_name, docker_file)

        assert image_id

        info = client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        container_id = info["Id"]
        client.start(container_id)
        assert run_docker_command(
            client, container_id, ("python", "--version")
        ).startswith(f"Python {DEFAULT_PYTHON}")

        with cleanup_image() as other_image_name:
            if MIN_PYTHON_VERSION == DEFAULT_PYTHON:
                version = MAX_PYTHON_VERSION
            else:
                version = MIN_PYTHON_VERSION

            build_system_docker_file(docker_file, [], version)
            build_image(other_image_name, docker_file)

            assert len(client.images(name=image_name)) == 1
            assert len(client.images(name=other_image_name)) == 1

            info = client.create_container(
                other_image_name, "/bin/bash", detach=True, tty=True
            )
            container_id = info["Id"]
            client.start(container_id)
            assert run_docker_command(
                client, container_id, ("python", "--version")
            ).startswith(f"Python {version}")


@requires_docker
def test_delete_image(tmp_path):
    from plz.build import DEFAULT_PYTHON
    from plz.docker import build_image, build_system_docker_file, delete_image

    client = APIClient()
    with cleanup_image() as image_name, cleanup_image() as other_image_name:
        docker_file = tmp_path / "DockerFile"
        other_docker_file = tmp_path / "OtherDockerFile"

        build_system_docker_file(docker_file, [], DEFAULT_PYTHON)
        build_image(image_name, docker_file)

        # add a non-running container
        client.create_container(image_name, "/bin/bash", detach=True, tty=True)
        # add a running container
        container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]
        client.start(container_id)

        build_system_docker_file(other_docker_file, ["rsync"], DEFAULT_PYTHON)
        build_image(other_image_name, other_docker_file)
        client.create_container(other_image_name, "/bin/bash", detach=True, tty=True)
        assert delete_image(other_image_name, force=False)

        assert 0 == len(
            client.containers(filters={"ancestor": other_image_name}, all=True)
        )
        assert len(client.images(name=other_image_name)) == 0

        # shouldn't have affected other images
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) > 0
        assert len(client.images(name=image_name)) == 1

        # delete shouldn't work with running containers
        with pytest.raises(Exception):
            print(delete_image(image_name, force=False))
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) > 0
        assert len(client.images(name=image_name)) == 1

        # stop_containers should force the image to delete
        assert delete_image(image_name, force=True)
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 0
        assert len(client.images(name=image_name)) == 0


@requires_docker
def test_start_container(tmp_path):
    import plz.docker
    from plz.build import MAX_PYTHON_VERSION, MIN_PYTHON_VERSION
    from plz.docker import (
        IMAGE_VERSION,
        IMAGE_VERSION_FILE,
        PYTHON_VERSION_FILE,
        build_image,
        build_system_docker_file,
        start_container,
    )

    client = APIClient()
    with cleanup_image() as image_min, cleanup_image() as image_max:
        docker_file = tmp_path / "DockerFile"

        build_system_docker_file(docker_file, [], MIN_PYTHON_VERSION)
        min_id = build_image(image_min, docker_file)

        container_name = f"{image_min}-container"

        container_id = start_container(
            min_id, container_name, tmp_path, MIN_PYTHON_VERSION
        )

        assert (
            container_id
            == client.containers(
                filters={"name": container_name, "ancestor": image_min}, quiet=True
            )[0]["Id"]
        )

        assert (
            run_docker_command(
                client, container_id, ("cat", str(IMAGE_VERSION_FILE))
            ).strip()
            == IMAGE_VERSION
        )
        assert (
            run_docker_command(
                client, container_id, ("cat", str(PYTHON_VERSION_FILE))
            ).strip()
            == MIN_PYTHON_VERSION
        )
        assert run_docker_command(
            client, container_id, ("python", "--version")
        ).startswith(f"Python {MIN_PYTHON_VERSION}")

        # starting again should be safe
        new_container_id = start_container(
            min_id, container_name, tmp_path, MIN_PYTHON_VERSION
        )

        assert new_container_id == container_id
        assert (
            container_id
            == client.containers(
                filters={"name": container_name, "ancestor": image_min}, quiet=True
            )[0]["Id"]
        )

        # Making a new image shouldn't break the old one
        build_system_docker_file(docker_file, [], MAX_PYTHON_VERSION)
        max_id = build_image(image_max, docker_file)
        max_container_name = f"{image_max}-container"
        max_container_id = start_container(
            max_id, max_container_name, tmp_path, MAX_PYTHON_VERSION
        )

        assert (
            container_id
            == client.containers(
                filters={"name": container_name, "ancestor": image_min}, quiet=True
            )[0]["Id"]
        )
        assert (
            max_container_id
            == client.containers(
                filters={"name": max_container_name, "ancestor": image_max}, quiet=True
            )[0]["Id"]
        )

        assert (
            run_docker_command(
                client, max_container_id, ("cat", str(IMAGE_VERSION_FILE))
            ).strip()
            == IMAGE_VERSION
        )
        assert (
            run_docker_command(
                client, max_container_id, ("cat", str(PYTHON_VERSION_FILE))
            ).strip()
            == MAX_PYTHON_VERSION
        )
        assert run_docker_command(
            client, max_container_id, ("python", "--version")
        ).startswith(f"Python {MAX_PYTHON_VERSION}")

        # A mismatched python version should error
        with pytest.raises(Exception):
            container_id = start_container(
                min_id, container_name, tmp_path, MAX_PYTHON_VERSION
            )

        # A mismatched image version should be an error
        plz.docker.IMAGE_VERSION = f"1{plz.docker.IMAGE_VERSION}"
        with pytest.raises(Exception):
            container_id = plz.docker.start_container(
                min_id, container_name, tmp_path, MIN_PYTHON_VERSION
            )

    # Unknown image type
    with cleanup_image() as incompatible:
        list(
            client.build(
                fileobj=BytesIO(b"FROM lambci/lambda:build-python3.7\nENV HOME /root"),
                tag=incompatible,
                rm=True,
            )
        )
        with pytest.raises(Exception):
            start_container(
                incompatible,
                f"{incompatible}-container",
                tmp_path,
                MIN_PYTHON_VERSION,
            )

    # missing image should be an error
    with pytest.raises(Exception):
        container_id = start_container(
            "fake", "fake-container", tmp_path, MIN_PYTHON_VERSION
        )


@requires_docker
def test_stop_container(tmp_path):
    from plz.build import DEFAULT_PYTHON
    from plz.docker import build_image, build_system_docker_file, stop_container

    client = APIClient()
    with cleanup_image() as image_name:
        docker_file = tmp_path / "DockerFile"
        build_system_docker_file(docker_file, [], DEFAULT_PYTHON)
        build_image(image_name, docker_file)

        container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]
        other_container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]

        # can't stop a nonexistent container:
        with pytest.raises(Exception):
            stop_container(f"plz-fake-container-{uuid4()}")

        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 0
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        # stopping a stopped container is a no-op
        stop_container(container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 0
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        client.start(container_id)
        client.start(other_container_id)

        # should only stop the one
        stop_container(container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 1
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2


@requires_docker
def test_delete_container(tmp_path):
    from plz.build import DEFAULT_PYTHON
    from plz.docker import (
        build_image,
        build_system_docker_file,
        delete_container,
        stop_container,
    )

    client = APIClient()
    with cleanup_image() as image_name:
        docker_file = tmp_path / "DockerFile"
        build_system_docker_file(docker_file, [], DEFAULT_PYTHON)
        build_image(image_name, docker_file)

        container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]
        other_container_id = client.create_container(
            image_name, "/bin/bash", detach=True, tty=True
        )["Id"]

        # can't delete a nonexistent container:
        with pytest.raises(Exception):
            delete_container(f"plz-fake-container-{uuid4()}")

        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 0
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        client.start(container_id)
        client.start(other_container_id)

        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 2

        # deleting a running container is an error
        with pytest.raises(Exception):
            delete_container(container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 2
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 2

        # should only delete the one
        stop_container(container_id)
        delete_container(container_id)
        assert len(client.containers(filters={"ancestor": image_name}, all=False)) == 1
        assert len(client.containers(filters={"ancestor": image_name}, all=True)) == 1
