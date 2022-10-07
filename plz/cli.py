"""
Command-line interface for PLZ.
"""
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Sequence

import boto3  # type: ignore

from plz.build import DEFAULT_PYTHON, build_image, build_zip


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

    for command, subparser in subparsers.choices.items():
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
        subparser.add_argument(
            "--build",
            type=Path,
            default=Path("build"),
            help="Where to put the build directory for the package.",
        )
        subparser.add_argument("-i", "--repository", help="What to call the image")
        subparser.add_argument("-t", "--tag", help="What to tag the image")
        subparser.add_argument(
            "-p",
            "--python-version",
            default=DEFAULT_PYTHON,
            help="Version of Python to build with/for (<major>.<minor>)",
        )
        subparser.add_argument("--profile", help="The AWS profile to use for uploading")
        subparser.add_argument(
            "--rebuild", action="store_true", help="rebuild the image"
        )

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
            repository=parsed_args.repository,
            tag=parsed_args.tag,
            python_version=parsed_args.python_version,
            remote_repository=parsed_args.remote,
            session=session,
            rebuild=parsed_args.rebuild,
        )
        print(image)
    else:
        package = build_zip(
            parsed_args.build,
            *parsed_args.files,
            requirements=parsed_args.requirements,
            constraints=parsed_args.constraints,
            pip_args=parsed_args.pip,
            system_packages=parsed_args.system,
            zipped_prefix=parsed_args.zipped_prefix,
            repository=parsed_args.repository,
            tag=parsed_args.tag,
            python_version=parsed_args.python_version,
            bucket=parsed_args.bucket,
            key=parsed_args.key,
            session=session,
            rebuild=parsed_args.rebuild,
            unique_key=parsed_args.unique,
        )
        print(package)


if __name__ == "__main__":
    main()
