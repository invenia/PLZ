"""
Build a package
"""
import json
import logging
import re
import sys
from hashlib import sha256
from pathlib import Path
from shutil import rmtree
from subprocess import check_output
from typing import Dict, List, Optional, Sequence, Set, Union
from uuid import uuid4
from zipfile import ZipFile

import docker  # type: ignore
import yaml
from pkg_resources import Requirement

from plz.plzdocker import (
    BUILD_INFO_FILENAME,
    DEFAULT_PYTHON,
    INSTALL_PATH,
    SUPPORTED_PYTHON,
    build_docker_image,
    copy_system_packages,
    delete_docker_image,
    fix_file_permissions,
    pip_freeze,
    pip_install,
    start_docker_container,
    stop_docker_container,
    yum_install,
)


__all__ = ["build_package"]


DOCKER_IMAGE_NAME = "plz-builder-{version}"
PACKAGE_INFO_FILENAME = "package-info.json"
PACKAGE_INFO_VERSION = "0.1.0"
SYSTEM_REQUIREMENTS_VERSION = "0.1.0"

# Arguments used for both download and install
CROSS_ARGUMENTS = {
    "--no-deps": 0,
    "--no-binary": 1,
    "--only-binary": 1,
    "--prefer-binary": 0,
    "--pre": 0,
    "--require-hashes": 0,
    "--no-build-isolation": 0,
    "--use-pep517": 0,
    "--no-use-pep517": 0,
    "--platform": 1,
    "--implementation": 1,
    "--abi": 1,
}

# lambdas are guaranteed to have boto3/botocore
IGNORE = {"__pycache__", "boto3", "botocore"}
IGNORE_FILETYPES = {".dist-info", ".egg-info", ".pyc"}


