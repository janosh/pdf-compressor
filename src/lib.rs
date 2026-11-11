//! Batch-compress PDFs with the [iLovePDF API](https://developer.ilovepdf.com).
//!
//! ```no_run
//! use pdf_compressor::{Client, CompressionLevel, Options, Output, DEFAULT_API_URL};
//!
//! let client = Client::new(&pdf_compressor::api_key()?, DEFAULT_API_URL)?;
//! let (pdfs, _not_pdfs) = pdf_compressor::collect_pdfs(&["docs".into()])?;
//! let opts = Options {
//!     level: CompressionLevel::Recommended,
//!     output: Output::Copy { dir: None, suffix: "-compressed".into() },
//!     min_size_reduction: 0,
//!     password: None,
//! };
//! for stat in pdf_compressor::compress_files(&client, &pdfs, &opts)? {
//!     println!("{}: {:.0}% smaller", stat.path.display(), stat.reduction_ratio() * 100.0);
//! }
//! # Ok::<(), pdf_compressor::Error>(())
//! ```

mod api;
mod compress;
mod config;
mod error;

pub use api::{Client, CompressionLevel, DEFAULT_API_URL};
pub use compress::{
    Action, FileStats, Options, Output, StatsFormat, collect_pdfs, compress_files, output_paths,
    write_stats,
};
pub use config::{API_KEY_ENV, api_key, config_path, set_api_key};
pub use error::{Error, Result};
