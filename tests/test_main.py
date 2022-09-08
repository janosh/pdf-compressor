from __future__ import annotations

import os
import shutil
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from pytest import CaptureFixture

from pdf_compressor import DEFAULT_SUFFIX, main
from pdf_compressor.utils import load_dotenv

dummy_pdf = "assets/dummy.pdf"
compressed_pdf = f"dummy{DEFAULT_SUFFIX}.pdf"

expected_out = "'dummy.pdf' is now 9.6KB, was 13.0KB which is 3.4KB = 26% smaller.\n"


def test_main_batch_compress(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Test standard main() invocation batch-compressing 2 PDFs at once."""

    # include path sep to test https://github.com/janosh/pdf-compressor/issues/9
    input_pdf = f".{os.path.sep}dummy.pdf"
    shutil.copy2(dummy_pdf, input_path := str(tmp_path / input_pdf))

    shutil.copy2(dummy_pdf, input_path_2 := str(tmp_path / "dummy2.pdf"))

    # add input_path twice to test how we handle duplicate input files
    ret_code = main([input_path, input_path, input_path_2])
    assert ret_code == 0, "main() should return 0 on success"

    assert os.path.isfile(str(tmp_path / compressed_pdf))

    std_out, std_err = capsys.readouterr()
    assert (
        std_out == f"\n1: {expected_out}\n2: {expected_out.replace('dummy', 'dummy2')}"
    )
    assert std_err == ""


def test_main_in_place(capsys: CaptureFixture[str], tmp_path: Path) -> None:
    """Test in-place main() invocation."""

    input_pdf = shutil.copy2(dummy_pdf, tmp_path)

    ret_code = main([input_pdf, "-i"])
    assert ret_code == 0, "main() should return 0 on success"
    std_out, std_err = capsys.readouterr()
    if sys.platform == "darwin":
        assert std_out == expected_out + "Old file moved to trash.\n"
    else:
        assert std_out == expected_out + "Old file deleted.\n"
    assert std_err == ""

    # repeat same operation to test if original file (after 1st compression) can
    # successfully be moved to trash (pdf-compressor should append file counter
    # since a file by that name [the original input_pdf] already exists)
    main([input_pdf, "-i"])

    # test dropping minimum size reduction
    main([input_pdf, "-i", "--min-size-reduction", "0"])


def test_main_report_quota(capsys: CaptureFixture[str]) -> None:
    """Test CLI quota reporting."""

    main(["--report-quota"])

    std_out, std_err = capsys.readouterr()

    assert std_out.startswith("Remaining files ")
    assert std_err == ""


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

    std_out, std_err = capsys.readouterr()
    pkg_version = version(pkg_name := "pdf-compressor")
    assert std_out == f"{pkg_name} v{pkg_version}\n"
    assert std_err == ""


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

    std_out, std_err = capsys.readouterr()
    assert std_out == "" and std_err == ""

    try:
        main(files + ["--on-bad-files", "warn"])
    except FileNotFoundError:  # 'bar.pdf' does not exist
        pass

    std_out, std_err = capsys.readouterr()
    assert std_out.startswith("Warning: Got 2 input files without '.pdf' extension:")
    assert std_err == ""

    with pytest.raises(ValueError, match="Input files must be PDFs, got 2 "):
        main(files)
