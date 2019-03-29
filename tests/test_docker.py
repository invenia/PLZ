from pathlib import Path

import pytest
from docker.errors import APIError, ImageNotFound

from helpers.util import MockAPIClient
from plz.docker import (
    build_docker_image,
    pip_install,
    start_docker_container,
    stop_docker_container,
)

TEST_CONTAINER = "test-container"
TEST_PYTHON_DEP = "pg8000"


def test_build_docker_image_success(tmpdir):
    build_docker_image(MockAPIClient(), tmpdir)


def test_build_docker_image_failure(tmpdir):
    with pytest.raises(APIError):
        build_docker_image(MockAPIClient(build_api_error=True), tmpdir)


def test_start_docker_container_success(tmpdir):
    # tmpdir doesn't have the `resolve` command, so I need to wrap it in a Path
    tmpdir_path = Path(str(tmpdir))
    start_docker_container(MockAPIClient(), TEST_CONTAINER, tmpdir_path)


def test_start_docker_container_cc_image_failure(tmpdir):
    tmpdir_path = Path(str(tmpdir))
    with pytest.raises(ImageNotFound):
        start_docker_container(
            MockAPIClient(cc_image_error=True), TEST_CONTAINER, tmpdir_path
        )


def test_start_docker_container_cc_api_failure(tmpdir):
    tmpdir_path = Path(str(tmpdir))
    with pytest.raises(APIError):
        start_docker_container(
            MockAPIClient(cc_api_error=True), TEST_CONTAINER, tmpdir_path
        )


def test_start_docker_container_start_api_failure(tmpdir):
    tmpdir_path = Path(str(tmpdir))
    with pytest.raises(APIError):
        start_docker_container(
            MockAPIClient(start_api_error=True), TEST_CONTAINER, tmpdir_path
        )


def test_stop_docker_container_success():
    stop_docker_container(MockAPIClient(), TEST_CONTAINER)


def test_stop_docker_container_stop_failure():
    with pytest.raises(APIError):
        stop_docker_container(MockAPIClient(stop_api_error=True), TEST_CONTAINER)


def test_stop_docker_container_rc_failure():
    with pytest.raises(APIError):
        stop_docker_container(MockAPIClient(rc_api_error=True), TEST_CONTAINER)


def test_pip_install_success():
    pip_install(MockAPIClient(), TEST_CONTAINER, TEST_PYTHON_DEP)


def test_pip_install_ec_failure():
    with pytest.raises(APIError):
        pip_install(MockAPIClient(ec_api_error=True), TEST_CONTAINER, TEST_PYTHON_DEP)


def test_pip_install_es_failure():
    with pytest.raises(APIError):
        pip_install(MockAPIClient(es_api_error=True), TEST_CONTAINER, TEST_PYTHON_DEP)
