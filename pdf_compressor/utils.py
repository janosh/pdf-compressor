import os
from os.path import abspath, dirname


ROOT = dirname(dirname(abspath(__file__)))


def sizeof_fmt(size: float, prec: int = 1) -> str:
    """Convert file size to human readable format (https://stackoverflow.com/a/1094933).

    Args:
        size (int): File size in bytes.
        prec (int): Floating point precision in returned string. Defaults to 1.

    Returns:
        str: Human-readable file size string.
    """
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(size) < 1024:
            break
        size /= 1024

    return f"{size:3.{prec}f} {unit}"


def load_dotenv(filepath: str = f"{ROOT}/.env") -> None:
    """Parse environment variables in .env into os.environ.

    Args:
        filepath (str, optional): Path to .env file. Defaults to f"{ROOT}/.env".
    """
    try:
        with open(filepath) as dotenv:
            for line in dotenv:
                key, val = line.replace("\n", "").split("=")
                os.environ[key] = val
    except FileNotFoundError:
        pass
