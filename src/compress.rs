//! Input discovery, output placement and size bookkeeping around `Client::compress`.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use crate::api::{Client, CompressionLevel};
use crate::error::{Error, Result, io_err};

/// Where compressed PDFs go.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Output {
    /// Replace originals (moved to the Trash on macOS, overwritten elsewhere).
    InPlace,
    /// Write `{stem}{suffix}.pdf` into `dir`, or next to each original if `dir` is `None`.
    Copy {
        dir: Option<PathBuf>,
        suffix: String,
    },
}

#[derive(Clone, Debug)]
pub struct Options {
    pub level: CompressionLevel,
    pub output: Output,
    /// Keep a compressed file only if it's more than this many percent smaller.
    pub min_size_reduction: u8,
    /// Password for protected PDFs, shared by all inputs.
    pub password: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum Action {
    ReplacedOriginal,
    SavedAs(PathBuf),
    /// Compression didn't beat `min_size_reduction`, nothing was written.
    KeptOriginal,
    /// Writing the compressed file failed. The original is untouched unless the message
    /// says it was moved to the Trash.
    Failed(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FileStats {
    pub path: PathBuf,
    pub original_size: u64,
    pub compressed_size: u64,
    pub action: Action,
}

impl FileStats {
    /// Bytes saved (negative if iLovePDF made the file bigger).
    pub fn reduction(&self) -> i64 {
        self.original_size as i64 - self.compressed_size as i64
    }

    /// Fraction of the original size saved, in [-inf, 1].
    pub fn reduction_ratio(&self) -> f64 {
        self.reduction() as f64 / self.original_size.max(1) as f64
    }
}

/// Expand `paths` into a sorted list of PDFs, deduplicated by canonical path. Directories
/// are searched recursively for `*.pdf` without following symlinked directories.
/// Explicitly passed non-PDF files are returned separately. Missing paths are an error.
pub fn collect_pdfs(paths: &[PathBuf]) -> Result<(Vec<PathBuf>, Vec<PathBuf>)> {
    let mut seen = HashSet::new();
    let mut pdfs = Vec::new();
    let mut others = Vec::new();
    let mut add = |path: PathBuf| -> Result<()> {
        if seen.insert(path.canonicalize().map_err(io_err(&path))?) {
            pdfs.push(path);
        }
        Ok(())
    };
    for path in paths {
        if !path.exists() {
            return Err(Error::Input(format!("{} does not exist", path.display())));
        }
        if path.is_dir() {
            walk_pdfs(path, &mut add)?;
        } else if is_pdf(path) {
            add(path.clone())?;
        } else if !others.contains(path) {
            others.push(path.clone());
        }
    }
    pdfs.sort();
    others.sort();
    Ok((pdfs, others))
}

fn is_pdf(path: &Path) -> bool {
    path.extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("pdf"))
}

fn walk_pdfs(dir: &Path, add: &mut impl FnMut(PathBuf) -> Result<()>) -> Result<()> {
    for entry in fs::read_dir(dir).map_err(io_err(dir))? {
        let entry = entry.map_err(io_err(dir))?;
        let path = entry.path();
        // file_type() doesn't follow symlinks: recursing only into real dirs avoids cycles
        if entry.file_type().map_err(io_err(&path))?.is_dir() {
            walk_pdfs(&path, add)?;
        } else if is_pdf(&path) && path.is_file() {
            add(path)?;
        }
    }
    Ok(())
}

/// Where each PDF's compressed version would be written. Errors (before any API request)
/// if an output would overwrite any input, two outputs collide, or the suffix is a path.
pub fn output_paths(pdfs: &[PathBuf], output: &Output) -> Result<Vec<PathBuf>> {
    let Output::Copy { dir, suffix } = output else {
        return Ok(pdfs.to_vec());
    };
    if suffix.chars().any(std::path::is_separator) {
        return Err(Error::Input(format!(
            "suffix must not contain path separators, got {suffix:?}, use --outdir instead"
        )));
    }
    if let Some(dir) = dir
        && dir.is_file()
    {
        return Err(Error::Input(format!(
            "output dir {} is a file",
            dir.display()
        )));
    }
    let inputs: HashSet<PathBuf> = pdfs
        .iter()
        .map(|pdf| pdf.canonicalize().map_err(io_err(pdf)))
        .collect::<Result<_>>()?;
    let mut dests = HashSet::new();
    pdfs.iter()
        .map(|pdf| {
            let stem = pdf.file_stem().unwrap_or_default().to_string_lossy();
            let ext = pdf.extension().unwrap_or_default().to_string_lossy();
            let parent = pdf.parent().unwrap_or(Path::new(""));
            let dest = dir
                .as_deref()
                .unwrap_or(parent)
                .join(format!("{stem}{suffix}.{ext}"));
            let key = normalize(&dest);
            if inputs.contains(&key) {
                return Err(Error::Input(format!(
                    "output {} would overwrite an input PDF: change --suffix or --outdir, or \
                     compress in place",
                    dest.display()
                )));
            }
            if !dests.insert(key) {
                return Err(Error::Input(format!(
                    "multiple inputs would be written to {}: compress them separately or \
                     without --outdir",
                    dest.display()
                )));
            }
            Ok(dest)
        })
        .collect()
}

// Canonical form of a possibly not-yet-existing file: canonicalize the parent if it exists.
fn normalize(path: &Path) -> PathBuf {
    let parent = match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    };
    match (parent.canonicalize(), path.file_name()) {
        (Ok(parent), Some(name)) => parent.join(name),
        _ => path.to_path_buf(),
    }
}

/// Compress `pdfs` and write results per `opts.output`. Validates outputs before using any
/// API quota. Returns one `FileStats` per input, in input order. Failing to write one
/// output doesn't stop the others: it's recorded as `Action::Failed`.
pub fn compress_files(client: &Client, pdfs: &[PathBuf], opts: &Options) -> Result<Vec<FileStats>> {
    let dests = output_paths(pdfs, &opts.output)?;
    let original_sizes = pdfs
        .iter()
        .map(|pdf| Ok(fs::metadata(pdf).map_err(io_err(pdf))?.len()))
        .collect::<Result<Vec<_>>>()?;
    if let Output::Copy { dir: Some(dir), .. } = &opts.output {
        fs::create_dir_all(dir).map_err(io_err(dir))?;
    }
    let compressed = client.compress(pdfs, opts.level, opts.password.as_deref())?;

    let in_place = opts.output == Output::InPlace;
    let stats = pdfs
        .iter()
        .zip(dests)
        .zip(original_sizes)
        .zip(compressed)
        .map(|(((pdf, dest), original_size), bytes)| {
            let mut stats = FileStats {
                path: pdf.clone(),
                original_size,
                compressed_size: bytes.len() as u64,
                action: Action::KeptOriginal,
            };
            if worth_keeping(&stats, opts.min_size_reduction) {
                stats.action = match place(pdf, &dest, &bytes, in_place) {
                    Ok(()) if in_place => Action::ReplacedOriginal,
                    Ok(()) => Action::SavedAs(dest),
                    Err(err) => Action::Failed(err.to_string()),
                };
            }
            stats
        })
        .collect();
    Ok(stats)
}

// Strictly more than `min_pct` percent smaller, in exact integer arithmetic.
fn worth_keeping(stats: &FileStats, min_pct: u8) -> bool {
    let reduction = i128::from(stats.reduction());
    reduction > 0 && reduction * 100 > i128::from(min_pct) * i128::from(stats.original_size)
}

// Write `bytes` to `dest` atomically: a temp file in the same dir, renamed over `dest`.
// So a failed write never truncates an existing file.
fn place(pdf: &Path, dest: &Path, bytes: &[u8], in_place: bool) -> Result<()> {
    let name = dest.file_name().unwrap_or_default().to_string_lossy();
    let tmp = dest.with_file_name(format!(".{name}.pdf-compressor-tmp"));
    let result = fs::write(&tmp, bytes).map_err(io_err(&tmp)).and_then(|()| {
        if in_place && cfg!(target_os = "macos") {
            move_to_trash(pdf)?;
        }
        fs::rename(&tmp, dest).map_err(io_err(dest))
    });
    if result.is_err() {
        let _ = fs::remove_file(&tmp); // our own temp file, may not exist
    }
    result
}

/// Move a file into ~/.Trash, appending a counter if the name is taken.
fn move_to_trash(path: &Path) -> Result<()> {
    let home = std::env::var_os("HOME").ok_or_else(|| Error::Input("HOME not set".into()))?;
    let trash = PathBuf::from(home).join(".Trash");
    let stem = path.file_stem().unwrap_or_default().to_string_lossy();
    let ext = path.extension().unwrap_or_default().to_string_lossy();
    let mut target = trash.join(path.file_name().unwrap_or_default());
    let mut counter = 1;
    while target.exists() {
        counter += 1;
        target = trash.join(format!("{stem} {counter}.{ext}"));
    }
    // rename fails across volumes; copy + remove handles PDFs on external drives
    if fs::rename(path, &target).is_err() {
        fs::copy(path, &target).map_err(io_err(&target))?;
        fs::remove_file(path).map_err(io_err(path))?;
    }
    Ok(())
}

/// File format of `write_stats` output.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StatsFormat {
    Csv,
    Json,
}

