from pathlib import Path

from setuptools import setup, find_packages


def _requirements() -> list[str]:
    text = Path(__file__).with_name("requirements.txt").read_text(encoding="utf-8")
    reqs = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            reqs.append(line)
    return reqs


setup(
    name="homebot",
    version="0.1.0",
    description="HomeBot home robot control software",
    packages=find_packages(where="software/src"),
    package_dir={"": "software/src"},
    install_requires=_requirements(),
    include_package_data=True,
)
