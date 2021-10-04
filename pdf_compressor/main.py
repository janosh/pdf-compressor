import os
from argparse import ArgumentParser
from importlib.metadata import version
from os.path import expanduser, relpath
from typing import Sequence

from pdf_compressor.ilovepdf import Compress, ILovePDF
from pdf_compressor.utils import ROOT, del_or_keep_compressed, load_dotenv


DEFAULT_SUFFIX = "-compressed"


def main(argv: Sequence[str] = None) -> int:

    parser = ArgumentParser(
        "PDF Compressor",
        description="Batch compress PDFs on the command line. Powered by iLovePDF.com.",
    )

    parser.add_argument("filenames", nargs="*", help="List of PDF files to compress.")

    parser.add_argument(
        "--set-api-key",
        help="Set the public key needed to authenticate with the iLovePDF API. Exits "
        "immediately afterwards ignoring all other flags.",
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
        "--handle-non-pdfs",
        choices=("error", "warn", "ignore"),
        default="error",
        help="How to behave when receiving non-PDF input files. Defaults to 'error'.",
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
        "--debug",
        action="store_true",
        help="When true, iLovePDF won't process the request but will output the "
        "parameters received by the server. Defaults to False.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="When true, progress will be reported while tasks are running. Defaults "
        "to False.",
    )

    tb_version = version("pdf-compressor")

    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s v{tb_version}"
    )
    args = parser.parse_args(argv)

    if api_key := args.set_api_key:

        assert api_key.startswith(
            "project_public_"
        ), f"invalid API key, expected to start with 'project_public_', got {api_key=}"

        with open(f"{ROOT}/.env", "w+") as file:
            file.write(f"ILOVEPDF_PUBLIC_KEY={api_key}\n")

        return 0

    load_dotenv()

    if not (api_key := os.environ["ILOVEPDF_PUBLIC_KEY"]):
        raise ValueError(
            "pdf-compressor needs an iLovePDF public key to access its API. Set one "
            "with pdf-compressor --set-api-key project_public_7af905e..."
        )

    if args.report_quota:

        ILovePDF(api_key).report_quota()

        return 0

    assert args.inplace or args.suffix, (
        "Files must either be compressed in-place (--inplace) or you must specify a "
        "non-empty suffix to append to the name of compressed files."
    )

    pdfs = [file for file in args.filenames if file.lower().endswith(".pdf")]
    not_pdfs = [file for file in args.filenames if not file.lower().endswith(".pdf")]

    if args.handle_non_pdfs == "error":
        assert len(not_pdfs) == 0, (
            f"Input files must be PDFs, got {len(not_pdfs)} files without '.pdf' "
            f"extension: {', '.join(not_pdfs)}"
        )
    elif args.handle_non_pdfs == "warn":
        print(
            f"Warning: Got {len(not_pdfs)} input files without '.pdf' "
            f"extension: {', '.join(not_pdfs)}"
        )

    print(f"PDFs to be compressed with iLovePDF: {len(pdfs)}")

    task = Compress(api_key, compression_level=args.compression_level, debug=args.debug)
    task.verbose = args.verbose

    for pdf in pdfs:
        task.add_file(pdf)
        print(f"- {relpath(pdf, expanduser('~'))}")

    task.process()

    downloaded_file = task.download()

    task.delete_current_task()

    if not args.debug:
        del_or_keep_compressed(pdfs, downloaded_file, args.inplace, args.suffix)

    return 0


if __name__ == "__main__":
    exit(main())
