import logging
from pathlib import Path

import docker  # type: ignore
from docker.errors import APIError, ImageNotFound  # type: ignore

DOCKER_IMAGE_NAME = "plz-builder"


def build_docker_image(client: docker.APIClient, install_path: Path):
    """
    Create a docker image named `plz-builder`.
    Builds image from `install_path`

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        install_path (:obj:`pathlib.Path`): The directory to include in the docker
            image

    Raises:
        :obj:`docker.errors.APIError`: If the build fails
    """
    root_dir = Path(__file__).absolute().parent

    logging.info(f"Building Docker Image: {DOCKER_IMAGE_NAME}")
    with (root_dir / "Dockerfile").open(mode="rb") as f:
        try:
            build_stream = client.build(
                path=install_path,
                fileobj=f,
                tag=DOCKER_IMAGE_NAME,
                rm=True,
                encoding="UTF-8",
                decode=True,
            )
        except APIError:
            logging.error(
                "Build Failed. Ensure Docker is installed and running, and that "
                "internet access is available for the first run. Installation cannot "
                "proceed."
            )
            raise

    # Print output stream
    for line in build_stream:
        if "stream" not in line or line["stream"].strip() == "":
            continue
        logging.debug(line["stream"])
    logging.info("Build Completed Successfully")


def start_docker_container(
    client: docker.APIClient, container_name: str, install_path: Path
):
    """
    Start a docker container from the `plz-builder` image with the name
    `container_name`, and mount `install_path` to `/root/deps`

    Also mounts the user's SSH config folder (`~/.ssh`) to ensure that dependencies
    pulled from private repos are accessible as they would be to the user running the
    program.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_name (:obj:`str`): The name of the container to start
        install_path (:obj:`pathlib.Path`): The path to mount to the container's
            dependency directory

    Raises:
        :obj:`docker.errors.ImageNotFound`: If the `plz-builder` image doesn't exist
        :obj:`docker.errors.APIError`: If any other docker error occurs
    """

    logging.info(f"Creating Container: {container_name}")
    try:
        container_id = client.create_container(
            DOCKER_IMAGE_NAME,
            detach=True,
            tty=True,
            name=container_name,
            host_config=client.create_host_config(
                binds={
                    f"{install_path.resolve()}": {"bind": "/root/deps"},
                    f"{Path.home()}/.ssh": {"bind": "/root/.ssh"},
                }
            ),
        )
    except ImageNotFound:
        logging.error(
            f"{DOCKER_IMAGE_NAME} not found. Could not start container: "
            f"{container_name}"
        )
        raise
    except APIError as e:
        logging.error("Could not Create Docker Container")

        # If we fail here because container_name already exists, let's just delete it
        # and tell the user to try again
        if e.status_code == 409 and "conflict" in e.explanation.lower():
            logging.error(
                f"{container_name} already exists. It will now be deleted "
                "so that you can try running this again"
            )
            stop_docker_container(client, container_name)
        raise
    logging.info("Container Creation Successful")

    logging.info(f"Start Container: {container_name}")
    try:
        client.start(container=container_id["Id"])
    except APIError:
        logging.error(f"Could not start docker container: {container_name}")
        stop_docker_container(client, container_name)
        raise
    logging.info("Container Successfully Started")


def stop_docker_container(client: docker.APIClient, container_name: str):
    """
    Stop and remove the docker container with the name `container_name`

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_name (:obj:`str`): The name of the container to stop

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """

    logging.info(f"Stop Container: {container_name}")
    try:
        client.stop(container=container_name)
    except APIError:
        logging.error(f"Could not stop container: {container_name}")
        raise
    logging.info("Container Successfully Stopped")

    logging.info(f"Remove Container: {container_name}")
    try:
        client.remove_container(container=container_name)
    except APIError:
        logging.error(f"Could not remove container: {container_name}")
        raise
    logging.info("Container Successfully Removed")


def pip_install(client: docker.APIClient, container_name: str, dependency: str):
    """
    Pip install the python `dependency` in the docker container `container_name`.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_name (:obj:`str`): The name of the container to stop
        dependency (:obj:`str`): The python library to install

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """
    cmd = ["python3", "-m", "pip", "install", "-t", "/root/deps", dependency]

    logging.info(f"Pip Install {dependency}: {cmd}")

    try:
        ex = client.exec_create(
            container_name, cmd=cmd, environment=["PYTHONPATH=/root/deps"]
        )
    except APIError:
        logging.error(f"Could not create docker exec command: {cmd}")
        stop_docker_container(client, container_name)
        raise

    try:
        result = client.exec_start(ex["Id"], stream=True)
    except APIError:
        logging.error(f"Could not execute docker exec command: {cmd}")
        stop_docker_container(client, container_name)
        raise

    for line in result:
        decoded = line.decode("UTF-8").strip()
        logging.info(decoded)

    logging.info("Pip Install Complete")
