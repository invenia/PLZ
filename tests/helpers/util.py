import json
import os
from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
from pathlib import Path
from typing import Dict, Generator, Optional, Sequence
from uuid import uuid4

import pytest
from docker import APIClient
from docker.errors import APIError, ImageNotFound


class MockResponse(object):
    def __init__(self, status_code):
        self.status_code = status_code


class MockAPIClient(object):
    def __init__(
        self,
        build_api_error=False,
        cc_image_error=False,
        cc_api_error=False,
        cc_api_conflict_error=False,
        start_api_error=False,
        stop_api_error=False,
        rc_api_error=False,
        ec_api_error=False,
        es_api_error=False,
        ei_api_error=False,
        ei_exit_code=0,
    ):
        self.build_api_error = build_api_error
        self.cc_image_error = cc_image_error
        self.cc_api_error = cc_api_error
        self.cc_api_conflict_error = cc_api_conflict_error
        self.start_api_error = start_api_error
        self.stop_api_error = stop_api_error
        self.rc_api_error = rc_api_error
        self.ec_api_error = ec_api_error
        self.es_api_error = es_api_error
        self.ei_api_error = ei_api_error
        self.ei_exit_code = ei_exit_code

    def build(self, *args, **kwargs):
        if self.build_api_error:
            raise APIError("test api error")
        return [
            {"stream": "test stream"},
            {"stream": "test stream 2"},
            {"api": {"thing": "some other thing"}},
            {"stream": ""},
        ]

    def create_container(self, *args, **kwargs):
        if self.cc_image_error:
            raise ImageNotFound("test image not found")
        if self.cc_api_error:
            raise APIError("test api error")
        if self.cc_api_conflict_error:
            raise APIError(
                "test api error",
                response=MockResponse(409),
                explanation="Conflict the container already exists",
            )
        return {"Id": "test id"}

    def create_host_config(self, *args, **kwargs):
        # Make sure the paths defined in binds exist
        binds = kwargs["binds"]
        for bind in binds:
            path = Path(bind)
            if not path.exists():
                path.mkdir()

    def start(self, *args, **kwargs):
        if self.start_api_error:
            raise APIError("test api error")

    def stop(self, *args, **kwargs):
        if self.stop_api_error:
            raise APIError("test api error")

    def remove_container(self, *args, **kwargs):
        if self.rc_api_error:
            raise APIError("test api error")

    def exec_create(self, *args, **kwargs):
        if self.ec_api_error:
            raise APIError("test api error")
        return {"Id": "test id"}

    def exec_start(self, *args, **kwargs):
        if self.es_api_error:
            raise APIError("test api error")
        return [b"test stream ", b"test stream 2"]

    def exec_inspect(self, *args, **kwargs):
        if self.ei_api_error:
            raise APIError("test api error")
        return {"ExitCode": self.ei_exit_code}


class MockAPIClientError(MockAPIClient):
    def build(self, *args, **kwargs):
        raise APIError("test api error")


@contextmanager
def cleanup_image(name: Optional[str] = None) -> Generator[str, None, None]:
    """
    Generate a docker name that will be fully cleaned up on manager exit.

    Args:
        name (:obj:`Optional[str]`): The name to use for the image. If
            unsupplied, a unique name will be automatically generated.
    """
    if name is None:
        name = f"test-image-{uuid4()}"

    try:
        yield name
    finally:
        client = APIClient()

        for image in (name, f"{name}-python", f"{name}-system"):
            for container in client.containers(
                filters={"ancestor": image}, quiet=True, all=True
            ):
                client.remove_container(container["Id"], force=True)

            if client.images(name=image):
                client.remove_image(image)


@contextmanager
def update_json(path: Path) -> Generator[Dict, None, None]:
    """
    Load a json file containing a dictionary and then save it again on exit
    """
    with path.open("r") as stream:
        info = json.load(stream)

    yield info

    with path.open("w") as stream:
        json.dump(info, stream)


def requires_docker(function):
    """
    Skip this test if the environment doesn't have docker.
    """

    @pytest.mark.skipif(
        os.environ.get("NODOCKER") == "1", reason="This test needs docker to run"
    )
    @wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapper


def hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def run_docker_command(
    client: APIClient,
    container_id: str,
    cmd: Sequence[str],
    environment: Optional[Sequence[str]] = None,
) -> str:
    ex = client.exec_create(container_id, cmd=cmd, environment=environment)
    result = client.exec_start(ex["Id"], stream=True)

    cmd_output = []
    for line in result:
        decoded = line.decode("UTF-8")
        cmd_output.append(decoded)

    exec_info = client.exec_inspect(ex["Id"])

    if exec_info["ExitCode"]:
        cmd_output_string = "".join(cmd_output)
        exception_string = (
            f"The following error occurred while executing {cmd}:\n{cmd_output_string}"
        )
        raise RuntimeError(exception_string)

    return "".join(cmd_output)
