import os
import sys
from argparse import ArgumentParser
from importlib.metadata import version
from os.path import exists, expanduser, getsize, relpath, split
from typing import Sequence

from .ilovepdf import Compress, ILovePDF
from .utils import ROOT, load_dotenv, sizeof_fmt


def main(argv: Sequence[str] = None) -> int:

    parser = ArgumentParser(
        "PyDF Compress", description="Batch compress PDFs powered by iLovePDF.com"
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
        "-i",
        "--inplace",
        action="store_true",
        help="Whether to compress PDFs in place. Defaults to False.",
    )

    parser.add_argument(
        "-s",
        "--suffix",
        default="-compressed",
        help="String to append to the filename of compressed PDFs. Mutually exclusive with "
        "--inplace flag.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="When true, iLovePDF won't process the request but will output the parameters "
        "received by the server..",
    )

    tb_version = version("pdf_compressor")

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

    # if filenames received as command line arguments are relative paths,
    # convert to absolute paths
    paths = [a if a.startswith("/") else f"{os.getcwd()}/{a}" for a in args.filenames]
    # Keep only paths pointing to PDFs that exist.
    pdfs = [p for p in paths if p.endswith(".pdf") and exists(p)]

    assert (
        pdfs
    ), f"Invalid arguments, files must be PDFs, got {len(paths)} files without .pdf extension."

    print(f"{len(pdfs)} PDFs to be compressed with iLovePDF:")
    for pdf in pdfs:
        print(f"- {relpath(pdf, expanduser('~'))}")

    trash_path = f"{expanduser('~')}/.Trash"

    task = Compress(api_key, debug=args.debug)

    for idx, pdf_path in enumerate(pdfs, 1):

        task.add_file(pdf_path)

        dir_name, pdf_name = split(pdf_path)

        task.set_output_folder(dir_name)

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
                f"({sizeof_fmt(compressed_size)} vs {sizeof_fmt(orig_size)}). Using "
                "compressed file."
            )

            if args.inplace:
                if sys.platform == "darwin":  # move file to trash on macOS
                    print("Old file moved to trash.\n")
                    os.rename(pdf_path, f"{trash_path}/{pdf_name}")
                else:  # simply delete it on other platforms
                    print("Old file deleted.\n")
                    os.remove(pdf_path)

                os.rename(compressed_pdf_path, pdf_path)

            else:
                # TODO: implement adding suffix to compressed PDF file name
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
