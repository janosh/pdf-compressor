//! Batch-compress PDFs with the iLovePDF API: https://developer.ilovepdf.com

mod api;
mod compress;
mod config;
mod error;

use std::fmt::Display;
use std::io::IsTerminal;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{ArgAction, Parser};

use crate::api::{Client, CompressionLevel, DEFAULT_API_URL};
use crate::compress::{Action, FileStats, Options, Output};
use crate::error::{Error, Result};

/// Environment variable overriding the iLovePDF API base URL.
const API_URL_ENV: &str = "ILOVEPDF_API_URL";

/// Batch compress PDFs on the command line. Powered by iLovePDF.com.
#[derive(Parser)]
#[command(version, about, disable_version_flag = true)]
struct Cli {
    /// PDF files or directories (searched recursively for *.pdf) to compress.
    paths: Vec<PathBuf>,

    /// Save the iLovePDF public key (get one at https://developer.ilovepdf.com/signup).
    #[arg(long, value_name = "KEY", exclusive = true)]
    set_api_key: Option<String>,

    /// Report how many files the API key can still process this billing cycle.
    #[arg(long, exclusive = true)]
    report_quota: bool,

    /// Replace originals with compressed PDFs. Originals go to the system trash.
    #[arg(short, long, conflicts_with_all = ["suffix", "outdir"])]
    inplace: bool,

    /// Appended to compressed file names. May be empty with --outdir.
    #[arg(short, long, default_value = "-compressed")]
    suffix: String,

    /// Write compressed PDFs here instead of next to the originals.
    #[arg(short, long, value_name = "DIR")]
    outdir: Option<PathBuf>,

    /// How hard to squeeze. 'extreme' noticeably degrades embedded images.
    #[arg(long, visible_alias = "cl", value_enum, default_value_t)]
    compression_level: CompressionLevel,

    /// Keep compressed files only if more than this many percent smaller. [default: 10 with --inplace, else 0]
    #[arg(long, visible_alias = "min-red", value_name = "0-100",
          value_parser = clap::value_parser!(u8).range(0..=100))]
    min_size_reduction: Option<u8>,

    /// Password for protected PDFs. All inputs share it.
    #[arg(long)]
    password: Option<String>,

    /// Write per-file size stats to a .csv file.
    #[arg(long, value_name = "PATH")]
    write_stats_path: Option<PathBuf>,

    /// Print version.
    #[arg(short = 'v', long, action = ArgAction::Version)]
    version: Option<bool>,
}

fn main() -> ExitCode {
    let result = run(Cli::parse());
    status(""); // clear any progress line before final output
    match result {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::FAILURE,
        Err(err) => {
            eprintln!("error: {err}");
            ExitCode::FAILURE
        }
    }
}

/// Show a transient progress line on stderr, only when it's a terminal so pipes and logs
/// stay clean. An empty message clears the line.
pub(crate) fn status(msg: impl Display) {
    if std::io::stderr().is_terminal() {
        eprint!("\r\x1b[2K{msg}");
    }
}

fn client() -> Result<Client> {
    let api_url = std::env::var(API_URL_ENV).ok().filter(|url| !url.trim().is_empty());
    Client::new(&config::api_key()?, api_url.as_deref().unwrap_or(DEFAULT_API_URL))
}

