use std::env;
use std::fs;
use std::path::PathBuf;

use crate::error::{Error, Result, io_err};

/// Environment variable holding the iLovePDF public key. Takes precedence over the config file.
pub const API_KEY_ENV: &str = "ILOVEPDF_PUBLIC_KEY";

/// File the API key is stored in by `set_api_key`:
/// `$XDG_CONFIG_HOME/pdf-compressor/api-key`, falling back to `%APPDATA%\...` on Windows and
/// `~/.config/...` elsewhere.
pub fn config_path() -> Result<PathBuf> {
    let non_empty = |var: &str| env::var_os(var).filter(|val| !val.is_empty()).map(PathBuf::from);
    let base = non_empty("XDG_CONFIG_HOME")
        .or_else(|| {
            if cfg!(windows) {
                non_empty("APPDATA")
            } else {
                non_empty("HOME").map(|home| home.join(".config"))
            }
        })
        .ok_or_else(|| Error::Input("can't locate config dir: set XDG_CONFIG_HOME".into()))?;
    Ok(base.join("pdf-compressor").join("api-key"))
}

/// Read the API key from `ILOVEPDF_PUBLIC_KEY`, else from the config file.
pub fn api_key() -> Result<String> {
    if let Some(key) = env::var(API_KEY_ENV).ok().filter(|key| !key.trim().is_empty()) {
        return Ok(key.trim().to_owned());
    }
    let config_path = config_path()?;
    match fs::read_to_string(&config_path) {
        Ok(key) if !key.trim().is_empty() => Ok(key.trim().to_owned()),
        Ok(_) => Err(Error::MissingApiKey { config_path }),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            Err(Error::MissingApiKey { config_path })
        }
        Err(err) => Err(io_err(config_path)(err)),
    }
}

/// Validate and persist an API key to `config_path()`. Returns the path written.
pub fn set_api_key(key: &str) -> Result<PathBuf> {
    let key = key.trim();
    if !key.starts_with("project_public_") {
        return Err(Error::Input(format!(
            "invalid API key, must start with 'project_public_', got {key:?}"
        )));
    }
    let path = config_path()?;
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(io_err(dir))?;
    }
    // create empty, restrict to owner, then write: the key is never world-readable, also
    // when overwriting a file from an older version
    let mut file = fs::File::create(&path).map_err(io_err(&path))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600)).map_err(io_err(&path))?;
    }
    std::io::Write::write_all(&mut file, format!("{key}\n").as_bytes()).map_err(io_err(&path))?;
    Ok(path)
}
