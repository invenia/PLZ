"""
Build a package
"""
import json
import logging
import subprocess
import venv
from pathlib import Path
from shutil import copy2, rmtree
from sys import version_info
from typing import Optional, Sequence, Set, Union
from zipfile import ZipFile


__all__ = ["build_package"]


def build_package(
    build: Path,
    *files: Path,
    requirements: Optional[Union[Path, Sequence[Path]]] = None,
    force: bool = False,
) -> Path:
    """
    Build a python package.

    Args:
        build (Path): The directory to build the package in.
        *files (Path): Any number of files to include. Directories will
            be copied as subdirectories.
        requirements (Optional[Union[Path, Sequence[Path]]]): If given,
            a path to or a sequence of paths to requirements files to be
            installed.
        force (bool): Build the package even if a pre-built version
            already exists.
    """
    zipfile = build / "package.zip"

    if requirements is None:
        requirements = ()
    elif isinstance(requirements, Path):
        requirements = (requirements,)

    build_info = build / "build-info.json"

    try:
        info = {
            "files": {str(file): int(file.stat().st_mtime) for file in files},
            "requirements": {
                str(file): int(file.stat().st_mtime) for file in requirements
            },
        }

        if build_info.exists():
            with build_info.open("rb") as stream:
                old_info = json.load(stream)
        else:
            old_info = {}

        if force or info != old_info:
            package = build / "package"

            build.mkdir(parents=True, exist_ok=True)
            with build_info.open("w") as stream:
                json.dump(info, stream)

            if package.exists():
                rmtree(package)

            package.mkdir()

            for path in files:
                destination = package / path.name

                if path.is_dir():
                    subprocess.run(("cp", "-Rn", f"{path}", str(destination)))
                else:
                    copy2(str(path), str(destination))

            if requirements:
                python_version = f"python{version_info.major}.{version_info.minor}"
                env = build / "package-env"

                venv.create(env, clear=True, with_pip=True)

                # Debian-based distros put packages in dist-packages,
                # on linux there may be a separate lib64 directory.
                package_directories = {
                    env / lib / python_version / packages
                    for lib in ("lib", "lib64")
                    for packages in ("site-packages", "dist-packages")
                }

                # Apparently packages can also go here on our CI system
                package_directories = package_directories.union(
                    {env / lib / python_version for lib in ("lib", "lib64")}
                )

                existing: Set[Path] = set()

                # package directories will have some additional files in
                # them (e.g, pip) that don't need to be copied into our
                # package.
                for directory in package_directories:
                    if directory.exists():
                        existing = existing.union(directory.iterdir())

                if requirements:
                    python = env / "bin" / "python"

                    if not python.exists():
                        logging.error(
                            "Python does not exist in created virtualenv?! "
                            "The following executables exist in bin: %s",
                            list((env / "bin").iterdir()),
                        )

                    process = subprocess.run(
                        (
                            str(python),
                            "-m",
                            "pip",
                            "install",
                            "-r",
                            *map(str, requirements),
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )

                    if process.stdout:
                        logging.debug(process.stdout)

                    if process.stderr:
                        logging.error(process.stderr)

                for directory in package_directories:

                    if directory.exists():
                        for path in directory.iterdir():
                            # Skip things we know we don't need to add.
                            # These are separate because black and
                            # flake8 can't agree on where an or should
                            # go.
                            if path in package_directories or path in existing:
                                continue
                            if path.name == "__pycache__":
                                continue
                            if path.suffix == ".dist-info":
                                continue

                            destination = package / path.name

                            if not destination.exists():
                                logging.debug(f"Copying {destination}")
                                if path.is_dir():
                                    subprocess.run(
                                        ("cp", "-Rn", f"{path}", str(destination))
                                    )
                                else:
                                    copy2(str(path), str(destination))

            with ZipFile(zipfile, "w") as z:
                directories = [package]

                while directories:
                    directory = directories.pop()

                    for path in directory.iterdir():
                        if path.is_dir():
                            if path.name != "__pycache__":
                                directories.append(path)
                        else:
                            destination = path.relative_to(package)
                            logging.debug(f"Archiving {path} to {destination}")
                            z.write(path, destination)
    except BaseException:
        # If _anything_ goes wrong, we need to rebuild, so kill
        # build_info.
        if build_info.exists():
            build_info.unlink()

        raise

    return zipfile
