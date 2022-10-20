"""
Build a package
"""
import json
import logging
from hashlib import sha256
from pathlib import Path, PurePosixPath
from shutil import rmtree
from typing import Dict, List, Optional, Sequence, Set, Union
from uuid import uuid4
from zipfile import ZipFile

import boto3  # type: ignore
from botocore.exceptions import ClientError  # type: ignore

from plz import docker


__all__ = ["build_image", "build_zip", "upload_image"]


PACKAGE_INFO_FILENAME = "package-info.json"
ZIP_INFO_FILENAME = "zip-info.json"
FROZEN_FILE = "frozen-requirements.txt"
PACKAGE_INFO_VERSION = "0.2.0"

# lambdas are guaranteed to have boto3/botocore
IGNORE = {"__pycache__", "awscli", "boto3", "botocore"}
IGNORE_FILETYPES = {".dist-info", ".egg-info", ".pyc"}

# Current as of 2022-10-04
DEFAULT_PYTHON = "3.9"
MIN_PYTHON_VERSION = "3.7"
MAX_PYTHON_VERSION = "3.9"


def build_zip(
    directory: Path,
    *files: Path,
    # dependency options
    requirements: Union[Sequence[Path], Path, None] = None,
    constraints: Union[Sequence[Path], Path, None] = None,
    pip_args: Optional[List[str]] = None,
    system_packages: Optional[List[str]] = None,
    # bundle options
    ignore: Set[str] = IGNORE,
    ignore_filetypes: Set[str] = IGNORE_FILETYPES,
    filepath: Optional[Path] = None,
    zipped_prefix: Optional[Path] = None,
    # environment options
    image: Optional[str] = None,
    tag: Optional[str] = None,
    platform: Optional[str] = None,
    python_version: str = DEFAULT_PYTHON,
    location: Optional[Path] = None,
    # upload options
    bucket: Optional[str] = None,
    key: Optional[str] = None,
    session: Optional[boto3.Session] = None,
    # flags
    rebuild: bool = False,
    freeze: Union[bool, Path] = False,
    unique_key: bool = False,
) -> Union[Path, str]:
    """
    Bundle python code (along with any python or system dependencies)
    into a zip file and optionally upload it to an S3 bucket.

    Returns the path to the created zip UNLESS 'bucket' is provided.
    If 'bucket' is provided, the S3 key will be returned instead.

    directory: The directory to save all build files.
    *files: Any number of files to include. Directories will be
        copied over as directories, they won't be flattened.
    requirements: Either a requirements file or a list of
        requirements files.
    constraints: Either a constraints file or a list of
        constraints files.
    pip_args: a list of args to pass pip.
    system_packages: A list of packages to install.
    ignore: A set of files and directories to skip copying into the zip.
    ignore_filetypes: A set of filetypes to skip copying into the zip.
    filepath: Where to save the zip file. If not supplied, the
        zip file will be saved in the build directory with the name
        `package.zip`
    image: What to name the docker image. Will also create additional
        layers <image>-system and <image>-python. If unsupplied, the
        name will be based on the current working directory's name.
    tag: what to tag the docker image.
    python_version: The version of python to use when building packages.
    bucket: An S3 bucket to upload the zip to.
    key: An S3 key to upload the zip to.
    session: The boto3 session to use when performing S3 operations
    rebuild: Fully rebuild the image
    freeze: Generate a freeze file for the package. Can be either
        `True`, to freeze to the file `frozen-requirements.txt` in the
        build directory, or a path to freeze to.
    unique_key: When uploading the package to an s3 bucket, include
        a hash of the contents.
    """

    if filepath is None:
        filepath = directory / "package.zip"

    zip_location: Union[Path, str] = filepath

    zip_info = directory / ZIP_INFO_FILENAME

    rezip = rebuild

    if zip_info.exists():
        with zip_info.open("r") as stream:
            info = json.load(stream)

        if info.get("version") != PACKAGE_INFO_VERSION:
            logging.info(
                "Zip info has incompatible version (expected %s, found %s).",
                PACKAGE_INFO_VERSION,
                info.get("version"),
            )
            info = {"version": PACKAGE_INFO_VERSION}
            rezip = True
    else:
        info = {"version": PACKAGE_INFO_VERSION}

    try:
        package_files = []

        file_hashes = get_file_hashes(files)

        package_directory = directory / "python"
        if not requirements:
            if package_directory.exists():
                rmtree(package_directory)
                rezip = True

            info["image_id"] = None

            directory.mkdir(parents=True, exist_ok=True)
        else:
            image = build_image(
                directory,
                requirements=requirements,
                constraints=constraints,
                pip_args=pip_args,
                system_packages=system_packages,
                image=image,
                tag=tag,
                location=location,
                platform=platform,
                python_version=python_version,
                rebuild=rebuild,
                freeze=freeze,
            )

            image_id = docker.get_image(image)

            if (
                rebuild
                or info.get("image_id") != image_id
                or not package_directory.exists()
            ):
                rezip = True

                info["image_id"] = image_id

                if package_directory.exists():
                    rmtree(package_directory)

                package_directory.mkdir()

                container_id = info.get("container")
                container = f"{image.split(':', 1)[0]}-container"
                containers = docker.get_containers(name=container)

                if container_id is None or container_id not in containers:
                    try:
                        container_id = docker.start_container(
                            image,
                            container,
                            directory=directory,
                            python_version=python_version,
                        )
                    except Exception:
                        if container_id:  # incompatible container. make a new one
                            docker.delete_container(container_id)

                            container_id = docker.start_container(
                                image,
                                container,
                                directory=directory,
                                python_version=python_version,
                            )
                        else:
                            raise

                base_python = directory / "base"
                installed_python = directory / "installed"
                docker.copy_from(container_id, docker.BASE_PYTHON, base_python)
                docker.copy_from(
                    container_id, docker.INSTALLED_PYTHON, installed_python
                )

                base_locations = set(base_python.read_text().split())
                installed_locations = set(installed_python.read_text().split())

                for name in installed_locations - base_locations:
                    docker.copy_from(
                        container_id, PurePosixPath(name), package_directory
                    )

                docker.stop_container(container_id)

            package_files = list(package_directory.iterdir())

        if (
            rezip
            or info.get("files", {}) != file_hashes
            or info.get("prefix") != (str(zipped_prefix) if zipped_prefix else None)
            or not filepath.exists()
            or info.get("zip") != get_file_hash(filepath)
        ):
            info["files"] = file_hashes
            info["prefix"] = str(zipped_prefix) if zipped_prefix else None

            zip_package(
                filepath,
                *files,
                *package_files,
                zipped_prefix=zipped_prefix,
                ignore=ignore,
                ignore_filetypes=ignore_filetypes,
            )
            info["zip"] = get_file_hash(filepath)
    except Exception:
        logging.exception("Error while building zip")

        # don't let people use an invalid zip
        if filepath.exists():
            filepath.unlink()

        info = {}

        raise
    finally:
        try:
            with zip_info.open("w") as stream:
                json.dump(info, stream, sort_keys=True, indent=4)
        except Exception:
            if zip_info.exists():
                zip_info.unlink()
            raise

    if bucket:
        if key is None:
            key = filepath.name

        if unique_key:
            path = Path(key)
            key = f"{path.stem}-{info['zip']}{path.suffix}"

        if not session:
            session = boto3.Session()

        s3 = session.client("s3")

        # see if code bucket exists and generate it if necessary
        try:
            # we're going to assume that if we can read to the bucket we can
            # write to it
            s3.list_objects_v2(Bucket=bucket, MaxKeys=0)
        except s3.exceptions.NoSuchBucket:
            # the bucket probably doesn't exist (bucket names have to be
            # globally unique. it's possible someone already made a bucket
            # with this name and we just don't have access)
            logging.info("creating code bucket %s", bucket)
            s3.create_bucket(Bucket=bucket)

        s3.put_object(Bucket=bucket, Key=key, Body=filepath.read_bytes())

        zip_location = key

    return zip_location


