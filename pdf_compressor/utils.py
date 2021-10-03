import os
import sys
from os.path import (
    abspath,
    dirname,
    expanduser,
    getsize,
    isfile,
    split,
    splitext,
)
from typing import List, TypedDict
from zipfile import ZipFile


ROOT = dirname(dirname(abspath(__file__)))


class ProcessResponse(TypedDict):
    download_filename: str
    filesize: int
    output_filesize: int
    output_filenumber: int
    output_extensions: List[str]
    timer: str
    status: str


def sizeof_fmt(size: float, prec: int = 1) -> str:
    """Convert file size to human readable format (https://stackoverflow.com/a/1094933).

    Args:
        size (int): File size in bytes.
        prec (int): Floating point precision in returned string. Defaults to 1.

    Returns:
        str: File size in human-readable format.
    """
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(size) < 1024:
            break
        size /= 1024

    return f"{size:3.{prec}f} {unit}"


def load_dotenv(filepath: str = f"{ROOT}/.env") -> None:
    """Parse environment variables in .env into os.environ.

    Args:
        filepath (str, optional): Path to .env file. Defaults to './.env'.
    """
    if not isfile(filepath) or getsize(filepath) == 0:
        return

    with open(filepath) as dotenv:
        for line in dotenv:
            if line.startswith("#"):
                continue

            key, val = line.replace("\n", "").split("=")
            os.environ[key] = val


def del_or_keep_compressed(
    pdfs: List[str], downloaded_file: str, inplace: bool, suffix: str
) -> None:
    """Check whether compressed PDFs are smaller than original. If so, relocate each
    compressed file to same directory as the original either with file suffix inserted
    or overwriting the original if inplace=True.

    Args:
        pdfs (list[str]): File paths to PDFs uploaded to iLovePDF.
        downloaded_file (str): Path to file downloaded from iLovePDF servers.
        inplace (bool): Whether to overwrite original PDFs with compressed ones if
            smaller.
        suffix (str): String to insert after filename and before extension of compressed
            PDFs. Used only if inplace=False.
    """

    if len(pdfs) == 1:
        compressed_files = [downloaded_file]
    else:
        archive = ZipFile(downloaded_file)
        compressed_files = sorted(archive.namelist())
        archive.extractall()

    trash_path = f"{expanduser('~')}/.Trash"

    for idx, (orig_file_path, compr_file_path) in enumerate(
        zip(pdfs, compressed_files), 1
    ):

        orig_size = getsize(orig_file_path)
        compressed_size = getsize(compr_file_path)

        diff = orig_size - compressed_size
        if diff > 0:
            print(
                f"{idx}/{len(pdfs)} Compressed PDF '{orig_file_path}' is "
                f"{sizeof_fmt(diff)} ({diff:.1%}) smaller than the original "
                f"({sizeof_fmt(compressed_size)} vs {sizeof_fmt(orig_size)})."
            )

            if inplace:
                # move original PDF to trash on macOS (for later retrieval if necessary)
                # simply let os.rename() overwrite existing PDF on other platforms
                if sys.platform == "darwin":
                    print("Using compressed file. Old file moved to trash.\n")
                    orig_file_name = split(orig_file_path)[1]
                    os.rename(orig_file_path, f"{trash_path}/{orig_file_name}")
                else:
                    print("Using compressed file.\n")

                os.rename(compr_file_path, orig_file_path)

            elif suffix:
                base_name, ext = splitext(orig_file_path)
                new_path = f"{base_name}{suffix}{ext}"

                if isfile(new_path):
                    counter = 2
                    while isfile(f"{base_name}{suffix}-{counter}{ext}"):
                        counter += 1
                    new_path = f"{base_name}{suffix}-{counter}{ext}"

                os.rename(compr_file_path, new_path)

        else:
            print(
                f"{idx}/{len(pdfs)} Compressed '{orig_file_path}' no smaller than "
                "original file. Keeping original."
            )
            os.remove(compr_file_path)

    # check needed since if single PDF was processed, the file will have been moved
    if isfile(downloaded_file):
        os.remove(downloaded_file)
