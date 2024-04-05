from importlib.metadata import PackageNotFoundError, version

from pdf_compressor.ilovepdf import Compress, ILovePDF, Task
from pdf_compressor.main import DEFAULT_SUFFIX, compress, main
from pdf_compressor.utils import si_fmt

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    pass  # package not installed
