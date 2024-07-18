"""Code in this file was adapted from Andrea Bruschi's pylovepdf.

https://github.com/AndyCyberSec/pylovepdf
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, BinaryIO, Literal, TypedDict

import requests
from requests import Response


class ProcessResponse(TypedDict):
    """Type of ILovePDF.process() return value."""

    timer: str
    status: str
    download_filename: str
    filesize: int
    output_filesize: int
    output_filenumber: int
    output_extensions: list[str]


class ILovePDF:
    """Communicates with the iLovePDF API."""

    def __init__(self, public_key: str, *, debug: bool = False) -> None:
        """Creates a new iLovePDF object to interact with the API.

        Args:
            public_key (str): iLovePDF API key. Get yours by signing up at
                https://developer.ilovepdf.com/signup.
            debug (bool, optional): Whether to perform real API requests (consumes
                quota) or just report what would happen. Defaults to False.
        """
        self.public_key = public_key

        self.api_version = "v1"
        self.start_server = "api.ilovepdf.com"
        self.working_server = ""
        # Any resource can be called with a debug option. When true, iLovePDF won't
        # process the request but will output the parameters received by the server.
        self.debug = debug  # https://developer.ilovepdf.com/docs/api-reference#testing

        # header will contain authorization token to be sent with every task
        self.headers: dict[str, str] | None = None

        self.auth()

    def auth(self) -> None:
        """Get iLovePDF API session token."""
        payload = {"public_key": self.public_key}

        response = self._send_request("post", endpoint="auth", payload=payload)

        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    def get_quota(self) -> int:
        """Get the number of remaining files that can be processed by the API in the
        current billing cycle.

        Response has only one key: {'remaining_files': int}.
        """
        response = json.loads(self._send_request("get", "info").text)

        return response["remaining_files"]

    def _send_request(
        self,
        method: Literal["get", "post", "delete"],
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        files: dict[str, BinaryIO] | None = None,
        stream: bool = False,
    ) -> Response:
        # continue to use old server if task was already assigned one, else connect to
        # new server
        server = self.working_server or self.start_server
        payload = payload or {}

        url = f"https://{server}/{self.api_version}/{endpoint}"

        if self.debug:
            payload["debug"] = True

        response = getattr(requests, method)(
            url, data=payload, headers=self.headers, files=files, stream=stream
        )

        if not response.ok:
            raise ValueError(
                f"Error: {response.url} returned status code {response.status_code}, "
                f"reason: '{response.reason}'. Full response text is: '{response.text}'"
            )

        return response


class Task(ILovePDF):
    """Class for interacting with the iLovePDF request workflow.

    https://developer.ilovepdf.com/docs/api-reference#request-workflow
    """

    def __init__(
        self,
        public_key: str,
        tool: str,
        *,
        verbose: bool = False,
        password: str = "",
        **kwargs: Any,
    ) -> None:
        """Creates a new task object to interact with the API.

        Args:
            public_key (str): iLovePDF API key.
            tool (str): The desired API tool you wish to access. Possible values: merge,
                split, compress, pdfjpg, imagepdf, unlock, pagenumber, watermark, pdfa,
                officepdf, repair, rotate, protect, validatepdfa, htmlpdf, extract.
                pdf-compressor only supports 'compress'. Might change in the future.
                https://developer.ilovepdf.com/docs/api-reference#process.
            verbose (bool, optional): Whether to print progress messages while uploading
                and processing files. Defaults to False.
            password (str, optional): Password to open PDFs in case they have one.
                Defaults to "".
            **kwargs: Additional keyword arguments to pass to ILovePDF.__init__().
        """
        super().__init__(public_key, **kwargs)

        self.files: dict[str, str] = {}
        self._task_id = ""
        self._process_response: ProcessResponse | None = None

        self.verbose = verbose
        self.tool = tool
        self.password = password

        # API options https://developer.ilovepdf.com/docs/api-reference#process
        # placeholders like {app}, {n}, {filename} in output/packaged_filename will be
        # inserted by iLovePDF.
        # See https://developer.ilovepdf.com/docs/api-reference#output_filename
        self.process_params = {
            "tool": tool,
            "ignore_password": True,
            # important to keep {n} in front as sort order is used to match existing
            # to downloaded files
            "output_filename": "{n}-{filename}-{app}",
            "packaged_filename": "{app}ed-PDFs",
        }

        self.start()

    def start(self) -> None:
        """Initiate contact with iLovePDF API to get assigned a working server that will
        handle ensuing requests.
        """
        response = self._send_request("get", f"start/{self.tool}").json()

        if response:
            self.working_server = response["server"]

            self._task_id = response["task"]

        else:
            print(
                "Warning: Starting this task returned empty JSON response. "
                "Was likely already started."
            )

    def add_file(self, file_path: str) -> None:
        """Add a file to the task.

        Args:
            file_path (str): Path of file to be added.

        Raises:
            FileNotFoundError: If file does not exist.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"'{file_path}' does not exist")

        if file_path in self.files:
            print(f"Warning: File '{file_path}' was already added to this task.")

        self.files[file_path] = ""

    def upload(self) -> dict[str, str]:
        """Second step of the task is where the PDFs files to be processed are uploaded
        to iLovePDF servers.

        Returns:
            dict[str, str]: Map from local filenames to corresponding filenames on the
                server.
        """
        payload = {"task": self._task_id}

        for filename in self.files:
            with open(filename, "rb") as file:
                response = self._send_request(
                    "post", "upload", payload=payload, files={"file": file}
                ).json()

            # server_filename is the only key in the JSON response
            self.files[filename] = response["server_filename"]

        return self.files

    def process(self) -> ProcessResponse:
        """Uploads and then processes files added to this Task. Files will be processed
        in the same order as iterating over self.files.items().

        Returns:
            ProcessResponse: The post-processing JSON response.
        """
        if self.verbose:
            print("Uploading file(s)...")

        self.upload()

        payload = self.process_params.copy()
        payload["task"] = self._task_id

        for idx, (filename, server_filename) in enumerate(self.files.items()):
            payload[f"files[{idx}][filename]"] = filename
            payload[f"files[{idx}][server_filename]"] = server_filename
            payload[f"files[{idx}][password]"] = self.password

        response: ProcessResponse = self._send_request(
            "post", "process", payload=payload
        ).json()

        self._process_response = response
        n_files = response["output_filenumber"]

        if len(self.files) != response["output_filenumber"]:
            raise ValueError(
                f"Unexpected file count mismatch: task received {len(self.files)} files"
                f" for processing, but only {n_files} were downloaded from server."
            )

        if self.verbose:
            print(f"File(s) uploaded and processed!\n{response = }")

        return response

    def download(self, save_to_dir: str | None = None) -> str:
        """Download this task's output file(s) for the given task. Should not be called
        until after task.process(). In case of a single output file, it is saved to disk
        as a PDF. Multiple files are saved in a compressed ZIP folder.

        Raises:
            ValueError: If task.download() is called in absence of downloadable files,
                usually because task.process() wasn't called yet.

        Returns:
            str: Path to the newly downloaded file.
        """
        if not self._process_response:
            raise ValueError(
                "You called task.download() but there are no files to download"
            )

        endpoint = f"download/{self._task_id}"

        response = self._send_request("get", endpoint, stream=True)

        if save_to_dir is None:  # save_to_dir is None or ''
            save_to_dir = tempfile.mkdtemp()

        file_path = os.path.join(
            save_to_dir, self._process_response["download_filename"]
        )

        # create parent dir (we use download_filename instead of save_to_dir because it
        # may contain subdirs)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # response.content is PDF file or ZIP archive, either way, we save as binary
        with open(file_path, "wb") as file:
            file.write(response.content)

        return file_path

    def delete_current_task(self) -> None:
        """Delete the current task from iLovePDF servers.

        https://developer.ilovepdf.com/docs/api-reference#task
        """
        if not self._task_id:
            print("Warning: You're trying to delete a task that was never started")
            return

        self._send_request("delete", f"task/{self._task_id}")
        self._task_id = ""
        self._process_response = None


class Compress(Task):
    """Use the iLovePDF compression tool.

    Example:
        from pdf_compressor import Compress

        task = Compress("public_key")
        task.add_file("pdf_file")
        task.process()
        task.download("output/dir")
        task.delete_current_task()
    """

    def __init__(
        self, public_key: str, compression_level: str = "recommended", **kwargs: Any
    ) -> None:
        """Subclass of Task for using the iLovePDF compression tool.

        Args:
            public_key (str): iLovePDF public API key. Get yours by signing up at
                https://developer.ilovepdf.com/signup.
            compression_level (str, optional): How hard to squeeze the file size.
                'extreme' noticeably degrades image quality. Defaults to 'recommended'.
            **kwargs: Additional keyword arguments to pass to Task.__init__().
        """
        super().__init__(public_key, tool="compress", **kwargs)

        if compression_level not in (valid_levels := ("low", "recommended", "extreme")):
            raise ValueError(
                f"Invalid {compression_level=}, must be one of {valid_levels}"
            )

        self.process_params["compression_level"] = compression_level