def build_image(
    directory: Path,
    *files: Path,
    # dependency options
    requirements: Union[Sequence[Path], Path, None] = None,
    constraints: Union[Sequence[Path], Path, None] = None,
    pip_args: Optional[List[str]] = None,
    system_packages: Optional[List[str]] = None,
    # environment options
    image: Optional[str] = None,
    tag: Optional[str] = None,
    platform: Optional[str] = None,
    python_version: str = DEFAULT_PYTHON,
    location: Optional[Path] = None,
    # upload options
    ecr_repository: Optional[str] = None,
    ecr_tag: Optional[str] = None,
    session: Optional[boto3.Session] = None,
    # flags
    rebuild: bool = False,
    freeze: Union[bool, Path] = False,
) -> str:
    """
    Bundle python code (along with any python or system dependencies)
    into a docker image and optionally upload it to ECR.

    Returns the name of the image (will be the remote image if
    ecr_repository is specified)

    directory: The directory to save all build files.
    *files: Any number of files to include. Directories will be
        copied over as directories, they won't be flattened.
    requirements: Either a requirements file or a list of requirements
        files.
    constraints: Either a constraints file or a list of constraints
        files.
    pip_args: a list of arguments to pass to pip.
    system_packages: A list of packages to install.
    image: What to name the docker image. Will also create additional
        layers <image>-system and <image>-python. If unsupplied, the
        name will be based on the current working directory's name.
    tag: what to tag the docker image.
    container: What to name the docker container.
    python_version: The version of python to use when building packages.
    ecr_repository: An ECR repository to upload the image to.
    ecr_tag: What to tag the remote object.
    session: The boto3 session to use when performing S3 operations
    rebuild: Fully rebuild teh image.
    freeze: Generate a freeze file for the package. Can be either
        `True`, to freeze to the file `frozen-requirements.txt` in the
        build directory, or a path to freeze to.
    """
    directory.mkdir(parents=True, exist_ok=True)

    if requirements is None:
        requirements = []
    elif isinstance(requirements, Path):
        requirements = [requirements]

    if constraints is None:
        constraints = []
    elif isinstance(constraints, Path):
        constraints = [constraints]

    if pip_args is None:
        pip_args = []

    if system_packages is None:
        system_packages = []

    if image is None:
        image = docker.name_image()

        if tag is None:
            tag = python_version
    else:
        docker.validate_image_name(image)

    if tag is None:
        system_image = f"{image}-system"
        python_image = f"{image}-python"
        lambda_image = image
    else:
        docker.validate_tag_name(tag)

        system_image = f"{image}-system:{tag}"
        python_image = f"{image}-python:{tag}"
        lambda_image = f"{image}:{tag}"

    if platform is None:
        platform = docker.PLATFORM

    if python_version > MAX_PYTHON_VERSION:
        logging.warning(
            "Python version (%s) "
            "is greater than the latest known supported verion: %s",
            python_version,
            MAX_PYTHON_VERSION,
        )
    elif python_version < MIN_PYTHON_VERSION:
        raise ValueError(
            f"Can't build for Python {python_version}: "
            f"Oldest supported Python {MIN_PYTHON_VERSION}"
        )

    if location is None:
        location = Path.cwd()

    freeze_file = None
    if freeze:
        if freeze is True:
            freeze_file = directory / FROZEN_FILE
        else:
            freeze_file = freeze

    package_info = directory / PACKAGE_INFO_FILENAME

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

    file_hashes = get_file_hashes(files)
    python_hashes = get_file_hashes(requirements)
    constraint_hashes = get_file_hashes(constraints)

    # all copied files need to be relative to docker location
    relative_files = [
        path.relative_to(location) if path.is_absolute() else path for path in files
    ]
    requirements = [
        path.relative_to(location) if path.is_absolute() else path
        for path in requirements
    ]
    constraints = [
        path.relative_to(location) if path.is_absolute() else path
        for path in constraints
    ]

    rebuild_system = (
        rebuild
        or info.get("system") != system_packages
        or info.get("platform") != platform
        or info.get("python-version") != python_version
    )
    rebuild_python = (
        rebuild_system
        or info.get("python", {}) != python_hashes
        or info.get("constraint", {}) != constraint_hashes
    )
    update_files = info.get("files", {}) != file_hashes

    docker.verify_running()

    try:
        info["platform"] = platform
        info["python-version"] = python_version
        info["system"] = system_packages
        info["python"] = python_hashes
        info["constraint"] = constraint_hashes

        system_id = docker.get_image(system_image)
        python_id = docker.get_image(python_image)
        lambda_id = docker.get_image(lambda_image)

        if rebuild_system or not system_id:
            system_docker_file = directory / "SystemDockerFile"
            docker.build_system_docker_file(
                system_docker_file, system_packages, python_version
            )

            if lambda_id:
                docker.delete_image(lambda_image)

            if python_id:
                docker.delete_image(python_image)

            if system_id:
                docker.delete_image(system_image)

            system_id = docker.build_image(
                system_image,
                system_docker_file,
                location=location,
                platform=platform,
            )
            python_id = lambda_id = None

        if rebuild_python or not python_id:
            if lambda_id:
                docker.delete_image(lambda_image)

            if python_id:
                docker.delete_image(python_image)

            lambda_id = None

            if requirements:
                home = Path.home()
                for subdirectory in (".pip", ".config/pip"):
                    pipconf = home / subdirectory / "pip.conf"
                    if pipconf.exists():
                        break
                else:
                    pipconf = None

                python_docker_file = directory / "PythonDockerFile"
                docker.build_python_docker_file(
                    python_docker_file,
                    system_image,
                    requirements=requirements,
                    constraints=constraints,
                    pip_args=pip_args,
                    pipconf=pipconf,
                )

                python_id = docker.build_image(
                    python_image,
                    python_docker_file,
                    pipconf=pipconf,
                    ssh=True,
                    location=location,
                    platform=platform,
                )
            else:
                docker.tag(system_image, python_image)
                python_id = system_id

        if update_files or not lambda_id:
            if lambda_id:
                docker.delete_image(lambda_image)

            if relative_files:
                lambda_docker_file = directory / "DockerFile"
                docker.build_lambda_docker_file(
                    lambda_docker_file,
                    python_image,
                    relative_files,
                )
                lambda_id = docker.build_image(
                    lambda_image,
                    lambda_docker_file,
                    location=location,
                    platform=platform,
                )
            else:
                docker.tag(python_image, lambda_image)
                lambda_id = python_id

        if freeze_file:
            container = f"{image}-container"
            for container_id in docker.get_containers(name=container):
                docker.delete_container(container_id)

            container_id = docker.start_container(
                lambda_image,
                container,
                directory=directory,
                python_version=python_version,
            )

            docker.copy_from(container_id, docker.FREEZE_FILE, freeze_file)

            docker.stop_container(container_id)
            docker.delete_container(container_id)
    except Exception:
        logging.exception("Error while building image")
        info = {}
        raise
    finally:
        try:
            with package_info.open("w") as stream:
                json.dump(info, stream, sort_keys=True, indent=4)
        except Exception:
            if package_info.exists():
                package_info.unlink()
            raise

    if ecr_repository:
        lambda_image = upload_image(
            lambda_image,
            ecr_repository,
            ecr_tag,
            directory=directory,
            session=session,
            create_repository=True,
        )

    return lambda_image


