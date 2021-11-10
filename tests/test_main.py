import os
from shutil import copy2 as cp

import pytest

from pdf_compressor import DEFAULT_SUFFIX, main
from pdf_compressor.utils import load_dotenv

pdf_path = "assets/dummy.pdf"
backup_path = "assets/dummy-backup.pdf"
compressed_pdf_path = f"assets/dummy{DEFAULT_SUFFIX}.pdf"


def test_main():
    """Test standard main() invocation with changing cwd to PDF's folder."""

    root_dir = os.getcwd()

    try:
        os.chdir("./assets")

        main(["dummy.pdf"])

    finally:  # ensures clean up code runs even if main() crashed
        os.chdir(root_dir)

        if os.path.isfile(compressed_pdf_path):
            os.remove(compressed_pdf_path)


def test_main_in_place():
    """Test in-place main() invocation."""

    cp(pdf_path, backup_path)

    try:
        main([pdf_path, "-i"])

        # repeat same operation to test if file can be moved to trash (pdf-compressor
        # should append file counter since a file by that name already exists)
        cp(backup_path, pdf_path)
        main([pdf_path, "-i"])

        # test dropping minimum size reduction
        cp(backup_path, pdf_path)
        main([pdf_path, "-i", "--min-size-reduction", "0"])

    finally:
        if os.path.isfile(backup_path):
            os.rename(backup_path, pdf_path)


def test_main_multi_file():
    """Test multi-file main() invocation."""

    dummy_1 = "assets/dummy-1.pdf"
    dummy_2 = "assets/dummy-2.pdf"

    cp(pdf_path, dummy_1)
    cp(pdf_path, dummy_2)

    try:
        main([dummy_1, dummy_2, "-i"])

    finally:
        for path in [dummy_1, dummy_2]:
            if os.path.isfile(path):
                os.remove(path)


def test_main_bad_files():
    """Test main() with bad file extensions."""

    with pytest.raises(AssertionError, match="Input files must be PDFs, got 2 "):
        main(["foo.svg", "bar.pdf", "baz.png"])


def test_main_bad_args():
    """Test main() with bad CLI flags."""

    with pytest.raises(
        AssertionError, match="Files must either be compressed in-place"
    ):
        main(["--suffix", "", pdf_path])


def test_main_report_quota(capsys):
    """Test CLI quota reporting."""

    main(["--report-quota"])

    stdout, stderr = capsys.readouterr()

    assert stdout.startswith("Remaining files ")
    assert stderr == ""


def test_main_set_api_key():
    """Test CLI setting iLovePDF public API key."""

    load_dotenv()

    api_key = os.environ["ILOVEPDF_PUBLIC_KEY"]  # save API key to reset it later

    main(["--set-api-key", "project_public_foobar"])

    load_dotenv()

    assert os.environ["ILOVEPDF_PUBLIC_KEY"] == "project_public_foobar"

    main(["--set-api-key", api_key])  # restore previous value
