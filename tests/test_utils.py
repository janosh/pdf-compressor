from pdf_compressor.utils import sizeof_fmt, load_dotenv
from os.path import dirname
import os


def file_test_creator(filename: str, content: str):
    f = open(filename, 'a')
    f.write(content)
    f.close()


def file_remove(filename: str):
    os.remove(filename)


def test_sizeof_fmt():
    assert sizeof_fmt(123456) == "120.6 KB"

    assert sizeof_fmt(123456789, 3) == sizeof_fmt(123456789, prec=3) == "117.738 MB"


def test_load_env():
    test_file = f"{dirname(__file__)}/.env.test"

    file_test_creator(test_file, "APP_TEST=test")

    load_dotenv(filepath=test_file)

    file_remove(test_file)

    assert os.getenv('APP_TEST') == 'test'


def test_load_commented_env():
    test_file = f"{dirname(__file__)}/.env.test_commented"

    file_test_creator(test_file, "#APP_TEST=test")

    load_dotenv(filepath=test_file)

    file_remove(test_file)

    assert os.getenv('COMMENT') is None


def test_load_empty_env():
    test_file = f"{dirname(__file__)}/.env.empty"

    file_test_creator(test_file, "")

    load_dotenv(filepath=test_file)

    file_remove(test_file)

    assert os.getenv('APP_EMPTY') is None