def upload_image(
    local: str,
    remote: str,
    tag: Optional[str],
    *,
    directory: Optional[Path] = None,
    session: Optional[boto3.Session] = None,
    account_id: Optional[str] = None,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    create_repository: bool = True,
) -> str:
    """
    Upload a docker image to ECR.

    Returns the full URI of the uploaded image.

    NOTE: This function has to shell out. Make sure the profile awscli
    needs to use is command-line accessible.

    local: The local repository (and tag)
    remote: The name of the remote repository
    tag: The tag to use when uploading
    directory: Where to save the bash script this function needs to run.
        If not supplied, the current working directory will be used.
    session: Optionally, the boto session to use. If not supplied, the
        default session will be used.
    account_id: The ID of the account whos ECR repository to upload to.
        If not supplied, the session's account will be used.
    profile: The profile name to use when shelling out.
    region: The region of the ECR repository to upload to.
    create_repository: Whether to create the repository if it doesn't
        yet exist.
    """
    if tag is None:
        tag = uuid4().hex

    if directory is None:
        directory = Path.cwd()

    if session is None:
        session = boto3.Session()

    if account_id is None:
        sts = session.client("sts")
        account_id = sts.get_caller_identity()["Account"]

    if profile is None:
        profile = session.profile_name

    if region is None:
        region = session.region_name

    if create_repository:
        ecr = session.client("ecr")
        try:
            ecr.describe_repositories(registryId=account_id, repositoryNames=[remote])
        except ClientError as exception:
            error = exception.response["Error"]
            if error["Code"] != "RepositoryNotFoundException":
                raise

            logging.info("Creating repository in account %s: %s", account_id, remote)
            ecr.create_repository(registryId=account_id, repositoryName=remote)

    repository = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{remote}"

    docker.push(
        directory,
        local,
        repository,
        tag,
        profile=profile,
        region=region,
    )

    return f"{repository}:{tag}"