def build_package(
    build: Path,
    *files: Path,
    requirements: Optional[Union[Sequence[Path], Path]] = None,
    system_requirements: Optional[Path] = None,
    python_version: str = DEFAULT_PYTHON,
    reinstall_python: bool = False,
    reinstall_system: bool = False,
    freeze_dependencies: bool = False,
    zipped_prefix: Optional[Path] = None,
    pip_args: Optional[Dict[str, List[str]]] = None,
    image_name: Optional[str] = None,
    container_name: Optional[str] = None,
    rebuild_image: bool = False,
    rezip: bool = False,
    no_secrets: bool = False,
    force: bool = False,
    freeze: bool = False,
    ignore: Set[str] = IGNORE,
    ignore_filetypes: Set[str] = IGNORE_FILETYPES,
) -> Path:
    """
    Build a python package.

    Args:
        build (:obj:`pathlib.Path`): The directory to build the package in.
        *files (:obj:`pathlib.Path`): Any number of files to include. Directories will
            be copied as subdirectories.
        requirements (:obj:`Optional[Union[Sequence[pathlib.Path], pathlib.Path]]`):
            If given, a path to or a sequence of paths to requirements files to be
            installed.
        system_requirements (:obj:`Optional[pathlib.Path]`): If given, a path to a yum
            requirements yaml file to be installed
        python_version (:obj:`str`): The version of Python to build for
            (<major>.<minor>), default: "3.8"
        reinstall_python (:obj:`bool`): Reinstall python requirements.
        reinstall_system (:obj:`bool`): Reinstall system requirements.
        zipped_prefix (:obj:`Optional[pathlib.Path`]): If given, a path to prepend to
            all files in the package when zipping
        pip_args (:obj:`Optional[dict[str, list[str]]]`): A dict that maps the python
            dependency names to a list of pip-install args.
        image_name (:obj:`str`): What to name the docker image for building the package
        rebuild_image (:obj:`bool`): Delete any existing images with the same name
        rezip (:obj:`bool`): Recreate the zip file even if nothing has changed.
        no_secrets (:obj:`bool`): Download all python packages locally instead of
            binding your .ssh directory and .pip file to the docker container.
            If any or your packages depend on private dependencies, you will need
            to manually specify them in the order they need to be deleted.
        force (:obj:`bool`): Force a complete rebuild
        freeze (:obj:`bool`): Create a pip freeze file.
        ignore (:obj:`Set[str]`): Files to ignore when packaging the zip.
        ignore_filetypes (:obj:`Set[str]`): Filetypes to ignore when packaging the zip.

    TODO: any additional work required to make the resulting container directly
    usable by a lambda.

    NOTE: plz doesn't handle removing existing dependencies that were removed
    from a requirements list. You should set reinstall_python or
    reinstall_system to true if you have python or system dependencies that
    should be removed

    Raises:
        :obj:`BaseException`: If anything goes wrong
    """
    if image_name is None:
        image_name = DOCKER_IMAGE_NAME.format(version=python_version)

    if container_name is None:
        f"{image_name}-container"

    if python_version not in SUPPORTED_PYTHON:
        logging.warning(
            "Python version (%s) does not appear to be supported by AWS Lambda. "
            "Currently supported versions: %s",
            python_version,
            ", ".join(sorted(SUPPORTED_PYTHON)),
        )

    if force:
        reinstall_python = reinstall_system = rebuild_image = rezip = True
    elif reinstall_system:
        rebuild_image = True

    build.mkdir(parents=True, exist_ok=True)

    zipfile = build / "package.zip"
    package_info = build / PACKAGE_INFO_FILENAME

    # Make sure requirements is a list
    if requirements is None:
        requirements = []
    elif isinstance(requirements, Path):
        requirements = [requirements]

    if package_info.exists():
        with package_info.open("r") as stream:
            info = json.load(stream)

        if info.get("version") != PACKAGE_INFO_VERSION:
            logging.info(
                "Package info has incompatible version (expected %s, found %s).",
                PACKAGE_INFO_VERSION,
                info.get("version"),
            )
            info = {"version": PACKAGE_INFO_VERSION}
    else:
        info = {"version": PACKAGE_INFO_VERSION}

    if container_name is None:
        # Our image name is generic and may appear across multiple projects.
        # If a container name isn't specified we should make sure it's unique
        container_name = info.setdefault("container", f"{image_name}-{uuid4()}")
    else:
        info["container"] = container_name

    python_hashes = _get_file_hashes(*requirements)
    system_hashes = {}
    if system_requirements:
        system_hashes = _get_file_hashes(system_requirements)

    try:
        if (
            rebuild_image
            or reinstall_python
            or reinstall_system
            or info.get("python", {}) != python_hashes
            or info.get("system", {}) != system_hashes
        ):
            process_requirements(
                requirements,
                system_requirements,
                build,
                python_version=python_version,
                pip_args=pip_args,
                image_name=image_name,
                container_name=container_name,
                no_secrets=no_secrets,
                rebuild_image=rebuild_image,
                reinstall_python=reinstall_python,
                reinstall_system=reinstall_system,
                freeze=freeze,
            )
            rezip = True

        file_hashes = _get_file_hashes(*files)
        if (
            rezip
            or info.get("prefix") != zipped_prefix
            or info.get("files", {}) != file_hashes
            or not zipfile.exists()
            or info.get("zip") != _get_file_hash(zipfile)
        ):
            all_files: List[Path] = []

            for component, hashes in (
                ("system", system_hashes),
                ("python", python_hashes),
            ):
                directory = build / component
                if hashes and directory.exists():
                    all_files.append(directory)
                    info[component] = hashes

            all_files.extend(files)
            info["files"] = file_hashes

            zip_package(
                zipfile,
                *all_files,
                zipped_prefix=zipped_prefix,
                ignore=ignore,
                ignore_filetypes=ignore_filetypes,
            )
            info["prefix"] = zipped_prefix
            info["zip"] = _get_file_hash(zipfile)
    except Exception:
        logging.exception("Error while building zip file")

        # We're blanking values on error and writing the file instead of just
        # deleting the file because the image/container may be fine and we
        # don't want to just throw that out. build_info will mark the container
        # as bad if an error occurred that effects the container.
        info["system"] = {}
        info["python"] = {}
        info["files"] = {}
        info["prefix"] = None
        info["zip"] = None

        if zipfile.exists():
            zipfile.unlink()

        raise
    finally:
        with package_info.open("w") as stream:
            json.dump(info, stream, sort_keys=True, indent=4)

    return zipfile


def _get_file_hashes(*paths: Path) -> Dict[str, str]:
    """
    Return a dictionary of hashes for all files in a file tree.

    Args:
        path (:obj:`pathlib.Path`): The files/directories to get the last
            modified time from.
    """
    hashes = {}
    remaining = list(paths)
    while remaining:
        path = remaining.pop()

        if path.is_file():
            hashes[str(path)] = _get_file_hash(path)
        elif path.is_dir():
            remaining.extend(path.iterdir())

    return hashes


