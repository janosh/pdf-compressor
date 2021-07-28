import json
import os
from typing import Any, BinaryIO, Dict, Mapping, Union

import requests


class ILovePDF:
    """Communicates with the iLovePDF API."""

    def __init__(self, public_key: str) -> None:
        """
        Args:
            public_key (str): iLovePDF API key. Get yours by signing up at
                https://developer.ilovepdf.com/signup.
        """

        self.public_key = public_key

        self.api_version = "v1"
        self.start_server = "api.ilovepdf.com"
        self.working_server = ""

        # header will contain authorization token to be sent with every task
        self.headers: Union[Dict[str, str], None] = None

        self.auth()

    def auth(self) -> None:
        """Get iLovePDF API session token."""

        payload = {"public_key": self.public_key}

        response = self._send_request("post", endpoint="auth", payload=payload)

        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    def report_quota(self) -> int:
        response = self._send_request("get", "info")

        remaining_files = json.loads(response.text)["remaining_files"]

        print(f"Remaining files in this billing cycle: {remaining_files:,}")

        return remaining_files

    def _send_request(
        self,
        type: str,
        endpoint: str,
        payload: Mapping[str, Union[str, bool]] = None,
        files: Dict[str, BinaryIO] = None,
        stream: bool = False,
    ) -> requests.Response:

        if type not in ["get", "post"]:
            raise ValueError(
                f"iLovePDF API only accepts 'post' and 'get' requests, got {type=}"
            )

        # continue to use old server if task was already assigned one, else connect to new one
        server = self.working_server or self.start_server

        url = f"https://{server}/{self.api_version}/{endpoint}"

        response = getattr(requests, type)(
            url, data=payload, headers=self.headers, files=files, stream=stream
        )

        if not response.ok:
            raise ValueError(
                f"Error: {response.url} returned status code {response.status_code} with "
                f"reason '{response.reason}'. Full response text is: {response.text}."
            )

        return response


