use std::fmt;
use std::path::PathBuf;

use crate::config::API_KEY_ENV;

pub type Result<T> = std::result::Result<T, Error>;

/// Everything that can go wrong while configuring, validating inputs or talking to iLovePDF.
#[derive(Debug)]
#[non_exhaustive]
pub enum Error {
    /// No API key in the environment or the config file.
    MissingApiKey { config_path: PathBuf },
    /// Bad user input caught before any API request is made.
    Input(String),
    /// iLovePDF answered with a non-2xx status.
    Http {
        url: String,
        status: u16,
        body: String,
    },
    /// The request never got a response (DNS, TLS, connection, body decoding).
    Transport {
        url: String,
        source: Box<dyn std::error::Error + Send + Sync>,
    },
    /// iLovePDF answered 2xx but with content we can't use.
    Api(String),
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingApiKey { config_path } => write!(
                f,
                "pdf-compressor needs an iLovePDF public key to access its API. Get one at \
                 https://developer.ilovepdf.com/signup, then save it with \
                 `pdf-compressor --set-api-key project_public_...` (writes {}) or set the \
                 {API_KEY_ENV} environment variable",
                config_path.display()
            ),
            Self::Input(msg) | Self::Api(msg) => f.write_str(msg),
            Self::Http { url, status, body } => {
                write!(f, "{url} returned HTTP {status}: {body}")
            }
            Self::Transport { url, source } => write!(f, "request to {url} failed: {source}"),
            Self::Io { path, source } => write!(f, "{}: {source}", path.display()),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Transport { source, .. } => Some(source.as_ref()),
            Self::Io { source, .. } => Some(source),
            _ => None,
        }
    }
}

/// Attach the offending path to an io::Error.
pub(crate) fn io_err(path: impl Into<PathBuf>) -> impl FnOnce(std::io::Error) -> Error {
    let path = path.into();
    move |source| Error::Io { path, source }
}
