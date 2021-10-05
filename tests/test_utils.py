import zipfile

from pdf_compressor.utils import sizeof_fmt, load_dotenv, del_or_keep_compressed
from os.path import dirname, isfile
from zipfile import ZipFile
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


def test_del_or_keep_compressed():
    file = f"{dirname(__file__)}/dummy.pdf"
    file2 = f"{dirname(__file__)}/dummy2.pdf"
    archive = f"{dirname(__file__)}/dummy.zip"

    f = open(file, 'a')
    f.write('content')
    f.close()

    f = open(file2, 'a')
    f.write('content')
    f.close()

    with zipfile.ZipFile(archive, mode='w') as z:
        z.write(f"{dirname(__file__)}/dummy.pdf")
        z.write(f"{dirname(__file__)}/dummy2.pdf")

    del_or_keep_compressed([file, file2], archive, inplace=False, suffix='test')

    assert isfile(archive) is False