class Task(ILovePDF):
    """Class for interacting with the iLovePDF request workflow.

    https://developer.ilovepdf.com/docs/api-reference#request-workflow
    """

    def __init__(
        self, public_key: str, tool: str, verbose: bool = False, debug: bool = False
    ) -> None:
        """
        Args:
            public_key (str): iLovePDF API key.
            tool (str): The desired API tool you wish to access. Possible values: merge, split,
                compress, pdfjpg, imagepdf, unlock, pagenumber, watermark, officepdf, repair,
                rotate, protect, pdfa, validatepdfa, htmlpdf, extract. pdf-compressor only
                supports 'compress'.
                https://developer.ilovepdf.com/docs/api-reference#process.
            verbose (bool, optional): How much printing to do. Defaults to False.
            debug (bool, optional): Whether to perform real API requests (consumes quota) or
                just report what would happen. Defaults to False.
        """

        super().__init__(public_key)

        self.verbose = verbose

        self.files: Dict[str, str] = {}
        self.download_path = ""
        self._task = ""

        # Any resource can be called with a debug option. When true, iLovePDF won't process
        # the request but will output the parameters received by the server.
        self.debug = debug  # https://developer.ilovepdf.com/docs/api-reference#testing
        self.tool = tool

        # API options below (https://developer.ilovepdf.com/docs/api-reference#process)
        # available place holders in output/packaged_filename (will be inserted by iLovePDF):
        # {date} = current date
        # {n} = file number
        # {filename} = original filename
        # {app} = current processing tool (e.g. compress)
        # https://developer.ilovepdf.com/docs/api-reference#output_filename
        self.payload: Dict[str, Union[str, bool]] = {
            "task": self._task,
            "tool": tool,
            "ignore_errors": True,
            "ignore_password": True,
            "output_filename": "{filename}_{app}",
            "packaged_filename": "{app}_PDFs",
            "try_pdf_repair": True,
        }

        self.start()

    def start(self) -> None:
        """Initiate contact with iLovePDF API to get assigned a working server that will
        handle ensuing requests.
        """

        json = self._send_request("get", f"start/{self.tool}").json()

        if json:
            self.working_server = json["server"]

            self._task = self.payload["task"] = json["task"]

        else:
            print(
                "Warning: Starting this task returned empty JSON response. "
                "Was likely already started."
            )

    def add_file(self, file_path: str) -> None:

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"'{file_path}' does not exist")

        if file_path in self.files:
            print(f"Warning: File '{file_path}' was already added to this task.")

        self.files[file_path] = ""

    def upload(self) -> None:

        for filename in self.files:

            with open(filename, "rb") as file:
                response = self._send_request(
                    "post", "upload", payload={"task": self._task}, files={"file": file}
                )

            self.files[filename] = response.json()["server_filename"]

    def check_values(self, prop: str, prop_val_key: str) -> bool:

        value = getattr(self, prop)
        try:
            list_of_values = getattr(self, prop_val_key)
        except AttributeError:
            # for example self.mode does not have self.mode_values
            return True

        if value in list_of_values:
            return True
        else:
            return False

    def process(self, verbose: bool = False) -> None:

        if verbose:
            print("Uploading file...")

        self.upload()

        payload = {**self.payload}

        for idx, (filename, server_filename) in enumerate(self.files.items()):

            payload[f"files[{idx}][filename]"] = filename
            payload[f"files[{idx}][server_filename]"] = server_filename

        response = self._send_request("post", "process", payload=payload)

        if verbose:

            print("File uploaded! Below file stats:")

            print(response)

    def set_outdir(self, path: str) -> None:

        os.makedirs(path, exist_ok=True)

        self.download_path = path

    def download(self) -> Union[str, None]:

        if self.debug:
            return None

        if not len(self.files) > 0:
            print(
                "Warning: you called task.download() but there are no files to be downloaded"
            )
            return None

        endpoint = f"download/{self._task}"

        response = self._send_request("get", endpoint, stream=True)

        if self.verbose:
            print("Downloading processed file(s)...")

        # content disposition is something like 'attachment; filename="some_file_compress.pdf"'
        # so split('"')[-2] should get us "some_file_compress.pdf"
        filename = response.headers["content-disposition"].split('"')[-2]

        if not filename:
            raise ValueError(
                f"{filename=} after parsing {response.headers['content-disposition']=}, "
                "expected non-empty string"
            )

        with open(f"{self.download_path}/{filename}", "wb") as f:
            for chunk in response.iter_content(10):
                f.write(chunk)

        return filename

    def delete_current_task(self) -> None:

        self._send_request("post", f"task/{self._task}")


    def get_task_information(self) -> requests.Response:
        """Get task status information.

        If the task is TaskSuccess, TaskSuccessWithWarnings or TaskError it
        will also specify all files of the Task and their status one by one.

        Returns:
            Response: request response object
        """
        return self._send_request("get", f"task/{self._task}")


class Compress(Task):
    """Use the iLovePDF compression tool.

    Example:
        from pdf_compressor import Compress

        task = Compress('public_key')
        task.add_file('pdf_file')
        task.set_outdir('output_dir')
        task.process()
        task.download()
        task.delete_current_task()
    """

    def __init__(
        self, public_key: str, compression_level: str = "recommended", **kwargs: Any
    ) -> None:
        """
        Args:
            public_key (str): iLovePDF public API key. Get yours by signing up at
                https://developer.ilovepdf.com/signup.
            compression_level (str, optional): How hard to squeeze the file size.
                'extreme' noticeably degrades image quality. Defaults to 'recommended'.
        """
        super().__init__(public_key, tool="compress", **kwargs)

        assert compression_level in (
            valid_levels := ("low", "recommended", "extreme")
        ), f"Invalid {compression_level=}, must be one of {valid_levels}"

        self.payload["compression_level"] = compression_level
