//! Input discovery, output placement and size bookkeeping around `Client::compress`.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use crate::api::{Client, CompressionLevel};
use crate::error::{Error, Result, io_err};

/// Where compressed PDFs go.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Output {
    /// Replace originals, moving them to the system trash first.
    InPlace,
    /// Write `{stem}{suffix}.pdf` into `dir`, or next to each original if `dir` is `None`.
    Copy { dir: Option<PathBuf>, suffix: String },
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
pub enum Action {
    ReplacedOriginal,
    SavedAs(PathBuf),
    /// Compression didn't beat `min_size_reduction`, nothing was written.
    KeptOriginal,
    /// Writing the compressed file failed. The original is untouched unless the message
    /// says it was moved to the trash (`--inplace`).
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
/// are searched recursively for `*.pdf`, skipping hidden entries (e.g. macOS `._*` resource
/// forks, `.git`) and symlinked directories.
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
    path.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("pdf"))
}

fn walk_pdfs(dir: &Path, add: &mut impl FnMut(PathBuf) -> Result<()>) -> Result<()> {
    for entry in fs::read_dir(dir).map_err(io_err(dir))? {
        let entry = entry.map_err(io_err(dir))?;
        if entry.file_name().as_encoded_bytes().starts_with(b".") {
            continue;
        }
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

/// Where each PDF's compressed version would be written. Creates the output dir if needed.
/// Errors (before any API request) if an output would overwrite any input, two outputs
/// collide, or the suffix is a path.
pub fn output_paths(pdfs: &[PathBuf], output: &Output) -> Result<Vec<PathBuf>> {
    let Output::Copy { dir, suffix } = output else {
        return Ok(pdfs.to_vec());
    };
    if suffix.chars().any(std::path::is_separator) {
        return Err(Error::Input(format!(
            "suffix must not contain path separators, got {suffix:?}, use --outdir instead"
        )));
    }
    // create first so `normalize` can canonicalize paths like `new/../d` reliably
    if let Some(dir) = dir {
        fs::create_dir_all(dir).map_err(io_err(dir))?;
    }
    let inputs: HashSet<PathBuf> = pdfs.iter().map(|pdf| normalize(pdf)).collect::<Result<_>>()?;
    let mut dests = HashSet::new();
    pdfs.iter()
        .map(|pdf| {
            let stem = pdf.file_stem().unwrap_or_default().to_string_lossy();
            let ext = pdf.extension().unwrap_or_default().to_string_lossy();
            let parent = pdf.parent().unwrap_or(Path::new(""));
            let dest = dir.as_deref().unwrap_or(parent).join(format!("{stem}{suffix}.{ext}"));
            let key = normalize(&dest)?;
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

// Comparison key for a file that may not exist yet: canonical parent + file name, case-folded
// where the default file systems are case-insensitive (macOS, Windows).
fn normalize(path: &Path) -> Result<PathBuf> {
    let parent = path.parent().filter(|dir| !dir.as_os_str().is_empty()).unwrap_or(Path::new("."));
    let key =
        parent.canonicalize().map_err(io_err(parent))?.join(path.file_name().unwrap_or_default());
    if cfg!(any(target_os = "macos", windows)) {
        return Ok(PathBuf::from(key.to_string_lossy().to_lowercase()));
    }
    Ok(key)
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
    let tmp = dest.with_file_name(format!(".{name}.tmp"));
    let staged = fs::write(&tmp, bytes).map_err(io_err(&tmp)).and_then(|()| {
        if in_place {
            // keep the original's permissions, e.g. a private 0600 document
            let perms = fs::metadata(pdf).map_err(io_err(pdf))?.permissions();
            fs::set_permissions(&tmp, perms).map_err(io_err(&tmp))?;
            move_to_trash(pdf)?;
        }
        Ok(())
    });
    staged.inspect_err(|_| {
        let _ = fs::remove_file(&tmp); // our own temp file, may not exist
    })?;
    fs::rename(&tmp, dest).map_err(|err| {
        if in_place {
            // don't delete the only remaining copy of the compressed PDF
            return Error::Input(format!(
                "original moved to the trash, but moving compressed PDF {} into place failed: {err}",
                tmp.display()
            ));
        }
        let _ = fs::remove_file(&tmp);
        io_err(dest)(err)
    })
}

/// Move a file to the system trash (macOS Trash, Windows Recycle Bin, freedesktop trash).
fn move_to_trash(path: &Path) -> Result<()> {
    #[allow(unused_mut)] // only mutated on macOS
    let mut trash = trash::TrashContext::default();
    // NSFileManager instead of the default Finder scripting: no permission prompt or sound,
    // which suits a CLI (but Finder may not offer "Put Back")
    #[cfg(target_os = "macos")]
    trash::macos::TrashContextExtMacos::set_delete_method(
        &mut trash,
        trash::macos::DeleteMethod::NsFileManager,
    );
    trash.delete(path).map_err(|err| {
        Error::Input(format!("moving {} to the trash failed: {err}", path.display()))
    })
}

/// Check `path` is a `.csv` file in an existing dir. Call before compressing so a typo
/// doesn't fail after the API work is done.
pub fn check_stats_path(path: &Path) -> Result<()> {
    let ext = path.extension().unwrap_or_default();
    if !ext.eq_ignore_ascii_case("csv") {
        return Err(Error::Input(format!("stats path must end in .csv, got {}", path.display())));
    }
    match path.parent() {
        Some(dir) if !dir.as_os_str().is_empty() && !dir.is_dir() => {
            Err(Error::Input(format!("stats dir {} does not exist", dir.display())))
        }
        _ => Ok(()),
    }
}

/// Write per-file stats as CSV.
pub fn write_stats(stats: &[FileStats], path: &Path) -> Result<()> {
    check_stats_path(path)?;
    let quote = |field: &str| format!("\"{}\"", field.replace('"', "\"\""));
    let mut csv = String::from(
        "file,original size (B),compressed size (B),size reduction (B),size reduction (%),action\n",
    );
    for stat in stats {
        let action = match &stat.action {
            Action::ReplacedOriginal => "replaced original".to_owned(),
            Action::SavedAs(dest) => format!("saved as {}", dest.display()),
            Action::KeptOriginal => "kept original".to_owned(),
            Action::Failed(err) => format!("failed: {err}"),
        };
        csv += &format!(
            "{},{},{},{},{:.4},{}\n",
            quote(&stat.path.display().to_string()),
            stat.original_size,
            stat.compressed_size,
            stat.reduction(),
            stat.reduction_ratio() * 100.0,
            quote(&action)
        );
    }
    fs::write(path, csv).map_err(io_err(path))
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
}
