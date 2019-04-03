"""
Build a package
"""
import json
import logging
from pathlib import Path
from shutil import copy2, copytree, ignore_patterns, rmtree
from typing import Optional, Sequence
from zipfile import ZipFile

import docker  # type: ignore

from .docker import (
    build_docker_image,
    pip_install,
    start_docker_container,
    stop_docker_container,
)

__all__ = ["build_package"]


def build_package(
    build: Path,
    *files: Path,
    requirements: Sequence[Path] = [],
    zipped_prefix: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Build a python package.

    Args:
        build (:obj:`pathlib.Path`): The directory to build the package in.
        *files (:obj:`pathlib.Path`): Any number of files to include. Directories will
            be copied as subdirectories.
        requirements (:obj:`Sequence[pathlib.Path]`): If given, a path to or a sequence
            of paths to requirements files to be installed.
        zipped_prefix (:obj:`Optional[pathlib.Path`]): If given, a path to prepend to
            all files in the package when zipping
        force (:obj:`bool`): Build the package even if a pre-built version already
            exists.

    Raises:
        :obj:`BaseException`: If anything goes wrong
    """
    zipfile = build / "package.zip"
    build_info = build / "build-info.json"
    package = build / "package"

    try:
        info = {
            "files": {str(file): int(file.stat().st_mtime) for file in files},
            "requirements": {
                str(file): int(file.stat().st_mtime) for file in requirements
            },
            "zipped_prefix": str(zipped_prefix),
        }

        if build_info.exists():
            with build_info.open("rb") as stream:
                old_info = json.load(stream)
        else:
            old_info = {}

        # Just return the zipfile path, if there is no new work to do on it
        if not force and info == old_info:
            return zipfile

        # Copy the included files into the package dir
        copy_included_files(build, build_info, info, package, *files)

        # Process requirements files if they exist
        if requirements:
            env = build / "package-env"
            process_requirements(requirements, package, env)

        # Zip it all up
        zip_package(zipfile, package, zipped_prefix=zipped_prefix)
    except BaseException:
        # If _anything_ goes wrong, we need to rebuild, so kill
        # build_info.
        if build_info.exists():
            build_info.unlink()
        raise

    return zipfile


def copy_included_files(
    build_path: Path, build_info: Path, info: dict, package_path: Path, *files: Path
):
    """
    Copy the files and dirs pointed to by `*files` into the build_path

    Args:
        build_path (:obj:`pathlib.Path`): The main build directory
        build_info (:obj:`pathlib.Path`): The path to the build-info.json file
        info (:obj:`dict`): The build-info
        package_path (:obj:`pathlib.Path`): The path to the resulting package directory
        *files (:obj:`pathlib.Path`): The files/dirs to copy
    """
    # Make sure the main build directory is created
    build_path.mkdir(parents=True, exist_ok=True)
    with build_info.open("w") as stream:
        json.dump(info, stream)

    # Delete the package path if it exists, and recreate it
    if package_path.exists():
        rmtree(package_path)
    package_path.mkdir()

    # Ignore unnecessary files/dirs
    ignore = ignore_patterns("*.pyc", "__pycache__", "*.dist-info")

    # For each file or dir, copy it into the package_path.
    for path in files:
        destination = package_path / path.name

        if path.is_dir():
            copytree(path, destination / path.parts[-1], ignore=ignore)
        else:
            copy2(path, destination)


def process_requirements(requirements: Sequence[Path], package_path: Path, env: Path):
    """
    Using pip, install python requirements into a docker instance

    Args:
        requirements (:obj:`Sequence[pathlib.Path]`): Paths to requirements files to
            install
        package_path (:obj:`pathlib.Path`): Path to resulting package dir
        env (:obj:`pathlib.Path`): Path to docker environment to install packages to
    """
    client = docker.APIClient()
    container = "plz-container"
    build_docker_image(client, env)
    start_docker_container(client, container, env)

    # pip install all requirements files
    for r_file in requirements:
        with r_file.open() as f:
            for line in f.readlines():
                dep = line.strip()
                pip_install(client, container, dep)

    stop_docker_container(client, container)

    # Ignore unnecessary files/dirs
    ignore = ignore_patterns("*.pyc", "__pycache__", "*.dist-info")

    # Copy the python libs to the package
    for path in env.iterdir():
        destination = package_path / path.name
        if not destination.exists():
            logging.debug(f"Copying {destination}")
            if path.is_dir():
                copytree(path, destination / path.parts[-1], ignore=ignore)
            else:
                copy2(path, destination)


def zip_package(
    zipfile_path: Path, package_path: Path, zipped_prefix: Optional[Path] = None
):
    """
    Zip up files/dirs and python packages

    Args:
        zipfile_path (:obj:`pathlib.Path`): The path to the zipfile you want to make
        package_path (:obj:`pathlib.Path`): The path to the package to be zipped
        zipped_prefix (:obj:`Optional[pathlib.Path]`): A path to prefix non dirs in the
            resulting zip file
    """
    with ZipFile(zipfile_path, "w") as z:
        directories = [package_path]

        while directories:
            directory = directories.pop()

            for path in directory.iterdir():
                if path.is_dir():
                    directories.append(path)
                else:
                    destination = path.relative_to(package_path)
                    if zipped_prefix:
                        destination = zipped_prefix / destination
                    logging.debug(f"Archiving {path} to {destination}")
                    z.write(path, destination)
