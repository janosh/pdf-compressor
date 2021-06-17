import os

from pdf_compressor import main
from pdf_compressor.utils import load_dotenv


def test_main():
    """Test main()."""

    main(["tests/pdfs/a.pdf"])


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
