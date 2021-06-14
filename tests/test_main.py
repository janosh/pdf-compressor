import os
from importlib import reload

from pdf_compressor import env, main


def test_main():
    """Test main()."""

    main(["tests/pdfs/a.pdf"])


def test_main_set_api_key():

    api_key_file = "pdf_compressor/env.py"
    backup_file = "env_bak.py"

    if os.path.exists(api_key_file) and not os.path.exists(backup_file):
        os.rename(api_key_file, backup_file)

    main(["--set-api-key", "foobar"])

    reload(env)

    assert env.ILOVEPDF_PUBLIC_KEY == "foobar"

    os.rename(backup_file, api_key_file)
