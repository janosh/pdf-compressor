"""CLI and Python API for batch compressing PDFs with iLovePDF."""

from pdf_compressor._version import __version__
from pdf_compressor.ilovepdf import Compress, ILovePDF, Task
from pdf_compressor.main import DEFAULT_SUFFIX, compress, main
from pdf_compressor.utils import si_fmt