def _get_file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def process_requirements(
    requirements: Sequence[Path],
    system_requirements: Optional[Path],
    build: Path,
    image_name: str,
    container_name: str,
    *,
    python_version: str = DEFAULT_PYTHON,
    pip_args: Optional[Dict[str, List[str]]] = None,
    no_secrets: bool = False,
    rebuild_image: bool = False,
    reinstall_python: bool = False,
    reinstall_system: bool = False,
    freeze: bool = False,
):
    """
    Using pip, install python requirements into a docker instance

    Args:
        requirements (:obj:`Sequence[pathlib.Path]`): Paths to requirements files to
            install
        system_requirements (:obj:`Optional[pathlib.Path]`): Path to system
            requirements yaml file to install
        build (:obj:`pathlib.Path`): Path to the directory to build in.
        image_name (:obj:`str`): What to name the docker image for building the package
        container_name (:obj:`str`): What to name the docker container for building the
            package
        python_version (:obj:`str`): The version of Python to build for
            (<major>.<minor>), default: "3.8"
        pip_args (:obj:`Optional[dict[str, list[str]]]`): A dict that maps the python
            dependency names to a list of pip-install args.
        no_secrets (:obj:`bool`): Download all python packages locally instead of
            binding your .ssh directory and .pip file to the docker container.
            If any or your packages depend on private dependencies, you will need
            to manually specify them in the order they need to be deleted.
        rebuild_image (:obj:`bool`): Delete any existing images with the same name
        reinstall_python (:obj:`bool`): Reinstall python requirements.
        reinstall_system (:obj:`bool`): Reinstall system requirements.
        freeze (:obj:`bool`): Create a pip freeze file.

    Raises:
        FileNotFoundError: If no files are copied after pip installation
    """
    build.mkdir(parents=True, exist_ok=True)
    build_info = build / BUILD_INFO_FILENAME

    try:
        client = docker.APIClient()
    except docker.errors.APIError:
        logging.exception(
            "Accessing Docker failed. Ensure Docker is installed and running, and that "
            "internet access is available for the first run. "
            "Installation cannot proceed."
        )
        raise

    if rebuild_image and client.images(name=image_name):
        delete_docker_image(client, image_name)

    if not client.images(name=image_name):
        build_docker_image(client, python_version=python_version, image_name=image_name)

    container_id = start_docker_container(
        client,
        container_name,
        build,
        image_name=image_name,
        no_secrets=no_secrets,
        python_version=python_version,
        fresh=reinstall_system,
    )

    with build_info.open("r") as stream:
        info = json.load(stream)

    try:
        system_dependencies = build / "system"
        if system_dependencies.exists() and (
            reinstall_system or not system_requirements
        ):
            rmtree(system_dependencies)
            info["system-packages"] = {}

        if system_requirements:
            system_dependencies.mkdir(parents=True, exist_ok=True)
            with system_requirements.open() as stream:
                data = yaml.safe_load(stream)

                if data.get("version") != SYSTEM_REQUIREMENTS_VERSION:
                    raise ValueError(
                        "Unsupported system requirements file version. "
                        f"Expected {SYSTEM_REQUIREMENTS_VERSION}. "
                        f"Found: {data.get('version')}"
                    )

                skip = set(data.get("skip", []))
                include = set()

                for package in data["packages"]:
                    yum_install(client, container_id, package)

                    if package not in skip:
                        include.add(package)

            kwargs = {}

            if "filetypes" in data:
                kwargs["desired_filetypes"] = data["filetypes"]

            if isinstance(data["packages"], dict):
                kwargs["desired_files"] = data["packages"]

            copy_system_packages(
                client,
                container_id,
                info["base-packages"],
                installed_packages=info["system-packages"],
                skip=skip,
                include=include,
                **kwargs,
            )

        python_dependencies = build / "python"
        if python_dependencies.exists() and (reinstall_python or not requirements):
            rmtree(python_dependencies)
            info["python-packages"] = {}

        if requirements:
            python_dependencies.mkdir(parents=True, exist_ok=True)
            if no_secrets:
                download_directory = build / "pip-downloads"
                if download_directory.exists():
                    rmtree(download_directory)
                download_directory.mkdir(exist_ok=True)
                docker_download_directory = INSTALL_PATH / download_directory.name

            if pip_args is None:
                pip_args = {}

            # pip install all requirements files
            for path in requirements:
                with path.open() as stream:
                    for line in stream.readlines():
                        requirement_line = line.strip()

                        # ignore comments and blank names
                        if not requirement_line or re.search(
                            r"^\s*#", requirement_line
                        ):
                            continue

                        # Pip supports a private repository across a number of
                        # version control systems:
                        # https://pip.pypa.io/en/stable/reference/pip_install/#vcs-support
                        # This is an attempt at a maximally-permissive regex that will
                        # find the name of a package. As such, this will have some false
                        # positives (that shouldn't be a problem because no real pypi
                        # name starts vcs+protocol:). It can also break on a project
                        # named `trunk` if the protocol isn't included as an extension
                        # for the project and #egg=name isn't specified.
                        match = re.search(
                            # bzr+lp, which we'll never see, doesn't use the slashes
                            r"^(\w+)\+\w+:(?://)?"  # vcs + protocol
                            r"(?:\w+(?::[^@]+)?@)?"  # user + password
                            r"(?:[^/#@]+/)+"  # domain/path
                            r"([^/#@.]+)"  # project
                            r"(?:\.\1)?"  # name may include vcs as extension
                            r"(?:/trunk)?"  # svn may have trunk after project
                            # may have a reference (branch, tag, etc.) to check out
                            r"(?:@[^#]+)?"
                            r"(?:#egg=(.+))?$",  # actual package name may be supplied
                            requirement_line,
                        )

                        requirement = None
                        if match:
                            _, project, egg = match.groups()

                            name = egg or project
                            key = name.lower()
                        else:
                            try:
                                requirement = Requirement.parse(requirement_line)
                            except Exception:
                                logging.info(
                                    "Unable to parse requirement %s",
                                    requirement_line,
                                    exc_info=True,
                                )
                                name = requirement_line
                                key = name.lower()
                            else:
                                name = requirement.project_name
                                key = requirement.key

                                version = info["python-packages"].get(name)
                                if version and version in requirement:
                                    # We can skip reinstalling
                                    continue

                        # Allow people to specify args by the full line, the name,
                        # or the lowercased name. Search most-to-least specific
                        install_args = pip_args.get(
                            requirement_line, pip_args.get(name, pip_args.get(key, []))
                        )

                        if no_secrets:
                            cmd = [
                                "pip",
                                "download",
                                "--dest",
                                str(download_directory),
                            ]

                            for index, argument in enumerate(install_args):
                                if argument in CROSS_ARGUMENTS:
                                    end_index = index + CROSS_ARGUMENTS[argument] + 1
                                    cmd.extend(install_args[index:end_index])

                            for argset in (
                                ("--platform", "linux_x86_64"),
                                ("--abi", f"cp{python_version.replace('.', '')}"),
                                ("--python-version", python_version),
                                ("--no-deps",),
                            ):
                                if argset[0] not in cmd:
                                    cmd.extend(argset)

                            cmd.append(requirement_line)

                            logging.info("Downloading package: %s", cmd)
                            for line in check_output(cmd, text=True).splitlines():
                                match = re.search(
                                    r"^\s*(?:Saved|File was already downloaded) (.*?)$",
                                    line,
                                )
                                if match:
                                    location = Path(match.group(1))

                                    requirement_line = str(
                                        docker_download_directory / location.name
                                    )
                                    break
                            else:
                                raise ValueError(f"downloading {name} failed")

                        info["python-packages"][name] = pip_install(
                            client,
                            container_id,
                            requirement_line,
                            install_args,
                            name=name,
                        )

        if freeze:
            pip_freeze(client, container_id, build / "frozen-requirements.txt")

        # on non-mac platforms, we need to give the host system
        # permission to read container-created files in the shared
        # directory
        if sys.platform != "darwin":
            fix_file_permissions(client, container_id)

    except Exception:
        logging.exception("Error occurred while installing dependencies")
        raise
    finally:
        with build_info.open("w") as stream:
            json.dump(info, stream, sort_keys=True, indent=4)
        stop_docker_container(client, container_id)


