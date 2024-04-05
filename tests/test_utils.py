from __future__ import annotations

from pdf_compressor.utils import si_fmt


def test_si_fmt() -> None:
    assert si_fmt(123456) == "120.6K"

    assert si_fmt(12345678, fmt=">6.2f", sep=" ") == " 11.77 M"

    assert si_fmt(0.00123, fmt=".3g", binary=False) == "1.23m"

    assert si_fmt(0.00000123, fmt="5.1f", sep=" ") == "  1.3 μ"
