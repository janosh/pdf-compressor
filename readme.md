<p align="center">
  <img src="https://raw.githubusercontent.com/janosh/pdf-compressor/main/assets/pdf-compressor.svg" alt="PDF Compressor" height=150>
</p>

<h3 align="center">

[![Tests](https://github.com/janosh/pdf-compressor/workflows/Tests/badge.svg)](https://github.com/janosh/pdf-compressor/actions)
[![crates.io](https://img.shields.io/crates/v/pdf-compressor?logo=rust)](https://crates.io/crates/pdf-compressor)
[![PyPI](https://img.shields.io/pypi/v/pdf-compressor?logo=pypi&logoColor=white)](https://pypi.org/project/pdf-compressor)
[![PyPI Downloads](https://img.shields.io/pypi/dm/pdf-compressor?logo=icloud&logoColor=white)](https://pypistats.org/packages/pdf-compressor)

</h3>

Command line PDF compression powered by the free [iLovePDF API](https://developer.ilovepdf.com).

**Why?** iLovePDF delivers the best PDF compression I've seen but their web UI doesn't support automation and can be cumbersome when dealing with many files.

## Installation

A single self-contained binary, installable either way:

```sh
pip install pdf-compressor  # prebuilt wheels, no Rust toolchain needed
cargo install pdf-compressor
```

## Usage

First, tell `pdf-compressor` your iLovePDF API key (if you haven't yet, get one by signing up at <https://developer.ilovepdf.com/signup>):

```sh
pdf-compressor --set-api-key project_public_7c854a9db0...
```

This saves the key to `~/.config/pdf-compressor/api-key` (`$XDG_CONFIG_HOME` is respected, `%APPDATA%` on Windows). Alternatively, set the `ILOVEPDF_PUBLIC_KEY` environment variable, which takes precedence.

Then start compressing!

```sh
pdf-compressor **/*.pdf           # writes foo-compressed.pdf next to each foo.pdf
pdf-compressor docs/ -o out/ -s "" # all PDFs under docs/ into out/ with original names
pdf-compressor -i report.pdf      # replace original (moved to the Trash on macOS)
```

All inputs are validated (output paths, stats file type, file existence) before anything is uploaded, so typos don't cost API quota. Uploads stream from disk, up to 4 at a time, with a progress line when run in a terminal.

## Options

| Flags                      | Default         | Description                                                                                                                         |
| -------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `-i/--inplace`             | off             | Replace originals with compressed PDFs. On macOS, originals go to the Trash. Conflicts with `--suffix` and `--outdir`.              |
| `-s/--suffix`              | `-compressed`   | Appended to compressed file names. May be empty together with `--outdir`.                                                          |
| `-o/--outdir`              | next to input   | Directory to write compressed PDFs into (created if missing).                                                                       |
| `--cl/--compression-level` | `recommended`   | One of `low`, `recommended`, `extreme`. `extreme` noticeably degrades embedded bitmap images.                                       |
| `--min-red/--min-size-reduction` | 10 with `-i`, else 0 | Only keep compressed files that are more than this many percent smaller.                                                  |
| `--password`               |                 | Password for protected PDFs. All inputs share it; PDFs with different passwords must be compressed separately.                     |
| `--set-api-key`            |                 | Save the iLovePDF public key. Can't be combined with other flags.                                                                   |
| `--report-quota`           |                 | Print the number of remaining files in the current billing cycle.                                                                   |
| `--write-stats-path`       |                 | Write per-file original vs compressed sizes and actions taken to a `.csv` file.                                          |
| `-v/--version`             |                 | Print the version.                                                                                                                  |

Set `ILOVEPDF_API_URL` to point at a different API endpoint (default `https://api.ilovepdf.com`).

## Migrating from v0.x (Python)

v1 is a Rust rewrite. The CLI keeps its flags with these exceptions:

- The Python API (`from pdf_compressor import compress, Compress`) is gone. Call the CLI via `subprocess`.
- `--verbose`, `--on-no-files` and `--on-bad-files` were removed: finding no PDFs exits 0 silently, passing a non-PDF file is an error, and results always show paths as given.
- `--set-api-key` writes to the user config dir instead of a `.env` file inside the installed package. Re-run it once after upgrading, or set `ILOVEPDF_PUBLIC_KEY`.
- `--debug` was removed.
- `--outdir` now actually places the compressed PDFs there (it previously only changed where the download was staged).
- `--write-stats-path` writes CSV only (no more JSON/Excel/HTML or pandas dependency), and other extensions are an error instead of silently writing nothing.
- `.pdfa`/`.pdfx` extensions are no longer picked up; only `.pdf` (any case).
- Errors exit with code 1 and a one-line message instead of a Python traceback.
- Local file paths are no longer sent to iLovePDF; uploads are named `0.pdf`, `1.pdf`, ...
