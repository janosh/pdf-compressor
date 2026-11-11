//! Minimal client for the iLovePDF REST API: https://developer.ilovepdf.com/docs/api-reference

use std::fs;
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Deserialize;
use serde::de::DeserializeOwned;
use ureq::Agent;
use ureq::http::Response;

use crate::error::{Error, Result, io_err};

pub const DEFAULT_API_URL: &str = "https://api.ilovepdf.com";

/// How hard iLovePDF squeezes. `Extreme` noticeably degrades embedded images.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, clap::ValueEnum)]
pub enum CompressionLevel {
    Low,
    #[default]
    Recommended,
    Extreme,
}

impl CompressionLevel {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Low => "low",
            Self::Recommended => "recommended",
            Self::Extreme => "extreme",
        }
    }
}

/// An authenticated iLovePDF session.
pub struct Client {
    agent: Agent,
    scheme: String,
    base_url: String,
    auth_header: String,
}

#[derive(Deserialize)]
struct AuthResponse {
    token: String,
}

#[derive(Deserialize)]
struct StartResponse {
    server: String,
    task: String,
}

#[derive(Deserialize)]
struct UploadResponse {
    server_filename: String,
}

#[derive(Deserialize)]
struct ProcessResponse {
    output_filenumber: usize,
}

#[derive(Deserialize)]
struct InfoResponse {
    remaining_credits: i64,
}

impl Client {
    /// Authenticate against `base_url` (e.g. `DEFAULT_API_URL`) with an iLovePDF public key.
    pub fn new(public_key: &str, base_url: &str) -> Result<Self> {
        let base_url = base_url.trim_end_matches('/').to_owned();
        let scheme = base_url
            .split_once("://")
            .map(|(scheme, _)| scheme.to_owned())
            .ok_or_else(|| Error::Input(format!("API URL needs a scheme, got {base_url:?}")))?;
        // per-read rather than global timeouts: big uploads/downloads may take long overall
        let agent: Agent = Agent::config_builder()
            .http_status_as_error(false)
            .timeout_connect(Some(Duration::from_secs(30)))
            .timeout_per_read(Some(Duration::from_secs(300)))
            .build()
            .into();
        let url = format!("{base_url}/v1/auth");
        let resp = agent.post(&url).send_form([("public_key", public_key)]);
        let AuthResponse { token } = read_json(&url, resp)?;
        Ok(Self {
            agent,
            scheme,
            base_url,
            auth_header: format!("Bearer {token}"),
        })
    }

    /// Files left in the current billing cycle.
    pub fn remaining_credits(&self) -> Result<i64> {
        let url = format!("{}/v1/info", self.base_url);
        let resp = self
            .agent
            .get(&url)
            .header("Authorization", &self.auth_header)
            .call();
        Ok(read_json::<InfoResponse>(&url, resp)?.remaining_credits)
    }

    /// Compress `pdfs` in one iLovePDF task. Returns the compressed bytes of each input, in
    /// input order. The server-side task is deleted afterwards, also when a step fails. A
    /// failed deletion only warns on stderr (iLovePDF expires tasks anyway), so results
    /// that already used up quota aren't thrown away.
    pub fn compress(
        &self,
        pdfs: &[PathBuf],
        level: CompressionLevel,
        password: Option<&str>,
    ) -> Result<Vec<Vec<u8>>> {
        if pdfs.is_empty() {
            return Err(Error::Input("no PDFs to compress".into()));
        }
        let url = format!("{}/v1/start/compress", self.base_url);
        let resp = self
            .agent
            .get(&url)
            .header("Authorization", &self.auth_header)
            .call();
        let StartResponse { server, task } = read_json(&url, resp)?;
        let task_url = format!("{}://{server}/v1", self.scheme);

        let result = self.run_task(&task_url, &task, pdfs, level, password);
        let url = format!("{task_url}/task/{task}");
        let resp = self
            .agent
            .delete(&url)
            .header("Authorization", &self.auth_header)
            .call();
        if let Err(err) = check_status(&url, resp) {
            eprintln!("warning: failed to delete iLovePDF task {task}: {err}");
        }
        result
    }

