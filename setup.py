from os.path import abspath, dirname, join

from setuptools import find_packages, setup

TEST_DEPS = ["coverage", "pytest", "pytest-cov"]
DOCS_DEPS = [
    "sphinx",
    "sphinx-rtd-theme",
    "sphinx-autoapi",
    "recommonmark",
    "sphinxcontrib-runcmd",
]
CHECK_DEPS = ["isort", "flake8", "flake8-quotes", "pep8-naming", "black", "mypy"]
REQUIREMENTS = ["docker"]

EXTRAS = {
    "test": TEST_DEPS,
    "docs": DOCS_DEPS,
    "check": CHECK_DEPS,
    "dev": TEST_DEPS + CHECK_DEPS + DOCS_DEPS,
}

# Read in the version
with open(join(dirname(abspath(__file__)), "VERSION")) as version_file:
    version = version_file.read().strip()

setup(
    name="plz",
    version=version,
    description="Python Lambda Zipper",
    author="bcj",
    url="https://gitlab.invenia.ca/invenia/plz",
    packages=find_packages(exclude=["tests"]),
    install_requires=REQUIREMENTS,
    tests_require=TEST_DEPS,
    extras_require=EXTRAS,
    entry_points={"console_scripts": ["plz = plz.cli:main"]},
    package_data={"plz": ["Dockerfile"]},
    zip_safe=False,
    include_package_data=True,
)
