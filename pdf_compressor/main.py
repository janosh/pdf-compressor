import os
import sys
from argparse import ArgumentParser
from importlib.metadata import version
from os.path import abspath, basename, dirname, expanduser
from typing import Optional, Sequence

from .ilovepdf import Compress
from .utils import sizeof_fmt


def main(argv: Optional[Sequence[str]] = None) -> int:

    parser = ArgumentParser(
        "PyDF Compress", description="Batch compress PDFs powered by iLovePDF.com"
    )

    parser.add_argument(
        "--set-api-key",
        help="Set the public key needed to authenticate with the iLovePDF API.",
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

    if args.set_api_key:

        with open(f"{dirname(abspath(__file__))}/env.py", "w+") as file:
            file.write(f'ILOVEPDF_PUBLIC_KEY = "{args.set_api_key}"\n')

        return 0

    # if filenames received as command line arguments are relative paths,
    # convert to absolute paths
    paths = [a if a.startswith("/") else f"{os.getcwd()}/{a}" for a in args.filenames]
    # Keep only paths pointing to PDFs that exist.
    pdfs = [p for p in paths if p.endswith(".pdf") and os.path.exists(p)]

    assert (
        pdfs
    ), f"Invalid arguments, files must be PDFs, got {len(paths)} files without .pdf extension."

    print(f"{(n_pdfs := len(pdfs))} PDFs to be compressed with iLovePDF:")
    for pdf in pdfs:
        print(f"- {basename(pdf)}")

    trash_path = f"{expanduser('~')}/.Trash"

    from .env import ILOVEPDF_PUBLIC_KEY

    task = Compress(ILOVEPDF_PUBLIC_KEY, debug=args.debug)

    for idx, pdf_path in enumerate(pdfs, 1):

        task.add_file(pdf_path)

        dir_name, pdf_name = os.path.split(pdf_path)

        task.set_output_folder(dir_name)

        task.process()
        compressed_pdf_name = task.download()
        task.delete_current_task()

        if args.debug:
            continue

        compressed_pdf_path = f"{dir_name}/{compressed_pdf_name}"

        orig_size = os.path.getsize(pdf_path)
        compressed_size = os.path.getsize(compressed_pdf_path)

        diff = orig_size - compressed_size
        if diff > 0:
            percent_diff = 100 * diff / orig_size
            print(
                f"{idx}/{n_pdfs} Compressed PDF '{pdf_name}' is {sizeof_fmt(diff)} "
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
                f"{idx}/{n_pdfs} Compressed '{pdf_name}' no smaller than original "
                "file. Keeping original."
            )
            os.remove(compressed_pdf_path)

    return 0


if __name__ == "__main__":
    exit(main())
