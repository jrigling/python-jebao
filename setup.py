"""Setup configuration for python-jebao."""
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="python-jebao",
    version="0.1.0",
    author="Justin Rigling",
    author_email="jrigling@gmail.com",
    description="Python library for controlling Jebao aquarium pumps",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jrigling/python-jebao",
    packages=find_packages(exclude=["tests", "tests.*"]),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.9",
    install_requires=[
        "netifaces>=0.11.0",  # For multi-interface discovery
    ],
)