impl StatsFormat {
    /// Pick the format from `path`'s extension and check its directory exists. Call before
    /// compressing so a typo doesn't waste API quota.
    pub fn from_path(path: &Path) -> Result<Self> {
        let ext = path.extension().unwrap_or_default().to_string_lossy();
        let format = match ext.to_lowercase().as_str() {
            "csv" => Self::Csv,
            "json" => Self::Json,
            _ => {
                return Err(Error::Input(format!(
                    "stats path must end in .csv or .json, got {}",
                    path.display()
                )));
            }
        };
        match path.parent() {
            Some(dir) if !dir.as_os_str().is_empty() && !dir.is_dir() => Err(Error::Input(
                format!("stats dir {} does not exist", dir.display()),
            )),
            _ => Ok(format),
        }
    }
}

/// Write stats as CSV or JSON, chosen by `path`'s extension.
pub fn write_stats(stats: &[FileStats], path: &Path) -> Result<()> {
    let row = |stat: &FileStats| {
        let action = match &stat.action {
            Action::ReplacedOriginal => "replaced original".to_owned(),
            Action::SavedAs(dest) => format!("saved as {}", dest.display()),
            Action::KeptOriginal => "kept original".to_owned(),
            Action::Failed(err) => format!("failed: {err}"),
        };
        (stat.path.display().to_string(), action)
    };
    let content = match StatsFormat::from_path(path)? {
        StatsFormat::Csv => {
            let quote = |field: &str| format!("\"{}\"", field.replace('"', "\"\""));
            let mut csv = String::from(
                "file,original size (B),compressed size (B),size reduction (B),size reduction (%),action\n",
            );
            for stat in stats {
                let (file, action) = row(stat);
                csv += &format!(
                    "{},{},{},{},{:.4},{}\n",
                    quote(&file),
                    stat.original_size,
                    stat.compressed_size,
                    stat.reduction(),
                    stat.reduction_ratio() * 100.0,
                    quote(&action)
                );
            }
            csv
        }
        StatsFormat::Json => {
            let rows: Vec<_> = stats
                .iter()
                .map(|stat| {
                    let (file, action) = row(stat);
                    serde_json::json!({
                        "file": file,
                        "original_size": stat.original_size,
                        "compressed_size": stat.compressed_size,
                        "size_reduction": stat.reduction(),
                        "size_reduction_pct": stat.reduction_ratio() * 100.0,
                        "action": action,
                    })
                })
                .collect();
            serde_json::to_string_pretty(&rows).expect("JSON values always serialize") + "\n"
        }
    };
    fs::write(path, content).map_err(io_err(path))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stat(original_size: u64, compressed_size: u64) -> FileStats {
        FileStats {
            path: PathBuf::new(),
            original_size,
            compressed_size,
            action: Action::KeptOriginal,
        }
    }

    #[test]
    fn reduction_ratio_handles_growth_and_empty() {
        assert_eq!(stat(100, 75).reduction_ratio(), 0.25);
        assert_eq!(stat(100, 150).reduction(), -50);
        assert_eq!(stat(0, 0).reduction_ratio(), 0.0);
    }

    #[test]
    fn worth_keeping_is_strictly_more_than_threshold() {
        for (orig, comp, min_pct, expected) in [
            (10_000, 9_000, 10, false), // exactly 10% is not more than 10%
            (10_000, 8_999, 10, true),
            (25, 18, 28, false), // exactly 28%, f64 math got this wrong
            (100, 100, 0, false),
            (100, 99, 0, true),
            (100, 150, 0, false),
            (0, 0, 0, false),
            (u64::MAX / 2, 1, 100, false),
            (u64::MAX / 2, 1, 99, true),
        ] {
            assert_eq!(
                worth_keeping(&stat(orig, comp), min_pct),
                expected,
                "{orig} -> {comp} at {min_pct}%"
            );
        }
    }

    #[test]
    fn stats_format_by_extension() {
        assert_eq!(
            StatsFormat::from_path(Path::new("stats.CSV")).unwrap(),
            StatsFormat::Csv
        );
        assert_eq!(
            StatsFormat::from_path(Path::new("stats.json")).unwrap(),
            StatsFormat::Json
        );
        for (bad, msg) in [
            ("stats.xlsx", "must end in .csv or .json"),
            ("stats", "must end in .csv or .json"),
            (
                "no/such/dir/stats.csv",
                "stats dir no/such/dir does not exist",
            ),
        ] {
            let err = StatsFormat::from_path(Path::new(bad))
                .unwrap_err()
                .to_string();
            assert!(err.contains(msg), "{bad}: {err}");
        }
    }
}
