import os
import sys
from importlib.metadata import version
from pathlib import Path
from shutil import copy2

import pytest
from pytest import CaptureFixture, MonkeyPatch

from pdf_compressor import DEFAULT_SUFFIX, main
from pdf_compressor.utils import load_dotenv

dummy_pdf = "assets/dummy.pdf"
compressed_pdf = f"dummy{DEFAULT_SUFFIX}.pdf"

expected_out = "'dummy.pdf' is now 9.6KB, before 13.0KB (3.4KB = 26.2% smaller)\n"


def test_main_format_cells(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    """Test standard main() invocation with changing cwd to PDF's folder."""

    # include path sep to test https://github.com/janosh/pdf-compressor/issues/9
    input_pdf = f".{os.path.sep}dummy.pdf"
    copy2(dummy_pdf, tmp_path / input_pdf)
    monkeypatch.chdir(tmp_path)

    ret = main([input_pdf])
    assert ret == 0, "main() should return 0 on success"

    assert os.path.isfile(compressed_pdf)

    out, err = capsys.readouterr()
    assert out == expected_out
    assert err == ""


def test_main_in_place(capsys: CaptureFixture[str], tmp_path: Path) -> None:
    """Test in-place main() invocation."""

    input_pdf = copy2(dummy_pdf, tmp_path)

    ret = main([input_pdf, "-i"])
    assert ret == 0, "main() should return 0 on success"
    out, err = capsys.readouterr()
    if sys.platform == "darwin":
        assert out == expected_out + "Old file moved to trash.\n"
    else:
        assert out == expected_out + "Old file deleted.\n"
    assert err == ""

    # repeat same operation to test if original file (after 1st compression) can
    # successfully be moved to trash (pdf-compressor should append file counter
    # since a file by that name [the original input_pdf] already exists)
    main([input_pdf, "-i"])

    # test dropping minimum size reduction
    main([input_pdf, "-i", "--min-size-reduction", "0"])


def test_main_report_quota(capsys: CaptureFixture[str]) -> None:
    """Test CLI quota reporting."""

    main(["--report-quota"])

    stdout, stderr = capsys.readouterr()

    assert stdout.startswith("Remaining files ")
    assert stderr == ""


def test_main_set_api_key() -> None:
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
def test_main_report_version(capsys: CaptureFixture[str], arg: str) -> None:
    """Test CLI version flag."""

    with pytest.raises(SystemExit):
        ret_code = main([arg])
        assert ret_code == 0

    stdout, stderr = capsys.readouterr()
    pkg_version = version(pkg_name := "pdf-compressor")
    assert stdout == f"{pkg_name} v{pkg_version}\n"
    assert stderr == ""


def test_main_bad_args() -> None:
    """Test bad CLI flags."""

    with pytest.raises(
        AssertionError, match="Files must either be compressed in-place"
    ):
        # empty suffix and no in-place flag are invalid
        main(["--suffix", "", dummy_pdf])


def test_main_error_on_no_input_files() -> None:
    """Test error when no PDF input files are provided."""

    with pytest.raises(ValueError, match="No input files provided"):
        ret_code = main(["--on-no-pdfs", "error"])
        assert ret_code == 1

    # check no error by default
    ret_code = main([])
    assert ret_code == 0


def test_main_bad_files(capsys: CaptureFixture[str]) -> None:
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
