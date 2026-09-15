from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-edvibe",
    version="0.3.0",
    description="CLI-Anything harness for edvibe.com (materials/lessons over the Edvibe WebSocket RPC API)",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.9",
    install_requires=["requests>=2.28", "websocket-client>=1.6", "click>=8.0"],
    entry_points={"console_scripts": ["cli-anything-edvibe=cli_anything.edvibe.edvibe_cli:cli"]},
)
