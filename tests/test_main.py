import os

from pdf_compressor import main
from pdf_compressor.utils import load_dotenv


def test_main():
    """Test main()."""

    main(["tests/pdfs/a.pdf"])


def test_main_set_api_key():

    load_dotenv()

    api_key = os.environ["ILOVEPDF_PUBLIC_KEY"]  # save API key to reset it later

    main(["--set-api-key", "foobar"])

    load_dotenv()

    assert os.environ["ILOVEPDF_PUBLIC_KEY"] == "foobar"

    main(["--set-api-key", api_key])  # restore previous value
