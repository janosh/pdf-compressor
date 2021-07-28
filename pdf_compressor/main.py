import os
import sys
from argparse import ArgumentParser
from importlib.metadata import version
from os.path import expanduser, getsize, relpath, split
from typing import Sequence

from pdf_compressor.ilovepdf import Compress, ILovePDF
from pdf_compressor.utils import ROOT, load_dotenv, sizeof_fmt


DEFAULT_SUFFIX = "-compressed"


def main(argv: Sequence[str] = None) -> int:

    parser = ArgumentParser(
        "PDF Compressor",
        description="Batch compress PDFs on the command line. Powered by iLovePDF.com.",
    )

    parser.add_argument(
        "--set-api-key",
        help="Set the public key needed to authenticate with the iLovePDF API. Exits "
        "immediately afterwards ignoring all other flags.",
    )

    parser.add_argument(
        "--report-quota",
        action="store_true",
        help="Report how much of the monthly quota for the current API key has been used up.",
    )

    parser.add_argument("filenames", nargs="*", help="List of PDF files to compress.")

    parser.add_argument(
        "--compression-level",
        "--cl",
        choices=("low", "recommended", "extreme"),
        default="recommended",
        help="How hard to squeeze the file size. 'extreme' noticeably degrades image quality. "
        "Defaults to 'recommended'.",
    )

    parser.add_argument(
        "-i",
        "--inplace",
        action="store_true",
        help="Whether to compress PDFs in place. Defaults to False.",
    )

    parser.add_argument(
        "-s",
        "--suffix",
        default=DEFAULT_SUFFIX,
        help="String to append to the filename of compressed PDFs. Mutually exclusive with "
        "--inplace flag.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="When true, iLovePDF won't process the request but will output the parameters "
        "received by the server..",
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
            "pdf-compressor needs a iLovePDF public key to access its API. Set one with "
            "pdf-compressor --set-api-key project_public_7af905e..."
        )

    if args.report_quota:

        ILovePDF(api_key).report_quota()

        return 0

    assert args.inplace or args.suffix, (
        "Files must either be compressed in-place (--inplace) or you must specify a non-empty "
        "suffix to append to the name of compressed files."
    )

    pdfs = [file for file in args.filenames if file.lower().endswith(".pdf")]
    not_pdfs = [file for file in args.filenames if not file.lower().endswith(".pdf")]

    assert len(not_pdfs) == 0, (
        f"Input files must be PDFs, got {len(not_pdfs)} files without .pdf "
        f"extension: {', '.join(not_pdfs)}"
    )

    print(f"PDFs to be compressed with iLovePDF: {len(pdfs)}")
    for pdf in pdfs:
        print(f"- {relpath(pdf, expanduser('~'))}")

    trash_path = f"{expanduser('~')}/.Trash"

    for idx, pdf_path in enumerate(pdfs, 1):
        task = Compress(
            api_key, compression_level=args.compression_level, debug=args.debug
        )

        task.add_file(pdf_path)

        dir_name, pdf_name = split(pdf_path)

        task.set_outdir(dir_name)

        task.process()
        compressed_pdf_name = task.download()
        task.delete_current_task()

        if args.debug:
            continue

        compressed_pdf_path = f"{dir_name}/{compressed_pdf_name}"

        orig_size = getsize(pdf_path)
        compressed_size = getsize(compressed_pdf_path)

        diff = orig_size - compressed_size
        if diff > 0:
            percent_diff = 100 * diff / orig_size

            print(
                f"{idx}/{len(pdfs)} Compressed PDF '{pdf_name}' is {sizeof_fmt(diff)} "
                f"({percent_diff:.2g} %) smaller than the original "
                f"({sizeof_fmt(compressed_size)} vs {sizeof_fmt(orig_size)})."
            )

            if args.inplace:
                # move original PDF file to trash on macOS (for later retrieval if necessary)
                # simply let os.rename() overwrite existing PDF on other platforms
                if sys.platform == "darwin":
                    print("Using compressed file. Old file moved to trash.\n")
                    os.rename(pdf_path, f"{trash_path}/{pdf_name}")

                os.rename(compressed_pdf_path, pdf_path)

            elif args.suffix:
                path_name, ext = os.path.splitext(pdf_path)
                new_path = f"{path_name}{args.suffix}{ext}"

                if os.path.isfile(new_path):
                    counter = 2
                    while os.path.isfile(f"{path_name}{args.suffix}-{counter}{ext}"):
                        counter += 1
                    new_path = f"{path_name}{args.suffix}-{counter}{ext}"

                pdf_path = new_path

                os.rename(compressed_pdf_path, pdf_path)

        else:
            print(
                f"{idx}/{len(pdfs)} Compressed '{pdf_name}' no smaller than original "
                "file. Keeping original."
            )
            os.remove(compressed_pdf_path)

    return 0


if __name__ == "__main__":
    exit(main())
