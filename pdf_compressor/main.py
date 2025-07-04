from __future__ import annotations

import os
import re
from argparse import ArgumentParser
from glob import glob
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from pdf_compressor.ilovepdf import Compress, ILovePDF
from pdf_compressor.utils import ROOT, del_or_keep_compressed, load_dotenv

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_SUFFIX = "-compressed"
API_KEY_KEY = "ILOVEPDF_PUBLIC_KEY"
MISSING_API_KEY_ERR = KeyError(
    "pdf-compressor needs an iLovePDF public key to access its API. Set one "
    "with pdf-compressor --set-api-key project_public_7af905e... or as environment "
    f"variable {API_KEY_KEY}"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Compress PDFs using iLovePDF's API.

    Args:
        argv (Sequence[str] | None): Command line arguments.

    Returns:
        int: 0 if successful, else error code.

    Raises:
        ValueError: If the API key is invalid.
    """
    parser = ArgumentParser(
        description="Batch compress PDFs on the command line. Powered by iLovePDF.com.",
        allow_abbrev=False,
    )

    parser.add_argument("filenames", nargs="*", help="List of PDF files to compress.")

    parser.add_argument(
        "--set-api-key",
        help="Set the public key needed to authenticate with the iLovePDF API. Exits "
        "immediately afterwards ignoring all other flags.",
    )

    parser.add_argument(
        "--password",
        type=str,
        default="",
        help="Password for protected PDF files. All files will use the same password. "
        "Protected PDFs with different passwords must be compressed one by one.",
    )
    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        "-i",
        "--inplace",
        action="store_true",
        help="Whether to compress PDFs in place. Defaults to False.",
    )
    parser.add_argument(
        "-o",
        "--outdir",
        type=str,
        default="",
        help="Output directory for compressed PDFs. Defaults to the current working "
        "directory.",
    )

    group.add_argument(
        "-s",
        "--suffix",
        default=DEFAULT_SUFFIX,
        help="String to append to the filename of compressed PDFs. Mutually exclusive "
        "with --inplace flag.",
    )

    parser.add_argument(
        "--report-quota",
        action="store_true",
        help="Report how much of the monthly quota for the current API key has been "
        "used up.",
    )

    parser.add_argument(
        "--compression-level",
        "--cl",
        choices=("low", "recommended", "extreme"),
        default="recommended",
        help="How hard to squeeze the file size. 'extreme' noticeably degrades image "
        "quality. Defaults to 'recommended'.",
    )

    parser.add_argument(
        "--min-size-reduction",
        "--min-red",
        type=int,
        choices=range(101),
        metavar="[0-100]",  # prevents long list in argparse help message
        help="How much compressed files need to be smaller than originals (in percent) "
        "for them to be kept. Defaults to 10 when also passing -i/--inplace, else 0."
        "For example, when compressing files in-place and only achieving 5%% file size "
        "reduction, the compressed file will be discarded.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="When true, iLovePDF won't process the request but will output the "
        "parameters received by the server. Defaults to False.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="When true, progress will be reported while tasks are running. Also prints"
        " full file paths to compressed files instead of file name only. Defaults "
        "to False.",
    )

    parser.add_argument(
        "--on-no-files",
        choices=("error", "ignore"),
        default="ignore",
        help="What to do when no input PDFs received. One of 'ignore' or 'error', "
        "former exits 0, latter throws ValueError. Can be useful when using "
        "pdf-compressor in shell scripts. Defaults to 'ignore'.",
    )

    parser.add_argument(
        "--on-bad-files",
        choices=("error", "warn", "ignore"),
        default="error",
        help="How to behave when receiving input files that don't appear to be PDFs. "
        "One of 'error', 'warn', 'ignore'. Error will be TypeError. "
        "Defaults to 'error'.",
    )

    parser.add_argument(
        "--write-stats-path",
        type=str,
        default="",
        help="File path to write a CSV, Excel or other pandas supported file format "
        "with original vs compressed file sizes and actions taken on each input file",
    )

    pkg_version = version(pkg_name := "pdf-compressor")
    parser.add_argument(
        "-v", "--version", action="version", version=f"{pkg_name} v{pkg_version}"
    )
    args, _unknown_args = parser.parse_known_args(argv)

    if new_key := args.set_api_key:
        if not new_key.startswith("project_public_"):
            raise ValueError(
                f"invalid API key, must start with 'project_public_', got {new_key=}"
            )

        with open(f"{ROOT}/.env", mode="w+", encoding="utf8") as file:
            file.write(f"ILOVEPDF_PUBLIC_KEY={new_key}\n")

        return 0

    load_dotenv()
    if not (api_key := os.environ[API_KEY_KEY]):
        raise MISSING_API_KEY_ERR

    if args.report_quota:
        remaining_credits = ILovePDF(api_key).get_quota()

        print(f"Remaining files in this billing cycle: {remaining_credits:,}")

        return 0

    return compress(**vars(args))  # ty: ignore[missing-argument]


def compress(
    filenames: Sequence[str],
    *,
    inplace: bool = False,
    outdir: str = "",
    suffix: str = DEFAULT_SUFFIX,
    compression_level: str = "recommended",
    min_size_reduction: int | None = None,
    debug: bool = False,
    verbose: bool = False,
    on_no_files: str = "ignore",
    on_bad_files: str = "error",
    write_stats_path: str = "",
    password: str = "",
    **kwargs: Any,  # noqa: ARG001
) -> int:
    """Compress PDFs using iLovePDF's API.

    Args:
        filenames (list[str]): List of PDF files to compress.
        inplace (bool): Whether to compress PDFs in place.
        outdir (str): Output directory for compressed PDFs. Defaults to the current
            working directory.
        suffix (str): String to append to the filename of compressed PDFs.
        compression_level (str): How hard to squeeze the file size.
        min_size_reduction (int): How much compressed files need to be smaller than
            originals (in percent) for them to be kept.
        debug (bool): When true, iLovePDF won't process the request but will output the
            parameters received by the server.
        verbose (bool): When true, progress will be reported while tasks are running.
        on_no_files (str): What to do when no input PDFs received.
        on_bad_files (str): How to behave when receiving input files that don't appear
            to be PDFs.
        write_stats_path (str): File path to write a CSV, Excel or other pandas
            supported file format with original vs compressed file sizes and actions
            taken on each input file
        password (str): Password to open PDFs in case they have one. Defaults to "".
            TODO There's currently no way of passing different passwords for different
            files. PDFs with different passwords must be compressed one by one.
        **kwargs: Additional keywords are ignored.

    Returns:
        int: 0 if successful, else error code.

    Raises:
        ImportError: If pandas is not installed.
        ValueError: If the API key is invalid.
    """
    if min_size_reduction is None:
        min_size_reduction = 10 if inplace else 0

    load_dotenv()
    if not (api_key := os.environ[API_KEY_KEY]):
        raise MISSING_API_KEY_ERR

    if not (inplace or suffix):
        raise ValueError(
            "Files must either be compressed in-place (--inplace) or you must specify a"
            " non-empty suffix to append to the name of compressed files."
        )

    # use set() to ensure no duplicate files
    uniq_files: list[str] = sorted({fn.replace("\\", "/").strip() for fn in filenames})
    # for each directory received glob for all PDFs in it
    file_paths = []
    for file_path in uniq_files:
        if os.path.isdir(file_path):
            file_paths += glob(os.path.join(file_path, "**", "*.pdf*"), recursive=True)
        else:
            file_paths += [file_path]

    # match files case insensitively ending with .pdf(,a,x) and possible white space
    pdf_paths = [f for f in file_paths if re.match(r".*\.pdf[ax]?\s*$", f.lower())]
    not_pdf_paths = {*file_paths} - {*pdf_paths}

    if on_bad_files == "error" and len(not_pdf_paths) > 0:
        raise ValueError(
            f"Input files must be PDFs, got {len(not_pdf_paths):,} files with "
            f"unexpected extension: {', '.join(not_pdf_paths)}"
        )
    if on_bad_files == "warn" and len(not_pdf_paths) > 0:
        print(
            f"Warning: Got {len(not_pdf_paths):,} input files without '.pdf' "
            f"extension: {', '.join(not_pdf_paths)}"
        )

    if verbose:
        if len(pdf_paths) > 0:
            print(f"PDFs to be compressed with iLovePDF: {len(pdf_paths):,}")
        else:
            print("Nothing to do: received no input PDF files.")

    if len(pdf_paths) == 0:
        if on_no_files == "error":
            raise ValueError("No input files provided")
        return 0

    task = Compress(
        api_key, compression_level=compression_level, debug=debug, password=password
    )
    task.verbose = verbose

    for pdf in pdf_paths:
        task.add_file(pdf)

    task.process()

    downloaded_file = task.download(save_to_dir=outdir)

    task.delete_current_task()

    min_size_red = min_size_reduction or (10 if inplace else 0)

    if debug:
        stats = {}
    else:
        stats = del_or_keep_compressed(
            pdf_paths,
            downloaded_file,
            inplace=inplace,
            suffix=suffix,
            min_size_reduction=min_size_red,
            verbose=verbose,
        )

    if write_stats_path:
        try:
            import pandas as pd  # noqa: PLC0415
        except ImportError:
            err_msg = "To write stats to file, install pandas: pip install pandas"
            raise ImportError(err_msg) from None

        df_stats = pd.DataFrame(stats).T
        df_stats.index.name = "file"
        stats_path_lower = write_stats_path.strip().lower()

        if ".csv" in stats_path_lower:
            df_stats.to_csv(write_stats_path, float_format="%.4f")
        elif ".xlsx" in stats_path_lower or ".xls" in stats_path_lower:
            df_stats.to_excel(write_stats_path, float_format="%.4f")
        elif ".json" in stats_path_lower:
            df_stats.to_json(write_stats_path)
        elif ".html" in stats_path_lower:
            df_stats.to_html(write_stats_path, float_format="%.4f")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
