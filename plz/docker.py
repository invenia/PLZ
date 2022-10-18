import logging
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from subprocess import CalledProcessError, check_call, check_output
from typing import List, Optional, Sequence
from uuid import uuid4


IMAGE_VERSION = "1.0.0"
PLATFORM = "linux/amd64"

HOME_DIRECTORY = PurePosixPath("/root")
REQUIREMENTS_DIRECTORY = HOME_DIRECTORY / "requirements"
CONSTRAINTS_DIRECTORY = HOME_DIRECTORY / "constraints"
WORKING_DIRECTORY = PurePosixPath("/var/task")
SECRETS_DIRECTORY = PurePosixPath("/run/secrets")
IMAGE_VERSION_FILE = HOME_DIRECTORY / "image-version"
PYTHON_VERSION_FILE = HOME_DIRECTORY / "python-version"
FREEZE_FILE = HOME_DIRECTORY / "frozen.txt"
PACKAGE_SCRIPT = HOME_DIRECTORY / "packages.py"
BASE_PYTHON = HOME_DIRECTORY / "base-python"
INSTALLED_PYTHON = HOME_DIRECTORY / "installed-python"
INSTALLED_SYSTEM = HOME_DIRECTORY / "installed-system"

# It's not completely clear whether this length represents the length
# of the repository + tag or the repository or the tag.
MAX_TAG_LENGTH = 128


def name_image(base: str = None) -> str:
    """
    Create a valid name for an image. All image names will have plz-
    prepended to them.

    base: The base text to turn into an image name. If not supplied,
        the current working directory will be used.
    """
    # valid docker image names are based on
    # https://docs.docker.com/engine/reference/commandline/tag/
    if not base:
        base = Path.cwd().name

    parts = [["plz", "-"]]
    for symbol in base:
        if (
            re.search(r"[A-Za-z0-9]", symbol)
            or (symbol == "-" and parts[-1] and parts[-1][-1] not in "._")
            or (symbol in "._" and parts[-1] and parts[-1][-1] not in "._-")
        ):
            parts[-1].append(symbol)
        elif symbol == "/" and parts[-1]:
            parts.append([])

    return "".join("/".join(["".join(part).rstrip("._-") for part in parts]))[
        :MAX_TAG_LENGTH
    ].rstrip("._/-")


def validate_image_name(name: str):
    """
    Validate that a supplied name is a valid name for a docker image.

    name: The image name to validate.
    """
    # valid docker image names are based on
    # https://docs.docker.com/engine/reference/commandline/tag/
    section = ""
    reason = None

    if not name:
        reason = "Image name can't be blank"
    elif len(name) > MAX_TAG_LENGTH:
        reason = f"length ({len(name)}) > max allowed ({MAX_TAG_LENGTH})"
    else:
        for part in name.split("/"):
            if not part:
                reason = "Empty name section"
                break

            reason = _validate_part(part)
            if reason:
                section = part
                break

    if reason:
        if section:
            section = f", section ({section})"

        raise ValueError(f"Invalid image name ({name})({section}): {reason}")


def validate_tag_name(tag: Optional[str]):
    """
    Validate that the supplied tag can be used as a docker image tag

    tag: The tag to validate
    """
    if not tag:
        return

    reason = None

    if len(tag) > MAX_TAG_LENGTH:
        reason = f"length ({len(tag)}) > max allowed ({MAX_TAG_LENGTH})"
    else:
        reason = _validate_part(tag)

    if reason:
        raise ValueError(f"Invalid tag ({tag}): {reason}")


def _validate_part(part: str) -> Optional[str]:
    reason = None

    if not re.search(r"^[A-Za-z0-9]+(?:(?:-+|[._])[A-Za-z0-9]+)*$", part):
        reason = (
            "Only upper-/lower-case ASCII, digits, hypens (-), underscores (_), "
            "and periods (.) are allowed. "
            "non-alphanumeric characters must be surrounded by alphanumeric characters."
        )

    return reason


def verify_running():
    """
    Error if docker doesn't appear to be running
    """
    try:
        subprocess.run(
            ("docker", "ps", "--quiet"),
            capture_output=True,
            check=True,
            text=True,
        )
    except CalledProcessError as exception:
        if "Is the docker daemon running?" in exception.stderr:
            message = "Docker doesn't appear to be running. Aborting"
        elif "connect: no such file or directory" in exception.stderr:
            message = (
                "Docker is not responding. "
                "It may be in the process of starting up (or shutting down). "
                "Aborting"
            )
        else:
            message = "Unexpected error when checking Docker status. Aborting"

        logging.exception(message)

        raise


