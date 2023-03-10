"""
Command-line interface for PLZ.
"""
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Sequence

import boto3  # type: ignore

from plz.build import DEFAULT_PYTHON, build_image, build_zip, upload_image


BUILD = {"image", "zip"}
UPLOAD = {"push"}


def parse_args(args: Optional[Sequence[str]] = None):
    """
    Parse command line arguments for PLZ

    Args:
        args (Optional[Sequence[str]]): The command-line args.
    """
    parser = ArgumentParser(description="plz - Package a python script for AWS Lambda.")
    subparsers = parser.add_subparsers(dest="command")

    image_parser = subparsers.add_parser(
        "image", help="Build an image for uploading to ECR"
    )
    zip_parser = subparsers.add_parser(
        "zip", help="Build a zip file for uploading to S3"
    )
    push_parser = subparsers.add_parser("push", help="Push a local image to ECR")

    for command, subparser in subparsers.choices.items():
        if command in BUILD:
            subparser.add_argument(
                "files",
                type=Path,
                nargs="*",
                help="Any number of files or directories to include in the package.",
            )
            subparser.add_argument(
                "-r",
                "--requirements",
                type=Path,
                action="append",
                help=(
                    "Path to a requirements file for the package. "
                    "Can be supplied multiple times."
                ),
            )
            subparser.add_argument(
                "-c",
                "--constraints",
                type=Path,
                action="append",
                help=(
                    "Path to a constraint file for the package. "
                    "Can be supplied multiple times."
                ),
            )
            subparser.add_argument(
                "--pip",
                type=str,
                action="append",
                help="An argument to pass to pip. Can be supplied multiple times.",
            )
            subparser.add_argument(
                "-s",
                "--system",
                action="append",
                default=None,
                help="A system package to install. Can be supplied multiple times.",
            )
            subparser.add_argument("-i", "--image", help="What to call the image")
            subparser.add_argument("-t", "--tag", help="What to tag the image")
            subparser.add_argument(
                "-p",
                "--python-version",
                default=DEFAULT_PYTHON,
                help="Version of Python to build with/for (<major>.<minor>)",
            )
            subparser.add_argument(
                "--rebuild", action="store_true", help="rebuild the image"
            )

        subparser.add_argument(
            "--build",
            type=Path,
            default=Path("build"),
            help="Where to put the build directory for the package.",
        )
        subparser.add_argument("--profile", help="The AWS profile to use for uploading")

        logger_group_parent = subparser.add_argument_group(
            title="logging arguments",
            description=(
                "Control what log level the log outputs (default: logger.ERROR)"
            ),
        )
        logger_group = logger_group_parent.add_mutually_exclusive_group()

        logger_group.add_argument(
            "-d",
            "--debug",
            dest="log_level",
            action="store_const",
            const=logging.DEBUG,
            default=logging.ERROR,
            help="Set log level to DEBUG",
        )
        logger_group.add_argument(
            "-v",
            "--verbose",
            dest="log_level",
            action="store_const",
            const=logging.INFO,
            default=logging.ERROR,
            help="Log at info level",
        )

    zip_parser.add_argument(
        "--zipped-prefix",
        default=None,
        type=Path,
        help="path to prepend to all files in the package when zipping",
    )
    zip_parser.add_argument("--bucket", help="The S3 bucket to upload to")
    zip_parser.add_argument("--key", help="The S3 bucket to upload to")
    zip_parser.add_argument("--unique", action="store_true", help="Use a unique S3 key")

    image_parser.add_argument("--remote", help="The name of the remote ECR repository")

    push_parser.add_argument("local", help="The name of the local image")
    push_parser.add_argument("local_tag", help="The local image tag")
    push_parser.add_argument("remote", help="The name of the ECR repository")
    push_parser.add_argument("--tag", help="How to tag the remote image")
    push_parser.add_argument(
        "--account", help="Which AWS account ID (defaults to matching profile)"
    )
    push_parser.add_argument(
        "--region", help="Which AWS region (defaults to matching profile)"
    )

    return parser.parse_args(args)


def main(args: Optional[Sequence[str]] = None):
    """
    Run PLZ from the command line.

    Args:
        args (Optional[Sequence[str]]): The command-line args.
    """
    parsed_args = parse_args(args=args)
    logging.getLogger().setLevel(parsed_args.log_level)

    if parsed_args.profile:
        session = boto3.Session(profile_name=parsed_args.profile)
    else:
        session = None

    if parsed_args.command == "image":
        image = build_image(
            parsed_args.build,
            *parsed_args.files,
            requirements=parsed_args.requirements,
            constraints=parsed_args.constraints,
            pip_args=parsed_args.pip,
            system_packages=parsed_args.system,
            image=parsed_args.image,
            tag=parsed_args.tag,
            python_version=parsed_args.python_version,
            ecr_repository=parsed_args.remote,
            session=session,
            rebuild=parsed_args.rebuild,
        )
        print(image)
    elif parsed_args.command == "zip":
        package = build_zip(
            parsed_args.build,
            *parsed_args.files,
            requirements=parsed_args.requirements,
            constraints=parsed_args.constraints,
            pip_args=parsed_args.pip,
            system_packages=parsed_args.system,
            zipped_prefix=parsed_args.zipped_prefix,
            image=parsed_args.image,
            tag=parsed_args.tag,
            python_version=parsed_args.python_version,
            bucket=parsed_args.bucket,
            key=parsed_args.key,
            session=session,
            rebuild=parsed_args.rebuild,
            unique_key=parsed_args.unique,
        )
        print(package)
    elif parsed_args.command == "push":
        uri = upload_image(
            f"{parsed_args.local}:{parsed_args.local_tag}",
            parsed_args.remote,
            parsed_args.tag,
            directory=parsed_args.build,
            session=session,
            account_id=parsed_args.account,
            region=parsed_args.region,
        )
        print(uri)
    else:
        raise NotImplementedError(parsed_args.command)


if __name__ == "__main__":
    main()
