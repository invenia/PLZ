import json
import logging
import re
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Union, cast

import docker  # type: ignore
from docker.errors import APIError, ImageNotFound  # type: ignore


# Current as of 2021-04-08
DEFAULT_PYTHON = "3.8"
SUPPORTED_PYTHON = {"3.6", "3.7", "3.8"}

HOME_PATH = PurePosixPath("/root")
INSTALL_PATH = HOME_PATH / "dependencies"
PYTHON_INSTALL_PATH = INSTALL_PATH / "python"
SYSTEM_INSTALL_PATH = INSTALL_PATH / "system"


DOCKER_IMAGE_INFO = "/image-info.json"
DOCKERFILE_TEMPLATE = f"""
FROM lambci/lambda:build-python{{version}}
RUN export PYTHONPATH={PYTHON_INSTALL_PATH} && pip install --upgrade pip
RUN echo '{{image_info}}' > {DOCKER_IMAGE_INFO}
ENV HOME {HOME_PATH}
"""
DOCKER_IMAGE_VERSION = "0.1.0"
BUILD_INFO_FILENAME = "build-info.json"
BUILD_INFO_VERSION = "0.1.0"


def build_docker_image(
    client: docker.APIClient,
    image_name: str,
    python_version: str = DEFAULT_PYTHON,
):
    """
    Create a base docker image.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        image_name: (:obj:`str`): The name of the image being created.
        python_version (:obj:`str`): The version of Python to build for
            (<major>.<minor>), default: "3.8"
    Raises:
        :obj:`docker.errors.APIError`: If the build fails
        :obj:`ValueError`: If an incompatible image exists
    """
    logging.info("Building Docker Image: %s", image_name)
    docker_file = BytesIO(
        DOCKERFILE_TEMPLATE.format(
            version=python_version,
            image_info=json.dumps(
                {"version": DOCKER_IMAGE_VERSION, "python": python_version}
            ),
        ).encode("utf-8")
    )

    build_stream = client.build(
        fileobj=docker_file, tag=image_name, rm=True, encoding="UTF-8", decode=True
    )

    # Print output stream
    for line in build_stream:
        if "stream" not in line or line["stream"].strip() == "":
            continue
        logging.debug(line["stream"])
    logging.info("Build Completed Successfully")


def delete_docker_image(
    client: docker.APIClient, image_name: str, stop_containers: bool = True
):
    """
    Delete an existing docker image.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        image_name: (:obj:`str`): The name of the image being created.
    Raises:
        :obj:`docker.errors.APIError`: If the delete fails

    If stop_containers is True, existing containers will first be
    stopped and shut down

    NOTE: docker's api doesn't actually seem to consistently error if
    # a container is still running
    """
    if stop_containers:
        # if we're deleting them, we can just force them to quit
        for container in client.containers(
            filters={"ancestor": image_name}, quiet=True, all=True
        ):
            client.remove_container(container["Id"], force=True)

    client.remove_image(image_name)


