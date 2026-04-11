from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("pdf-compressor")
except PackageNotFoundError:
    __version__ = "0.0.0"  # package not installed
