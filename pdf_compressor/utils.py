from __future__ import annotations

import os
import shutil
import sys
from os.path import abspath, basename, dirname, expanduser, getsize, isfile, splitext
from zipfile import ZipFile

ROOT = dirname(dirname(abspath(__file__)))


def si_fmt(
    val: int | float, binary: bool = True, fmt_spec: str = ".1f", sep: str = ""
) -> str:
    """Convert large numbers into human readable format using SI prefixes in binary
    (1024) or metric (1000) mode.

    https://nist.gov/pml/weights-and-measures/metric-si-prefixes

    Args:
        val (int | float): Some numerical value to format.
        binary (bool, optional): If True, scaling factor is 2^10 = 1024 else 1000.
            Defaults to True.
        fmt_spec (str): f-string format specifier. Configure precision and left/right
            padding in returned string. Defaults to ".1f". Can be used to ensure leading
            or trailing whitespace for shorter numbers. Ex.1: ">10.2f" has 2 decimal
            places and is at least 10 characters long with leading spaces if necessary.
            Ex.2: "<20.3g" uses 3 significant digits (g: scientific notation on large
            numbers) with at least 20 chars through trailing space.
        sep (str): Separator between number and postfix. Defaults to "".

    Returns:
        str: Formatted number.
    """
    factor = 1024 if binary else 1000

    if abs(val) >= 1:
        # 1, Kilo, Mega, Giga, Tera, Peta, Exa, Zetta, Yotta
        for _scale in ("", "K", "M", "G", "T", "P", "E", "Z", "Y"):
            if abs(val) < factor:
                break
            val /= factor
    else:
        mu_unicode = "\u03BC"
        # milli, micro, nano, pico, femto, atto, zepto, yocto
        for _scale in ("", "m", mu_unicode, "n", "p", "f", "a", "z", "y"):
            if abs(val) > 1:
                break
            val *= factor

    return f"{val:{fmt_spec}}{sep}{_scale}"


def load_dotenv(filepath: str = None) -> None:
    """Parse environment variables in .env into os.environ.

    Args:
        filepath (str, optional): Path to .env file. Defaults to './.env'.
    """
    if filepath is None:
        filepath = os.path.join(f"{ROOT}", ".env")

    if not isfile(filepath) or getsize(filepath) == 0:
        return

    with open(filepath, encoding="utf8") as dotenv:
        for line in dotenv:
            if line.startswith("#"):
                continue

            key, val = line.replace("\n", "").split("=")
            os.environ[key] = val


def del_or_keep_compressed(
    pdfs: list[str],
    downloaded_file: str,
    inplace: bool,
    suffix: str,
    min_size_reduction: int,
    verbose: bool,
) -> None:
    """Check whether compressed PDFs are smaller than original. If so, relocate each
    compressed file to same directory as the original either with suffix appended to
    file name or overwriting the original if inplace=True.

    Args:
        pdfs (list[str]): File paths to PDFs uploaded to iLovePDF.
        downloaded_file (str): Path to file downloaded from iLovePDF servers. Will be
            PDF or ZIP depending on if single or multiple files were uploaded.
        inplace (bool): Whether to overwrite original PDFs with compressed ones if
            smaller.
        suffix (str): String to insert after filename and before extension of compressed
            PDFs. Used only if inplace=False.
        min_size_reduction (int): How much compressed files need to be smaller than
            originals (in percent) for them to be kept.
        verbose (bool): Whether to print file names or full file paths.
    """
    if (n_files := len(pdfs)) == 1:
        compressed_files = [downloaded_file]
    else:
        with ZipFile(downloaded_file) as archive:
            # sort compressed_files since pdfs were also sorted
            compressed_files = sorted(archive.namelist())
            archive.extractall()

    trash_path = f"{expanduser('~')}/.Trash"  # macOS only, no need for os.path.join()

    for idx, (orig_path, compressed_path) in enumerate(zip(pdfs, compressed_files), 1):

        orig_size = getsize(orig_path)
        compressed_size = getsize(compressed_path)

        diff = orig_size - compressed_size
        counter = f"\n{idx}: " if n_files > 1 else ""

        if diff / orig_size > min_size_reduction / 100:
            filepath = orig_path if verbose else basename(orig_path)
            print(
                f"{counter}'{filepath}' is now {si_fmt(compressed_size)}B, was "
                f"{si_fmt(orig_size)}B which is {si_fmt(diff)}B = {diff/orig_size:.0%} "
                "smaller."
            )

            if inplace:
                # move original PDF to trash on macOS (for later retrieval if necessary)
                # simply let os.rename() overwrite existing PDF on other platforms
                if sys.platform == "darwin":
                    print("Old file moved to trash.")
                    orig_file_name = os.path.split(orig_path)[1]

                    os.rename(orig_path, f"{trash_path}/{orig_file_name}")
                else:
                    print("Old file deleted.")

                # don't use os.(rename|replace)() on Windows, both error if src and
                # dest are on different drives, former also if destination file already
                # exists
                shutil.move(compressed_path, orig_path)

            elif suffix:
                base_name, ext = splitext(orig_path)
                new_path = f"{base_name}{suffix}{ext}"

                shutil.move(compressed_path, new_path)

        else:
            not_enough_reduction = "no" if diff == 0 else f"only {diff / orig_size:.1%}"
            print(
                f"{counter}'{orig_path}' {not_enough_reduction} smaller than original "
                "file. Keeping original."
            )
            os.remove(compressed_path)

    # remove ZIP archive and unused compressed PDFs
    for filename in (*compressed_files, downloaded_file):
        try:
            os.remove(filename)
        except OSError:
            pass