def start_docker_container(
    client: docker.APIClient,
    container_name: str,
    install_path: Path,
    image_name: str,
    no_secrets: bool = True,
    python_version: str = DEFAULT_PYTHON,
    fresh: bool = False,
) -> str:
    """
    Start a new docker container (optionally with the user's .ssh directory
    mounted) and mount `install_path` to `/root/deps`. Returns the started
    container's id

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_name (:obj:`str`): The name of the container to start
        install_path (:obj:`pathlib.Path`): The path to mount to the container's
            dependency directory
        image_name: (:obj:`str`): The name of the image the container is being built
            from.
        no_secrets (:obj:`bool`): Download all python packages locally instead of
            binding your .ssh directory and .pip file to the docker container.
            If any or your packages depend on private dependencies, you will need
            to manually specify them in the order they need to be deleted.
        python_version (:obj:`str`): The version of Python to build for
            (<major>.<minor>), default: "3.8"
        fresh (:obj:`bool`): Delete any existing container and build a
            fresh one.

    Raises:
        :obj:`docker.errors.ImageNotFound`: If the image doesn't exist
        :obj:`docker.errors.APIError`: If any other docker error occurs
        :obj: `ValueError`: If any non-docker errors occur
    """
    container_id = None

    build_info = install_path / BUILD_INFO_FILENAME

    binds = {f"{install_path.resolve()}": {"bind": INSTALL_PATH}}
    if not no_secrets:
        binds[str(Path.home() / ".ssh")] = {"bind": HOME_PATH / ".ssh"}
        binds[str(Path.home() / ".pip")] = {"bind": HOME_PATH / ".pip"}

    for started in (True, False):
        results = client.containers(
            filters={"name": container_name, "ancestor": image_name},
            quiet=True,
            all=not started,
        )

        if len(results):
            if len(results) > 1:
                raise ValueError(f"Multiple possible containers: {', '.join(results)}")

            container_id = results[0]["Id"]
            delete = fresh

            if not delete:
                if not build_info.exists():
                    logging.info(
                        "No build info exists for container %s. Rebuilding container",
                        container_name,
                    )
                    delete = True
                else:
                    with build_info.open("r") as stream:
                        info = json.load(stream)

                    # TODO: == and compatible with are different
                    if info["version"] != BUILD_INFO_VERSION:
                        logging.info(
                            "Incompatible build info exists for container %s. "
                            "Rebuilding container",
                            container_name,
                        )
                        delete = True
                    elif info["container-id"] != container_id:
                        logging.info(
                            "Mismatched contained id for container %s. "
                            "Rebuilding container",
                            container_name,
                        )
                        delete = True
                    else:
                        creation_date = client.inspect_container(container_id)[
                            "Created"
                        ]

                        if info["creation-date"] != creation_date:
                            logging.info(
                                "Mismatched creation date for container %s. "
                                "Rebuilding container",
                                container_name,
                            )
                            delete = True

            if delete:
                if started:
                    stop_docker_container(client, container_id)
                delete_docker_container(client, container_id)
                container_id = None
                if build_info.exists():
                    build_info.unlink()
                break  # skip straight to building a new container
            elif not started:
                logging.info("Starting container: %s", container_name)
                client.start(container_id)

    if not container_id:
        logging.info("Creating Container: %s", container_name)

        try:
            container_info = client.create_container(
                image_name,
                "/bin/bash",
                detach=True,
                tty=True,
                name=container_name,
                host_config=client.create_host_config(binds=binds),
            )
            container_id = container_info["Id"]
        except ImageNotFound:
            logging.exception(
                "%s not found. Could not start container: %s",
                image_name,
                container_name,
            )
            raise

        logging.info("Container Creation Successful")
        logging.info("Starting container: %s", container_name)
        client.start(container_id)

        # Check the image before we do anything else
        _check_image_version(client, container_id, python_version)

        creation_date = client.inspect_container(container_id)["Created"]

        version_tuple = tuple(map(int, python_version.split(".", 1)))
        if version_tuple >= (3, 8):
            run_docker_command(
                client,
                container_id,
                # Note: If we don't set the python version to python2 then the epel
                # install will fail
                ["env", "PYTHON=python2", "amazon-linux-extras", "install", "epel"],
            )

        # we need yum utils for bundling dependencies, not for running the
        # lambda so we don't want to include it in the dependencies list
        yum_install(client, container_id, "yum-utils")
        packages = list_system_packages(client, container_id)

        with build_info.open("w") as stream:
            json.dump(
                {
                    "version": BUILD_INFO_VERSION,
                    "python-version": python_version,
                    "container-id": container_id,
                    "creation-date": creation_date,
                    "base-packages": packages,
                    "python-packages": {},
                    "system-packages": {},
                },
                stream,
                sort_keys=True,
                indent=4,
            )
    else:
        # Still need to check an existing container because plz may have
        # been upgraded.
        _check_image_version(client, container_id, python_version)

    return container_id


def _check_image_version(
    client: docker.APIClient, container_id: str, python_version: str
):
    try:
        image_info = json.loads(
            run_docker_command(client, container_id, ["cat", DOCKER_IMAGE_INFO])
        )
    except Exception as exception:
        logging.exception(
            "Could not check the version of the image used to build this container. "
            "You should try re-running with --rebuild-image"
        )
        raise ValueError("Incompatible image") from exception

    # TODO: == and compatible with are different
    if image_info["version"] != DOCKER_IMAGE_VERSION:
        raise ValueError(
            "Mismatched image versions. "
            f"Expected: {DOCKER_IMAGE_VERSION}. "
            f"Found: {image_info['version']}. "
            "Try rebuilding the image."
        )

    if image_info["python"] != python_version:
        raise ValueError(
            "Mismatched python versions. "
            f"Expected: {python_version}. "
            f"Found: {image_info['python']}. "
            "Try rebuilding the image."
        )


def stop_docker_container(client: docker.APIClient, container_id: str):
    """
    Stop the docker container with the id `container_id`

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The id of the container to stop

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """

    logging.info("Stop Container: %s", container_id)
    try:
        client.stop(container=container_id)
    except APIError:
        logging.exception("Could not stop container: %s", container_id)
        raise
    logging.info("Container Successfully Stopped")


def delete_docker_container(client: docker.APIClient, container_id: str):
    """
    Remove the docker container with the id `container_id`

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The id of the container to delete

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """
    logging.info("Remove Container: %s", container_id)
    try:
        client.remove_container(container=container_id)
    except APIError:
        logging.error("Could not remove container: %s", container_id)
        raise
    logging.info("Container Successfully Removed")