def build_system_docker_file(
    path: Path,
    packages: List[str],
    python_version: str,
    platform: str = PLATFORM,
):
    """
    Build the docker file for the system image
    """
    with path.open("w") as stream:
        stream.write(
            f"FROM --platform={platform} "
            f"public.ecr.aws/lambda/python:{python_version}\n"
        )
        stream.write("RUN yum install -y git\n")
        stream.write("RUN pip install --upgrade pip\n")
        stream.write(f"ENV HOME {HOME_DIRECTORY}")
        stream.write(f"RUN yum list installed > {HOME_DIRECTORY / 'base-system'}\n")
        stream.write(
            "RUN echo '"
            "from pathlib import Path;"
            "from site import getsitepackages;"
            'print("\\n".join('
            "str(path) for path in Path(getsitepackages()[0]).iterdir() "
            'if path.suffix != ".dist-info"'
            "))"
            f"' > {PACKAGE_SCRIPT}\n"
        )
        stream.write(f"RUN python {PACKAGE_SCRIPT} > {BASE_PYTHON}\n")
        stream.write(f"RUN echo {IMAGE_VERSION} > {IMAGE_VERSION_FILE}\n")
        stream.write(f"RUN echo {python_version} > {PYTHON_VERSION_FILE}\n")

        for package in packages:
            stream.write(f"RUN yum install -y {package}\n")

        stream.write(f"RUN yum list installed > {INSTALLED_SYSTEM}\n")

        # write these in case system and python images are the same
        stream.write(f"RUN python {PACKAGE_SCRIPT} > {INSTALLED_PYTHON}\n")
        stream.write(f"RUN pip freeze > {FREEZE_FILE}\n")


def build_python_docker_file(
    path: Path,
    base_image: str,
    requirements: Sequence[Path],
    constraints: Sequence[Path],
    pip_args: List[str],
    pipconf: Optional[Path] = None,
):
    """
    Build the docker file for the python image
    """
    with path.open("w") as stream:
        stream.write(f"FROM {base_image}\n")

        pip_args = list(pip_args)

        stream.write(f"RUN mkdir {REQUIREMENTS_DIRECTORY}\n")
        for index, requirement in enumerate(requirements):
            destination = REQUIREMENTS_DIRECTORY / f"{index}.txt"
            stream.write(f"COPY {requirement} {destination}\n")
            pip_args.extend(("--requirement", str(destination)))

        stream.write(f"RUN mkdir {CONSTRAINTS_DIRECTORY}\n")
        for index, constraint in enumerate(constraints):
            destination = CONSTRAINTS_DIRECTORY / f"{index}.txt"
            stream.write(f"COPY {constraint} {destination}\n")
            pip_args.extend(("--constraint", str(destination)))

        stream.write(
            f"RUN mkdir -p -m u=rw,go= {HOME_DIRECTORY / '.ssh'} "
            "&& ssh-keyscan gitlab.invenia.ca "
            f">> {HOME_DIRECTORY / '.ssh' / 'known_hosts'}\n"
        )

        stream.write("RUN --mount=type=ssh ")
        if pipconf:
            stream.write(
                "--mount=type=secret,id=pipconf "
                f"export PIP_CONFIG_FILE={SECRETS_DIRECTORY / 'pipconf'} "
                "&& "
            )
        stream.write(f"pip install {' '.join(pip_args)}\n")
        stream.write(f"RUN python {PACKAGE_SCRIPT} > {INSTALLED_PYTHON}\n")
        stream.write(f"RUN pip freeze > {FREEZE_FILE}\n")


def build_lambda_docker_file(
    path: Path,
    base_image: str,
    files: Sequence[Path],
):
    """
    Build the docker file for the main image
    """
    with path.open("w") as stream:
        stream.write(f"FROM {base_image}\n")

        stream.write(f"RUN mkdir -p {WORKING_DIRECTORY}\n")
        for file in files:
            stream.write(f"COPY {file} {WORKING_DIRECTORY}\n")


def get_image(image: str) -> Optional[str]:
    """
    Get the image's id (if it exists)
    """
    id = None

    output = check_output(("docker", "images", image, "--quiet"), text=True).strip()

    if output:
        ids = output.split()

        if len(ids) > 1:
            raise ValueError(f"Ambigious image ({image}), could mean: {', '.join(ids)}")

        id = ids[0]

    return id


