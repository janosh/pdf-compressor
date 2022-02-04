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

        # include path sep to test https://github.com/janosh/pdf-compressor/issues/9
        main([f".{os.path.sep}dummy.pdf"])

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
            os.replace(backup_path, pdf_path)


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

    with pytest.raises(AssertionError, match="invalid API key"):
        main(["--set-api-key", "foo"])

    main(["--set-api-key", "project_public_foobar"])

    load_dotenv()

    assert os.environ["ILOVEPDF_PUBLIC_KEY"] == "project_public_foobar"

    main(["--set-api-key", api_key])  # restore previous value


@pytest.mark.parametrize("arg", ["-v", "--version"])
def test_main_report_version(capsys, arg):
    """Test CLI version flag."""

    with pytest.raises(SystemExit):
        ret_code = main([arg])
        assert ret_code == 0

    stdout, stderr = capsys.readouterr()

    assert stdout.startswith("PDF Compressor v")
    assert stderr == ""


def test_main_bad_args():
    """Test bad CLI flags."""

    with pytest.raises(
        AssertionError, match="Files must either be compressed in-place"
    ):
        # empty suffix and no in-place flag are invalid
        main(["--suffix", "", pdf_path])


def test_main_error_on_no_input_files():
    """Test error when no PDF input files are provided."""

    with pytest.raises(ValueError, match="No input files provided"):
        ret_code = main(["--on-no-pdfs", "error"])
        assert ret_code == 1

    # check no error by default
    ret_code = main([])
    assert ret_code == 0


def test_main_bad_files(capsys):
    """Test bad file extensions."""

    files = ["foo.svg", "bar.pdf", "baz.png"]

    try:
        main(files + ["--on-bad-files", "ignore"])
    except FileNotFoundError:  # 'bar.pdf' does not exist
        pass

    stdout, stderr = capsys.readouterr()
    assert stdout == "" and stderr == ""

    try:
        main(files + ["--on-bad-files", "warn"])
    except FileNotFoundError:  # 'bar.pdf' does not exist
        pass

    stdout, stderr = capsys.readouterr()
    assert stdout.startswith("Warning: Got 2 input files without '.pdf' extension:")
    assert stderr == ""

    with pytest.raises(TypeError, match="Input files must be PDFs, got 2 "):
        main(files)
