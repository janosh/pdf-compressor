<p align="center">
  <img src="https://raw.githubusercontent.com/janosh/pdf-compressor/main/assets/pdf-compressor.svg" alt="PDF Compressor" height=150>
</p>

<h3 align="center">

[![Tests](https://github.com/janosh/pdf-compressor/workflows/Tests/badge.svg)](https://github.com/janosh/pdf-compressor/actions)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/janosh/pdf-compressor/main.svg)](https://results.pre-commit.ci/latest/github/janosh/pdf-compressor/main)
[![PyPI](https://img.shields.io/pypi/v/pdf-compressor?logo=pypi)](https://pypi.org/project/pdf-compressor)
[![Requires Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue.svg?logo=python)](https://python.org/downloads)
[![PyPI Downloads](https://img.shields.io/pypi/dm/pdf-compressor)](https://pypistats.org/packages/pdf-compressor)

</h3>

Command line PDF compression powered by the free [iLovePDF API](https://developer.ilovepdf.com).

**Why?** iLovePDF delivers the best PDF compression I've seen but their web UI doesn't support automation and can be cumbersome when dealing with many files.

## Installation

```sh
pip install pdf-compressor
```

## Usage

First, tell `pdf-compressor` your iLovePDF API key (if you haven't yet, get one by signing up at <https://developer.ilovepdf.com/signup>):

```sh
pdf-compressor --set-api-key project_public_7c854a9db0...
```

Then start compressing!

```sh
pdf-compressor **/*.pdf
```

## Options

| Flags                      | Default         | Description                                                                                                                                                                                                                 |
| -------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-i/--inplace`             | `False`         | Whether to compress PDFs in place.                                                                                                                                                                                          |
| `-s/--suffix`              | `'-compressed'` | String to append to the filename of compressed PDFs. Mutually exclusive with `--inplace`.                                                                                                                                   |
| `--cl/--compression-level` | `'recommended'` | How hard to squeeze the file size. One of `'low'`, `'recommended'`, `'extreme'`. `'extreme'` noticeably degrades quality of embedded bitmap images.                                                                         |
| `--set-api-key`            |                 | Set the public key needed to authenticate with the iLovePDF API. You only need to do this once to start using.                                                                                                              |
| `--report-quota`           |                 | Report the number of remaining file operations in the current billing cycle for the stored iLovePDF API key.                                                                                                                |
| `--debug`                  | `False`         | When true, iLovePDF won't process the request but only reports the parameters that would have been sent to the server.                                                                                                      |
| `--verbose`                | `False`         | When true, progress will be reported while tasks are running.                                                                                                                                                               |
| `-v/--version`             |                 | Get the version number of `pdf-compressor`.                                                                                                                                                                                 |
| `--on-no-pdfs`             | `'ignore'`      | What to do when no input PDFs received. One of `'ignore'` or `'error'`, former exits 0, latter raises `ValueError`. Can be useful when using `pdf-compressor` in shell scripts.                                             |
| `--on-bad-files`           | `'error'`       | How to behave when receiving input files that don't appear to be PDFs. One of `'error'`, `'warn'`, `'ignore'`. Error type will be `ValueError`. Code used to check if something is PDF: `filepath.lower().endswith(".pdf")` |
