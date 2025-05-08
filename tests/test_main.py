from __future__ import annotations

import os
import shutil
import sys
from importlib.metadata import version
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import pandas as pd
import pytest
from pytest import CaptureFixture

from pdf_compressor import DEFAULT_SUFFIX, main
from pdf_compressor.main import API_KEY_KEY
from pdf_compressor.utils import load_dotenv

if TYPE_CHECKING:
    from pathlib import Path

    from pdf_compressor.ilovepdf import Task

dummy_pdf = "assets/dummy.pdf"
compressed_pdf = f"dummy{DEFAULT_SUFFIX}.pdf"

expected_out = "'dummy.pdf': 13.0KB -> 9.6KB which is 3.4KB = 26% smaller.\n"


def test_main_batch_compress(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Test standard main() invocation batch-compressing 2 PDFs at once."""
    # include path sep to test https://github.com/janosh/pdf-compressor/issues/9
    input_pdf = f".{os.path.sep}dummy.pdf"
    shutil.copy2(dummy_pdf, input_path := str(tmp_path / input_pdf))

    shutil.copy2(dummy_pdf, input_path_2 := str(tmp_path / "dummy2.pdf"))

    # add input_path twice to test how we handle duplicate input files
    stats_path = f"{tmp_path}/stats.csv"
    ret_code = main(
        [input_path, input_path, input_path_2, "--write-stats-path", stats_path]
    )
    assert ret_code == 0, f"expected main() exit code to be 0, got {ret_code}"

    # check stats file was written and has expected content
    df_stats = pd.read_csv(stats_path)
    assert list(df_stats) == [
        "file",
        "original size (B)",
        "compressed size (B)",
        "size reduction (B)",
        "size reduction (%)",
        "action",
    ]
    assert df_stats.shape == (2, 6)
    assert df_stats.file.tolist() == ["dummy.pdf", "dummy2.pdf"]

    assert os.path.isfile(f"{tmp_path}/{compressed_pdf}")

    std_out, std_err = capsys.readouterr()
    assert std_out == f"\n1 {expected_out}\n2 {expected_out.replace('dummy', 'dummy2')}"
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


def test_main_dir_glob(capsys: CaptureFixture[str], tmp_path: Path) -> None:
    """Test passing a directory to make sure main() recursively globs for PDFs."""
    input_pdf = shutil.copy2(dummy_pdf, tmp_path)

    ret_code = main([str(tmp_path), "-i", "--verbose"])
    assert ret_code == 0, "main() should return 0 on success"
    std_out, std_err = capsys.readouterr()
    assert std_out.startswith("PDFs to be compressed with iLovePDF: 1")
    if sys.platform == "darwin":
        assert input_pdf in std_out
    assert std_err == ""


def test_main_report_quota(capsys: CaptureFixture[str]) -> None:
    """Test CLI quota reporting."""
    main(["--report-quota"])

    std_out, std_err = capsys.readouterr()

    assert std_out.startswith("Remaining files in this billing cycle: ")
    assert std_err == ""


def test_main_set_api_key() -> None:
    """Test CLI setting iLovePDF public API key."""
    load_dotenv()

    api_key = os.environ[API_KEY_KEY]  # save API key to reset it later

    with pytest.raises(ValueError, match="invalid API key"):
        main(["--set-api-key", "foo"])

    main(["--set-api-key", "project_public_foobar"])

    load_dotenv()

    assert os.environ[API_KEY_KEY] == "project_public_foobar"

    main(["--set-api-key", api_key])  # restore previous value


@pytest.mark.parametrize("arg", ["-v", "--version"])
def test_main_report_version(capsys: CaptureFixture[str], arg: str) -> None:
    """Test CLI version flag."""
    with pytest.raises(SystemExit) as exc_info:
        main([arg])
    assert exc_info.value.code == 0

    std_out, std_err = capsys.readouterr()
    pkg_version = version(pkg_name := "pdf-compressor")
    assert std_out == f"{pkg_name} v{pkg_version}\n"
    assert std_err == ""


def test_main_bad_args() -> None:
    """Test bad CLI flags."""
    with pytest.raises(ValueError, match="Files must either be compressed in-place"):
        # empty suffix and no in-place flag are invalid
        main(["--suffix", "", dummy_pdf])


def test_main_error_on_no_input_files() -> None:
    """Test error when no PDF input files are provided."""
    with pytest.raises(ValueError, match="No input files provided"):
        main(["--on-no-files", "error"])

    # check no error by default
    ret_code = main([])
    assert ret_code == 0


def test_main_bad_files(capsys: CaptureFixture[str]) -> None:
    """Test bad file extensions."""
    files = ["foo.svg", "bar.pdf", "baz.png"]

    try:
        main([*files, "--on-bad-files", "ignore"])
    except FileNotFoundError:  # 'bar.pdf' does not exist
        pass

    std_out, std_err = capsys.readouterr()
    assert std_out == ""
    assert std_err == ""

    try:
        main([*files, "--on-bad-files", "warn"])
    except FileNotFoundError:  # 'bar.pdf' does not exist
        pass

    std_out, std_err = capsys.readouterr()
    assert std_out.startswith("Warning: Got 2 input files without '.pdf' extension:")
    assert std_err == ""

    with pytest.raises(ValueError, match="Input files must be PDFs, got 2 "):
        main(files)


def test_main_password_outdir_flags(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Test the --password CLI flag and assert password in API payload."""
    input_pdf1 = shutil.copy2(dummy_pdf, tmp_path / "test1.pdf")
    input_pdf2 = shutil.copy2(dummy_pdf, tmp_path / "test2.pdf")
    test_password = "test123"  # noqa: S105

    def mock_send_request(
        self: Task,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> MagicMock:
        if method == "post" and endpoint == "process" and payload is not None:
            # Check that each file in the payload has the correct password
            for idx in range(len(self.files)):
                assert payload[f"files[{idx}][password]"] == test_password, (
                    f"File {idx} does not have the correct password in the payload"
                )

        # Mock response for process endpoint
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "timer": "1",
            "status": "TaskSuccess",
            "download_filename": "compressed.pdf",
            "filesize": 1000,
            "output_filesize": 800,
            "output_filenumber": 2,
            "output_extensions": ["pdf"],
            "token": "1234567890",
            "server": "https://iloveapi.com",
            "task": "compress",
            "server_filename": "compressed.pdf",
        }
        mock_response.content = b"Mocked response content"
        # make tmp ZipFile at tmp_path/compressed.pdf
        with ZipFile(tmp_path / "compressed.pdf", "w") as zip_file:
            zip_file.write(input_pdf1, "test1.pdf")
            zip_file.write(input_pdf2, "test2.pdf")

        return mock_response

    with patch("pdf_compressor.Compress._send_request", new=mock_send_request):
        # Convert PosixPath objects to strings
        ret_code = main(
            [
                str(input_pdf1),
                str(input_pdf2),
                "--outdir",
                str(tmp_path),
                "--password",
                test_password,
                "--debug",  # to avoid calling del_or_keep_compressed()
            ]
        )

        # Check that main() returned successfully
        assert ret_code == 0, "main() should return 0 on success"

    # Check that no errors were printed
    stdout, stderr = capsys.readouterr()
    assert stderr == "", f"Unexpected error output: {stderr}"

    # Check that the output mentions the files were processed
    assert "" in stdout.lower(), f"{stdout=}"