def zip_package(
    zip_file_path: Path,
    *files: Path,
    zipped_prefix: Optional[Path] = None,
    ignore: Optional[Set[str]] = None,
    ignore_filetypes: Optional[Set[str]] = None,
):
    """
    Zip up files/dirs and python packages

    Args:
        zip_file_path (:obj:`pathlib.Path`): The path to the zip_file you want to make
        files (:obj:`pathlib.Path`): The files to be zipped
        zipped_prefix (:obj:`Optional[pathlib.Path]`): A path to prefix non dirs in the
            resulting zip file
        ignore (:obj:`Optional[Set[str]])`): Ignore any files with these names
        ignore_filetypes
    """
    if ignore is None:
        ignore = IGNORE
    else:
        ignore = ignore | IGNORE

    if ignore_filetypes is None:
        ignore_filetypes = IGNORE_FILETYPES
    else:
        ignore_filetypes = ignore_filetypes | IGNORE_FILETYPES

    with ZipFile(zip_file_path, "w") as z:
        remaining = []
        for file in files:
            file = file.absolute()
            remaining.append((file, file.parent))

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


def get_file_hashes(paths: Sequence[Path]) -> Dict[str, str]:
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
            hash_value = get_file_hash(path)
            if hash_value:
                hashes[str(path)] = hash_value
        elif path.is_dir():
            remaining.extend(path.iterdir())

    return hashes


def get_file_hash(path: Path) -> Optional[str]:
    with path.open("rb") as stream:
        contents = stream.read()

        if not contents:
            return None

        return sha256(contents).hexdigest()
