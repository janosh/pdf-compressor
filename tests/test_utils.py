from pdf_compressor.utils import sizeof_fmt, load_dotenv
from os.path import abspath, dirname, expanduser, getsize, isfile, splitext
import os


def test_sizeof_fmt():
    assert sizeof_fmt(123456) == "120.6 KB"

    assert sizeof_fmt(123456789, 3) == sizeof_fmt(123456789, prec=3) == "117.738 MB"


def test_load_env():
    f = open(".env", "a")
    f.write("APP_TEST=test")
    f.close()

    test_file = f"{dirname(__file__)}/.env"

    load_dotenv(filepath=test_file)

    assert os.getenv('APP_TEST') == 'test'
