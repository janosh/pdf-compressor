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
        self.headers: Dict[str, str]

    def _send_request(
        self,
        method: str,
        endpoint: str,
        payload: Mapping[str, Union[str, bool]] = None,
        headers: Dict[str, str] = None,
        files: Dict[str, BinaryIO] = None,
        stream: bool = False,
    ) -> requests.Response:

        if method not in ["get", "post"]:
            raise ValueError(
                "Only 'post' and 'get' requests are used to interact with the iLovePDF API. "
                f"_send_request() got {method=}"
            )

        # continue to use old server if task was already assigned one, else connect to new one
        server = self.working_server or self.start_server

        url = f"https://{server}/{self.api_version}/{endpoint}"

        response = getattr(requests, method)(
            url, data=payload, headers=headers, files=files, stream=stream
        )

        response.raise_for_status()

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
                rotate, protect, pdfa, validatepdfa, htmlpdf, extract.
                See https://developer.ilovepdf.com/docs/api-reference#process.
            verbose (bool, optional): How much printing to do. Defaults to False.
            debug (bool, optional): Whether to perform real API requests (consumes quota) or
                just report what would happen. Defaults to False.
        """

        super().__init__(public_key)

        self.verbose = verbose

        self.files: Dict[str, str] = {}
        self.download_path = ""
        self.task = ""

        # Any resource can be called with a debug option. When true, iLovePDF won't process
        # the request but will output the parameters received by the server.
        self.debug = debug  # https://developer.ilovepdf.com/docs/api-reference#testing
        self.tool = tool

        # API options below (https://developer.ilovepdf.com/docs/api-reference#process)
        self.payload: Dict[str, Union[str, bool]] = {
            "task": self.task,
            "tool": tool,
            "ignore_errors": True,
            "ignore_password": True,
            "output_filename": "{filename}_{app}",
            "packaged_filename": "{app}_PDFs",
            "try_pdf_repair": True,
        }

        # available place holders in output/packaged_filename (will be inserted by iLovePDF):
        # {date} = current date
        # {n} = file number
        # {filename} = original filename
        # {app} = current processing tool (e.g. compress)
        # https://developer.ilovepdf.com/docs/api-reference#output_filename

        self.auth()
        self.start()

    def auth(self) -> None:
        """Get iLovePDF API session token."""

        payload = {"public_key": self.public_key}

        response = self._send_request(
            "post", endpoint="auth", payload=payload, headers=None
        )

        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    def start(self) -> None:

        response = self._send_request("get", f"start/{self.tool}", headers=self.headers)

        self.working_server = response.json()["server"]
        self.task = response.json()["task"]
        self.payload["task"] = self.task

    def add_file(self, file_path: str) -> None:

        if file_path in self.files:
            raise ValueError(f"File '{file_path}' was already added.")

        self.files[file_path] = ""

    def upload(self) -> None:

        for filename in self.files:

            with open(filename, "rb") as file:
                response = self._send_request(
                    "post",
                    "upload",
                    payload={"task": self.task},
                    headers=self.headers,
                    files={"file": file},
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

        response = self._send_request(
            "post", "process", payload=payload, headers=self.headers
        )

        if verbose:

            print("File uploaded! Below file stats:")

            print(response)

    def set_output_folder(self, path: str) -> None:

        os.makedirs(path, exist_ok=True)

        self.download_path = path

    def clean_filename(self, filename: str) -> str:

        return "_".join(filename.split("_")[1:])

    def download(self) -> Union[str, None]:

        if self.debug:
            return None

        if not len(self.files) > 0:
            print(
                "Warning: you called task.download() but there are no files to be downloaded"
            )
            return None

        endpoint, headers = f"download/{self.task}", self.headers

        response = self._send_request("get", endpoint, headers=headers, stream=True)

        if self.verbose:
            print("Downloading processed file(s)...")

        # content disposition is something like 'attachment; filename="some_file_compress.pdf"'
        # so split('"')[-2] should get us "some_file_compress.pdf"
        filename = response.headers["content-disposition"].split('"')[-2]

        with open(f"{self.download_path}/{filename}", "wb") as f:
            for chunk in response.iter_content(10):
                f.write(chunk)

        return filename

    def delete_current_task(self) -> None:

        self._send_request("post", f"task/{self.task}", None, headers=self.headers)

    def get_task_information(self) -> requests.Response:
        """Get task status information.

        If the task is TaskSuccess, TaskSuccessWithWarnings or TaskError it
        will also specify all files of the Task and their status one by one.

        Returns:
            Response: request response object
        """
        return self._send_request("get", f"task/{self.task}", headers=self.headers)


class Compress(Task):
    """Use the iLovePDF compression tool.

    Example:
        from pypdf_compress import Compress

        task = Compress('public_key')
        task.add_file('pdf_file')
        task.set_output_folder('output_directory')
        task.execute()
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
            compression_level (str, optional): How hard to try to squeeze the file size.
                'extreme' noticeably degrades image quality. Defaults to "recommended".
        """
        super().__init__(public_key, tool="compress", **kwargs)

        self.payload["compression_level"] = compression_level

        valid_lvls = ("low", "recommended", "extreme")
        assert (
            compression_level in valid_lvls
        ), f"Invalid {compression_level=}, must be one of {valid_lvls}"
