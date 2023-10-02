from __future__ import annotations

import os
import re
from argparse import ArgumentParser
from glob import glob
from importlib.metadata import version
from typing import Sequence

from pdf_compressor.ilovepdf import Compress, ILovePDF
from pdf_compressor.utils import ROOT, del_or_keep_compressed, load_dotenv

DEFAULT_SUFFIX = "-compressed"


def main(argv: Sequence[str] | None = None) -> int:
    """Compress PDFs using iLovePDF's API."""
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

    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        "-i",
        "--inplace",
        action="store_true",
        help="Whether to compress PDFs in place. Defaults to False.",
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

    pkg_version = version(pkg_name := "pdf-compressor")
    parser.add_argument(
        "-v", "--version", action="version", version=f"{pkg_name} v{pkg_version}"
    )
    args = parser.parse_args(argv)

    if api_key := args.set_api_key:
        assert api_key.startswith(
            "project_public_"
        ), f"invalid API key, expected to start with 'project_public_', got {api_key=}"

        with open(f"{ROOT}/.env", "w+", encoding="utf8") as file:
            file.write(f"ILOVEPDF_PUBLIC_KEY={api_key}\n")

        return 0

    load_dotenv()

    if not (api_key := os.environ["ILOVEPDF_PUBLIC_KEY"]):
        raise ValueError(
            "pdf-compressor needs an iLovePDF public key to access its API. Set one "
            "with pdf-compressor --set-api-key project_public_7af905e..."
        )

    if args.report_quota:
        remaining_files = ILovePDF(api_key).get_quota()

        print(f"Remaining files in this billing cycle: {remaining_files:,}")

        return 0

    assert args.inplace or args.suffix, (
        "Files must either be compressed in-place (--inplace) or you must specify a "
        "non-empty suffix to append to the name of compressed files."
    )

    # use set() to ensure no duplicate files
    files: list[str] = sorted({f.replace("\\", "/").strip() for f in args.filenames})
    # for each directory received glob for all PDFs in it
    for filepath in files:
        if os.path.isdir(filepath):
            files.remove(filepath)
            files.extend(glob(os.path.join(filepath, "**", "*.pdf*"), recursive=True))
    # match files case insensitively ending with .pdf(,a,x) and possible white space
    pdfs = [f for f in files if re.match(r".*\.pdf[ax]?\s*$", f.lower())]
    not_pdfs = {*files} - {*pdfs}

    if args.on_bad_files == "error" and len(not_pdfs) > 0:
        raise ValueError(
            f"Input files must be PDFs, got {len(not_pdfs):,} files with unexpected "
            f"extension: {', '.join(not_pdfs)}"
        )
    if args.on_bad_files == "warn" and len(not_pdfs) > 0:
        print(
            f"Warning: Got {len(not_pdfs):,} input files without '.pdf' "
            f"extension: {', '.join(not_pdfs)}"
        )

    if args.verbose:
        if len(pdfs) > 0:
            print(f"PDFs to be compressed with iLovePDF: {len(pdfs):,}")
        else:
            print("Nothing to do: received no input PDF files.")

    if len(pdfs) == 0:
        if args.on_no_files == "error":
            raise ValueError("No input files provided")
        return 0

    task = Compress(api_key, compression_level=args.compression_level, debug=args.debug)
    task.verbose = args.verbose

    for pdf in pdfs:
        task.add_file(pdf)

    task.process()

    downloaded_file = task.download()

    task.delete_current_task()

    min_size_red = args.min_size_reduction or (10 if args.inplace else 0)

    if not args.debug:
        del_or_keep_compressed(
            pdfs, downloaded_file, args.inplace, args.suffix, min_size_red, args.verbose
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