def zip_package(
    zipfile_path: Path,
    *files: Path,
    zipped_prefix: Optional[Path] = None,
    ignore: Optional[Set[str]] = None,
    ignore_filetypes: Optional[Set[str]] = None,
):
    """
    Zip up files/dirs and python packages

    Args:
        zipfile_path (:obj:`pathlib.Path`): The path to the zipfile you want to make
        files (:obj:`pathlib.Path`): The files to be zipped
        zipped_prefix (:obj:`Optional[pathlib.Path]`): A path to prefix non dirs in the
            resulting zip file
        ignore (:obj:`Optional[Set[str]])`): Ignore any files with these names
        ignore_filetypes
    """
    if ignore is None:
        ignore = IGNORE

    if ignore_filetypes is None:
        ignore_filetypes = IGNORE_FILETYPES

    # Delete unnecessary dirs
    with ZipFile(zipfile_path, "w") as z:
        remaining = []
        for file in files:
            file = file.absolute()
            if file.is_file():
                remaining.append((file, file.parent))
            else:
                remaining.append((file, file))

        while remaining:
            path, relative_to = remaining.pop(0)

            destination = path.relative_to(relative_to)

            if (
                path.name in ignore
                or destination in ignore
                or path.suffix in ignore_filetypes
            ):
                continue

            if path.is_dir():
                remaining.extend((child, relative_to) for child in path.iterdir())
            else:
                if zipped_prefix:
                    destination = zipped_prefix / destination
                logging.debug(f"Archiving {path} to {destination}")
                z.write(path, destination)