def pip_install(
    client: docker.APIClient,
    container_id: str,
    requirement: str,
    pip_args: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> Optional[str]:
    """
    Pip install the python `dependency` in the docker container `container_id`.
    Returns the version installed.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The id of the container to use
        dependency (:obj:`str`): The python library to install
        pip_args (:obj:`Optional[dict[str, list[str]]]`): Additional pip install args.

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """
    version = None

    if pip_args is None:
        pip_args = []

    cmd = [
        "pip",
        "install",
        "--target",
        str(PYTHON_INSTALL_PATH),
        # If we're calling pip_install, it's because our current version
        # doesn't meet our requirements.
        "--upgrade",
        *pip_args,
        requirement,
    ]
    environment = [f"PYTHONPATH={PYTHON_INSTALL_PATH}"]

    logging.info("Pip Install %s: %s", requirement, cmd)
    run_docker_command(client, container_id, cmd, environment=environment)
    logging.info("Pip Install Complete")

    # pip show is often wrong if a package has been downgraded so try
    # grabbing a version interactively
    if name is not None:
        try:
            version = run_docker_command(
                client,
                container_id,
                ["python", "-c", f"import {name};print({name}.__version__)"],
                environment=environment,
            ).strip()
        except Exception:
            # package may not provide __version__. name may not be module
            # name (e.g., PyYAML vs yaml)
            pass

    if version is None:
        if name is None:
            name = requirement

        try:
            result = run_docker_command(
                client, container_id, ["pip", "show", name], environment=environment
            )

            for line in result.splitlines():
                match = re.search(r"^Version: (.*?)$", line)

                if match:
                    version = match.group(1)
                    break
        except Exception:
            logging.exception("Unable to work out installed version for %s", name)

    return version


def yum_install(
    client: docker.APIClient,
    container_id: str,
    dependency: str,
):
    """
    Yum install the system `dependency` in the docker container `container_id`.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The id of the container to use
        dependency (:obj:`str`): The system dependency to install

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """
    cmd = ["yum", "install", "-y", dependency]

    logging.info("Yum Install %s: %s", dependency, cmd)
    run_docker_command(client, container_id, cmd)
    logging.info("Yum Install Complete")


def list_system_packages(client: docker.APIClient, container_id: str) -> Dict[str, str]:
    """
    Get the set of packages currently installed in the linux container.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The id of the container to use

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
        :obj:`NotImplementedError`: If a version cannot be read
    """
    packages = {}

    logging.info("Checking which dependencies are installed")

    # yum list installed has a pre-amble (which we know is 2 lines), so
    # don't start parsing packages until we're past it.
    finished_header = False
    partial_line = None
    for line in run_docker_command(
        client,
        container_id,
        ("yum", "list", "installed"),
    ).splitlines():
        if not finished_header:
            finished_header = line == "Installed Packages"
            continue

        if partial_line:
            line = "".join((partial_line, line))
            partial_line = None

        match = re.search(
            r"^([^\s]+(?:\.[\w_]+))\s+(\w+(?:[.:_-]\w+)*)\s+(@\w+(?:-\w+)*)\s*$", line
        )

        if match:
            package, version, _ = match.groups()  # we don't care about source
            name = package.rsplit(".", 1)[0]
            packages[name] = version
        else:
            # package names can sometimes be too long to fit everything
            # in one line of output. We have only seen splits between columns
            if re.search(r"^[^\s]+(?:\.[\w_]+)+(?:\s+\w+(?:[.:_-]\w+)*)?\s*$", line):
                partial_line = line
            else:
                raise NotImplementedError(f"Learn how to parse {line!r}")
    return packages


def copy_system_packages(
    client: docker.APIClient,
    container_id: str,
    base_packages: Dict[str, str],
    installed_packages: Dict[str, Dict[str, Union[None, str, List[str]]]],
    *,
    desired_files: Optional[Dict[str, Union[str, List[str]]]] = None,
    desired_filetypes: Optional[Iterable[str]] = None,
    include: Optional[Iterable[str]] = None,
    skip: Optional[Iterable[str]] = None,
) -> List[str]:
    """
    Copy system dependency files.

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The id of the container to use
        base_packages (:obj:`Dict[str, str]`): The packages that came
            preinstalled on the image.
        installed_packages
            (:obj:`Dict[str, Dict[str, Union[None, str, List[str]]]]`):
                The packages that are already installed.
        desired_files (:obj:`Optional[Dict[str, Union[str, List[str]]]]`): A dict
            of files to copy sorted by package.
        desired_filetypes (:obj:`Optional[Iterable[str]]`): Any filetypes to copy over
        include (:obj:`Optional[Iterable[str]]`): Any packages to include even if
            they appear in the base packages list.
        skip (:obj:`Optional[Iterable[str]]`): Any packages to skip copying

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
    """
    all_copied_files = []

    if desired_files is None:
        desired_files = {}
    else:
        # we want to copy this so we can modify it
        desired_files = dict(desired_files)

    if desired_filetypes:
        desired_filetypes = set(desired_filetypes)
    else:
        desired_filetypes = set()

    if include:
        include = set(include)
    else:
        include = set()

    if skip:
        skip = set(skip)

        for name in skip:
            if name in installed_packages:
                logging.info("Removing %s", name)
                _delete_system_files(
                    client,
                    container_id,
                    cast(List[str], installed_packages.pop(name).get("files", [])),
                )
    else:
        skip = set()

    packages = list_system_packages(client, container_id)

    for name, version in packages.items():
        if name in skip:
            continue

        if name in installed_packages:
            if version == installed_packages[name]["version"]:
                continue
            else:
                # Important we remove from the list before deleting. If any
                # of these commands fail, we'd rather have files that aren't
                # in the installed list than think we have files that are
                # actually missing
                logging.info("Updated %s (%s), deleting old versions", name, version)
                _delete_system_files(
                    client,
                    container_id,
                    cast(List[str], installed_packages[name].pop("files", [])),
                )
        elif name not in base_packages or name in include:
            logging.info("Installed %s (%s)", name, version)
        elif version != base_packages[name]:
            logging.info("Updated %s (%s)", name, version)
        else:  # matches the version installed in base
            continue

        copied_files = []

        file_list = desired_files.pop(name, [])

        # This seems like an easy enough 'mistake' to make that we should just
        # support it because the error if we don't support it is trying to copy
        # '/' and that is confusing to debug.
        if isinstance(file_list, str):
            file_list = [file_list]

        if not file_list:
            for filename in run_docker_command(
                client, container_id, ("repoquery", "--list", name)
            ).splitlines():
                path = PurePosixPath(filename)
                if path.suffix and (
                    path.suffix in desired_filetypes
                    # We almost-certainly want .so files and .so files will have version
                    # information afterward. This is quicker than a regex but could burn
                    # us in the future. Sorry
                    or path.suffixes[0] in desired_filetypes
                ):
                    try:
                        run_docker_command(client, container_id, ("ls", "-L", filename))
                    except RuntimeError:
                        # repoquery lies sometimes. skip non-existent files
                        continue

                    file_list.append(filename)

        for filename in file_list:
            run_docker_command(
                client,
                container_id,
                (
                    "cp",
                    "-R",
                    # If the exact file we specified is a symlink, follow it.
                    # Don't follow any symlinks we recursively copy though.
                    "-H",
                    filename,
                    str(SYSTEM_INSTALL_PATH),
                ),
            )
            copied_files.append(filename)

        if copied_files:
            installed_packages[name] = {"version": version, "files": copied_files}
            all_copied_files.extend(copied_files)

    return all_copied_files


def _delete_system_files(client: docker.APIClient, container_id: str, files: List[str]):
    for filename in files:
        try:
            run_docker_command(
                client,
                container_id,
                (
                    "rm",
                    "-rf",
                    str(SYSTEM_INSTALL_PATH / PurePosixPath(filename).name),
                ),
            )
        except Exception:
            logging.exception("Failed to delete file %s", filename)


def run_docker_command(
    client: docker.APIClient,
    container_id: str,
    cmd: Sequence[str],
    environment: Optional[Sequence[str]] = None,
) -> str:
    """
    Create and run a command on a docker container

    Args:
        client (:obj:`docker.APIClient`): Docker APIClient object
        container_id (:obj:`str`): The name of the container to use
        cmd (:obj:`Sequence[str]`): A list of strings that make up the command
        environment: (:obj:`Optional[Sequence[str]]`): An optional list of environment
            settings in the form of "FOO=bar"

    Raises:
        :obj:`docker.errors.APIError`: If any docker error occurs
        :obj:`RuntimeError`: If the cmd returns a non-zero exit code
    """
    try:
        ex = client.exec_create(container_id, cmd=cmd, environment=environment)
        result = client.exec_start(ex["Id"], stream=True)
    except APIError:
        logging.error("Could not execute docker exec command: %s", cmd)
        stop_docker_container(client, container_id)
        raise

    cmd_output = []
    for line in result:
        decoded = line.decode("UTF-8")
        logging.info("\t%s", decoded.strip())
        cmd_output.append(decoded)

    try:
        exec_info = client.exec_inspect(ex["Id"])
    except APIError:
        logging.error("Could not inspect docker exec command: %s", cmd)
        stop_docker_container(client, container_id)
        raise

    if exec_info["ExitCode"]:
        cmd_output_string = "".join(cmd_output)
        exception_string = (
            f"The following error occurred while executing {cmd}:\n{cmd_output_string}"
        )
        raise RuntimeError(exception_string)

    return "".join(cmd_output)
