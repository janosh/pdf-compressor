import os

import pytest

from pdf_compressor import main
from pdf_compressor.utils import load_dotenv


def test_main():
    """Test standard main() invocation."""

    main(["tests/pdfs/a.pdf"])


def test_main_bad_files():
    """Test main() with bad file extensions."""

    with pytest.raises(AssertionError, match="Input files must be PDFs, got 2 "):
        main(["foo.svg", "bar.pdf", "baz.png"])


def test_main_bad_args():
    """Test main() with bad CLI flags."""

    with pytest.raises(
        AssertionError, match="Files must either be compressed in-place"
    ):
        main(["--suffix", "", "tests/pdfs/a.pdf"])


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
