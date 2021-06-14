from pdf_compressor.utils import sizeof_fmt


def test_sizeof_fmt():

    assert sizeof_fmt(123456) == "120.6 KB"

    assert sizeof_fmt(123456789, 3) == sizeof_fmt(123456789, prec=3) == "117.738 MB"