def build_image(
    name: str,
    dockerfile: Path,
    location: Optional[Path] = None,
    pipconf: Optional[Path] = None,
    ssh: bool = False,
    platform: str = PLATFORM,
) -> str:
    """
    Create a docker image from a docker file

    directory: Where to build the image
    name: The name of the image
    dockerfile: The build file to use
    location: Where to build the image
    pipconf: The path to the pip config file
    ssh: Include ssh keys
    """
    build_command = [
        "docker",
        "build",
        "--platform",
        platform,
        "--tag",
        name,
        "--file",
        str(dockerfile),
        str(location or Path.cwd()),
    ]

    if pipconf:
        build_command.extend(("--secret", f"id=pipconf,src={pipconf}"))

    if ssh and os.environ.get("SSH_AUTH_SOCK"):
        build_command.extend(("--ssh", "default"))

    check_call(build_command)

    image_id = get_image(name)

    if not image_id:
        raise ValueError("Couldn't find built image")

    return image_id


def delete_image(image: str, force: bool = False) -> bool:
    """
    Delete an image (if it exists).

    Returns true if an image was deleted
    """
    image_id = get_image(image)

    if image_id:
        for container_id in get_containers(image=image_id):
            delete_container(container_id, force=force)

        remove_command = ["docker", "rmi", image]

        if force:
            remove_command.append("--force")

        check_call(remove_command)
        return True

    return False


def tag(existing: str, new: str):
    check_call(("docker", "tag", existing, new))


def push(
    directory: Path,
    image: str,
    repository: str,
    *,
    account: str,
    tag: Optional[str] = None,
    profile: Optional[str] = None,
    region: Optional[str] = None,
) -> str:
    """
    Push an image to a remote repository
    """
    if tag is None:
        tag = uuid4().hex

    repository = f"{account}.dkr.ecr.{region}.amazonaws.com/{repository}"
    remote = f"{repository}:{tag}"

    script_file = directory / "push.sh"
    with script_file.open("w") as stream:
        if profile:
            stream.write(f"AWS_DEFAULT_PROFILE={profile}\n")
            stream.write("LOCAL=$1\n")
            stream.write("REPO=$2\n")
            stream.write("TAG=$3\n")

            stream.write("aws ecr get-login-password ")
            if region:
                stream.write(f"--region {region} ")
            stream.write("| docker login --username AWS --password-stdin $REPO\n")
            stream.write(f"docker tag {image} $REPO:$TAG\n")
            stream.write("docker push $REPO:$TAG\n")

    check_call(("bash", str(script_file), image, repository, tag))

    return remote


def get_containers(
    name: Optional[str] = None,
    image: Optional[str] = None,
    running: bool = False,
) -> List[str]:
    """
    Get the id of a container
    """
    containers = []

    command = ["docker", "ps", "--quiet", "--no-trunc"]

    if name:
        command.extend(("--filter", f"name={name}"))

    if image:
        command.extend(("--filter", f"ancestor={image}"))

    if not running:
        command.append("--all")

    output = check_output(command, text=True).strip()

    if output:
        containers = output.split()

    return containers


def start_container(
    image: str,
    name: str,
    directory: Optional[Path] = None,
    python_version: Optional[str] = None,
) -> str:
    container_id = None

    for running in (True, False):
        containers = get_containers(name=name, running=running)

        if len(containers) > 1:
            raise ValueError(
                f"Ambigious container name ({name}): Could be {', '.join(containers)}"
            )
        elif len(containers) == 1:
            container_id = containers[0]

            if not running:
                check_call(("docker", "start", container_id))

            break

    if container_id is None:
        container_id = check_output(
            ("docker", "create", "--name", name, image, "bash"), text=True
        ).strip()
        check_call(("docker", "start", container_id))

    if directory and python_version:
        version_file = directory / ".version"
        for kind, expected in (("image", IMAGE_VERSION), ("python", python_version)):
            try:
                check_call(
                    (
                        "docker",
                        "cp",
                        f"{container_id}:{HOME_DIRECTORY / f'{kind}-version'}",
                        str(version_file),
                    )
                )
            except Exception:
                logging.exception("Could not read %s version", kind)
                raise
            else:
                actual = version_file.read_text().strip()
                if actual != expected:
                    logging.error(
                        "Invalid %s version. Expected %r, found %r",
                        kind,
                        expected,
                        actual,
                    )
                    raise ValueError(actual)
        version_file.unlink()

    return container_id


def stop_container(id: str, time: Optional[int] = None):
    command = ["docker", "stop"]

    if time is not None:
        command.extend(("--time", str(time)))

    command.append(id)

    check_call(command)


def delete_container(id: str, force: bool = False):
    command = ["docker", "rm", id]

    if force:
        command.append("--force")

    check_call(command)


def copy_to(id: str, source: Path, destination: PurePosixPath = WORKING_DIRECTORY):
    check_call(("docker", "cp", str(source), f"{id}:{destination}"))


def copy_from(id: str, source: PurePosixPath, destination: Path):
    check_call(("docker", "cp", f"{id}:{source}", str(destination)))