// Returns whether every file was handled successfully.
fn run(cli: Cli) -> Result<bool> {
    if let Some(key) = &cli.set_api_key {
        let path = config::set_api_key(key)?;
        println!("API key saved to {}", path.display());
        return Ok(true);
    }
    if cli.report_quota {
        let credits = client()?.remaining_credits()?;
        println!("Remaining files in this billing cycle: {credits}");
        return Ok(true);
    }

    let (pdfs, not_pdfs) = compress::collect_pdfs(&cli.paths)?;
    if !not_pdfs.is_empty() {
        let list: Vec<_> = not_pdfs.iter().map(|path| path.display().to_string()).collect();
        let msg = format!("{} input files aren't PDFs: {}", list.len(), list.join(", "));
        return Err(Error::Input(msg));
    }
    if pdfs.is_empty() {
        return Ok(true);
    }

    let opts = Options {
        level: cli.compression_level,
        min_size_reduction: cli.min_size_reduction.unwrap_or(if cli.inplace { 10 } else { 0 }),
        output: if cli.inplace {
            Output::InPlace
        } else {
            Output::Copy { dir: cli.outdir, suffix: cli.suffix }
        },
        password: cli.password,
    };
    // fail on bad inputs before any network request
    compress::output_paths(&pdfs, &opts.output)?;
    if let Some(path) = &cli.write_stats_path {
        compress::check_stats_path(path)?;
    }

    let stats = compress::compress_files(&client()?, &pdfs, &opts)?;
    status("");
    for (idx, stat) in stats.iter().enumerate() {
        let counter = match stats.len() {
            1 => String::new(),
            n_files => format!("[{}/{n_files}] ", idx + 1),
        };
        let line = report_line(stat, opts.min_size_reduction);
        if matches!(stat.action, Action::Failed(_)) {
            eprintln!("{counter}{line}");
        } else {
            println!("{counter}{line}");
        }
    }
    let saved: Vec<_> = stats
        .iter()
        .filter(|stat| matches!(stat.action, Action::ReplacedOriginal | Action::SavedAs(_)))
        .collect();
    let total_orig: u64 = saved.iter().map(|stat| stat.original_size).sum();
    let total_comp: u64 = saved.iter().map(|stat| stat.compressed_size).sum();
    if saved.len() > 1 {
        println!(
            "Saved {} in total across {} files: {} -> {}",
            human_size(total_orig - total_comp),
            saved.len(),
            human_size(total_orig),
            human_size(total_comp)
        );
    }

    if let Some(path) = &cli.write_stats_path {
        compress::write_stats(&stats, path)?;
    }
    Ok(!stats.iter().any(|stat| matches!(stat.action, Action::Failed(_))))
}

fn report_line(stat: &FileStats, min_pct: u8) -> String {
    let sizes = format!(
        "'{}': {} -> {}",
        stat.path.display(),
        human_size(stat.original_size),
        human_size(stat.compressed_size)
    );
    let pct = stat.reduction_ratio() * 100.0;
    let saved = human_size(stat.reduction().unsigned_abs());
    match &stat.action {
        Action::KeptOriginal if stat.reduction() <= 0 => {
            format!("{sizes}, not smaller, kept original.")
        }
        Action::KeptOriginal => {
            format!("{sizes}, only {pct:.1}% smaller (needs more than {min_pct}%), kept original.")
        }
        Action::SavedAs(dest) => {
            format!("{sizes}, {saved} = {pct:.0}% smaller. Saved as {}.", dest.display())
        }
        Action::ReplacedOriginal => {
            format!("{sizes}, {saved} = {pct:.0}% smaller. Original moved to the trash.")
        }
        Action::Failed(err) => format!("{sizes}, failed to write: {err}"),
    }
}

/// Format a byte count with binary prefixes, e.g. 13312 -> "13.0KB".
fn human_size(bytes: u64) -> String {
    let mut val = bytes as f64;
    for unit in ["B", "KB", "MB", "GB"] {
        if val < 1024.0 {
            return format!("{val:.1}{unit}");
        }
        val /= 1024.0;
    }
    format!("{val:.1}TB")
}

#[cfg(test)]
mod tests {
    use super::human_size;

    #[test]
    fn human_size_picks_binary_unit() {
        for (bytes, expected) in [
            (0, "0.0B"),
            (1023, "1023.0B"),
            (1024, "1.0KB"),
            (13_312, "13.0KB"),
            (123_456, "120.6KB"),
            (12_345_678, "11.8MB"),
            (5 << 40, "5.0TB"),
        ] {
            assert_eq!(human_size(bytes), expected, "{bytes}");
        }
    }
}