    fn run_task(
        &self,
        task_url: &str,
        task: &str,
        pdfs: &[PathBuf],
        level: CompressionLevel,
        password: Option<&str>,
    ) -> Result<Vec<Vec<u8>>> {
        // Name uploads by input index so outputs map back unambiguously and local paths
        // never leave the machine.
        let mut form = vec![
            ("task".to_owned(), task.to_owned()),
            ("tool".to_owned(), "compress".to_owned()),
            ("compression_level".to_owned(), level.as_str().to_owned()),
            ("output_filename".to_owned(), "{filename}".to_owned()),
        ];
        for (idx, pdf) in pdfs.iter().enumerate() {
            let server_filename = self.upload(task_url, task, pdf, idx)?;
            form.push((format!("files[{idx}][filename]"), format!("{idx}.pdf")));
            form.push((format!("files[{idx}][server_filename]"), server_filename));
            if let Some(password) = password {
                form.push((format!("files[{idx}][password]"), password.to_owned()));
            }
        }

        let url = format!("{task_url}/process");
        let resp = self
            .agent
            .post(&url)
            .header("Authorization", &self.auth_header)
            .send_form(form);
        let ProcessResponse { output_filenumber } = read_json(&url, resp)?;
        if output_filenumber != pdfs.len() {
            return Err(Error::Api(format!(
                "iLovePDF processed {output_filenumber} files, expected {}",
                pdfs.len()
            )));
        }

        let url = format!("{task_url}/download/{task}");
        let resp = self
            .agent
            .get(&url)
            .header("Authorization", &self.auth_header)
            .call();
        let bytes = check_status(&url, resp)?
            .body_mut()
            .with_config()
            .limit(u64::MAX)
            .read_to_vec()
            .map_err(|source| Error::Transport {
                url,
                source: source.into(),
            })?;
        if pdfs.len() == 1 {
            Ok(vec![bytes])
        } else {
            unzip_by_index(bytes, pdfs.len())
        }
    }

    fn upload(&self, task_url: &str, task: &str, pdf: &Path, idx: usize) -> Result<String> {
        let content = fs::read(pdf).map_err(io_err(pdf))?;
        // the boundary must not occur in the content, else the server splits the PDF
        let boundary = (0..)
            .map(|n| format!("pdf-compressor-boundary-7f3a9c1e5b2d4086-{n}"))
            .find(|boundary| {
                !content
                    .windows(boundary.len())
                    .any(|win| win == boundary.as_bytes())
            })
            .expect("some boundary is absent from any finite file");
        let mut body = format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"task\"\r\n\r\n{task}\r\n\
             --{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{idx}.pdf\"\r\n\
             Content-Type: application/pdf\r\n\r\n"
        )
        .into_bytes();
        body.extend_from_slice(&content);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

        let url = format!("{task_url}/upload");
        let resp = self
            .agent
            .post(&url)
            .header("Authorization", &self.auth_header)
            .header(
                "Content-Type",
                format!("multipart/form-data; boundary={boundary}"),
            )
            .send(&body[..]);
        Ok(read_json::<UploadResponse>(&url, resp)?.server_filename)
    }
}

/// Multi-file downloads are a ZIP whose entries are named `{idx}.pdf` (see `run_task`).
fn unzip_by_index(bytes: Vec<u8>, n_files: usize) -> Result<Vec<Vec<u8>>> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))?;
    let mut outputs: Vec<Option<Vec<u8>>> = vec![None; n_files];
    for entry_idx in 0..archive.len() {
        let mut entry = archive.by_index(entry_idx)?;
        let name = entry
            .name()?
            .rsplit('/')
            .next()
            .unwrap_or_default()
            .to_owned();
        let digits: String = name.chars().take_while(char::is_ascii_digit).collect();
        let slot = digits
            .parse::<usize>()
            .ok()
            .and_then(|idx| outputs.get_mut(idx));
        let Some(slot) = slot.filter(|slot| slot.is_none()) else {
            return Err(Error::Api(format!(
                "unexpected or duplicate ZIP entry {name:?}"
            )));
        };
        let mut content = Vec::new();
        entry.read_to_end(&mut content).map_err(io_err(&name))?;
        *slot = Some(content);
    }
    outputs
        .into_iter()
        .enumerate()
        .map(|(idx, out)| out.ok_or_else(|| Error::Api(format!("ZIP has no entry for file {idx}"))))
        .collect()
}

fn check_status(
    url: &str,
    resp: std::result::Result<Response<ureq::Body>, ureq::Error>,
) -> Result<Response<ureq::Body>> {
    let mut resp = resp.map_err(|source| Error::Transport {
        url: url.to_owned(),
        source: source.into(),
    })?;
    let status = resp.status().as_u16();
    if !resp.status().is_success() {
        let body = resp
            .body_mut()
            .read_to_string()
            .unwrap_or_else(|err| format!("<unreadable response body: {err}>"));
        return Err(Error::Http {
            url: url.to_owned(),
            status,
            body,
        });
    }
    Ok(resp)
}

fn read_json<T: DeserializeOwned>(
    url: &str,
    resp: std::result::Result<Response<ureq::Body>, ureq::Error>,
) -> Result<T> {
    check_status(url, resp)?
        .body_mut()
        .read_json()
        .map_err(|source| Error::Transport {
            url: url.to_owned(),
            source: source.into(),
        })
}
