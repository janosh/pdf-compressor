//! Minimal client for the iLovePDF REST API: https://developer.ilovepdf.com/docs/api-reference

use std::fs;
use std::hash::{BuildHasher, RandomState};
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;
use std::time::Duration;

use clap::ValueEnum;
use serde::Deserialize;
use serde::de::DeserializeOwned;
use ureq::http::Response;
use ureq::{Agent, Body, RequestBuilder, SendBody};

use crate::error::{Error, Result, io_err};

pub const DEFAULT_API_URL: &str = "https://api.ilovepdf.com";
/// Concurrent uploads per task. Bounded so big batches don't open dozens of connections.
const PARALLEL_UPLOADS: usize = 4;

type Reply = std::result::Result<Response<Body>, ureq::Error>;

/// How hard iLovePDF squeezes. `Extreme` noticeably degrades embedded images.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, ValueEnum)]
pub enum CompressionLevel {
    Low,
    #[default]
    Recommended,
    Extreme,
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
        let Some((scheme, _)) = base_url.split_once("://") else {
            return Err(Error::Input(format!(
                "API URL needs a scheme, got {base_url:?}"
            )));
        };
        let scheme = scheme.to_owned();
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
        let auth_header = format!("Bearer {token}");
        Ok(Self {
            agent,
            scheme,
            base_url,
            auth_header,
        })
    }

    fn authed<B>(&self, req: RequestBuilder<B>) -> RequestBuilder<B> {
        req.header("Authorization", &self.auth_header)
    }

    /// Files left in the current billing cycle.
    pub fn remaining_credits(&self) -> Result<i64> {
        let url = format!("{}/v1/info", self.base_url);
        let resp = self.authed(self.agent.get(&url)).call();
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
        let resp = self.authed(self.agent.get(&url)).call();
        let StartResponse { server, task } = read_json(&url, resp)?;
        let task_url = format!("{}://{server}/v1", self.scheme);

        let result = self.run_task(&task_url, &task, pdfs, level, password);
        let url = format!("{task_url}/task/{task}");
        if let Err(err) = check_status(&url, self.authed(self.agent.delete(&url)).call()) {
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
        let level = level.to_possible_value().expect("no skipped variants");
        // Name uploads by input index so outputs map back unambiguously and local paths
        // never leave the machine.
        let mut form = vec![
            ("task".to_owned(), task.to_owned()),
            ("tool".to_owned(), "compress".to_owned()),
            ("compression_level".to_owned(), level.get_name().to_owned()),
            ("output_filename".to_owned(), "{filename}".to_owned()),
        ];
        for (idx, server_filename) in self
            .upload_all(task_url, task, pdfs)?
            .into_iter()
            .enumerate()
        {
            form.push((format!("files[{idx}][filename]"), format!("{idx}.pdf")));
            form.push((format!("files[{idx}][server_filename]"), server_filename));
            if let Some(password) = password {
                form.push((format!("files[{idx}][password]"), password.to_owned()));
            }
        }

        crate::status(format!("Compressing {} PDFs on iLovePDF...", pdfs.len()));
        let url = format!("{task_url}/process");
        let resp = self.authed(self.agent.post(&url)).send_form(form);
        let ProcessResponse { output_filenumber } = read_json(&url, resp)?;
        if output_filenumber != pdfs.len() {
            return Err(Error::Api(format!(
                "iLovePDF processed {output_filenumber} files, expected {}",
                pdfs.len()
            )));
        }

        crate::status("Downloading compressed PDFs...");
        let url = format!("{task_url}/download/{task}");
        let resp = self.authed(self.agent.get(&url)).call();
        let bytes = check_status(&url, resp)?
            .body_mut()
            .with_config()
            .limit(u64::MAX)
            .read_to_vec()
            .map_err(transport_err(&url))?;
        if pdfs.len() == 1 {
            Ok(vec![bytes])
        } else {
            unzip_by_index(bytes, pdfs.len())
        }
    }

    /// Upload `pdfs` with up to `PARALLEL_UPLOADS` concurrent requests. Returns server
    /// file names in input order. After the first failure no new uploads start.
    fn upload_all(&self, task_url: &str, task: &str, pdfs: &[PathBuf]) -> Result<Vec<String>> {
        let (next, done, failed) = (
            AtomicUsize::new(0),
            AtomicUsize::new(0),
            AtomicBool::new(false),
        );
        crate::status(format!("Uploading 0/{} PDFs...", pdfs.len()));
        let mut results: Vec<(usize, Result<String>)> = thread::scope(|scope| {
            let workers: Vec<_> = (0..PARALLEL_UPLOADS.min(pdfs.len()))
                .map(|_| {
                    scope.spawn(|| {
                        let mut results = Vec::new();
                        while !failed.load(Ordering::Relaxed) {
                            let idx = next.fetch_add(1, Ordering::Relaxed);
                            let Some(pdf) = pdfs.get(idx) else { break };
                            let result = self.upload(task_url, task, pdf, idx);
                            failed.fetch_or(result.is_err(), Ordering::Relaxed);
                            let n_done = done.fetch_add(1, Ordering::Relaxed) + 1;
                            crate::status(format!("Uploading {n_done}/{} PDFs...", pdfs.len()));
                            results.push((idx, result));
                        }
                        results
                    })
                })
                .collect();
            workers
                .into_iter()
                .flat_map(|worker| worker.join().expect("upload thread panicked"))
                .collect()
        });
        results.sort_by_key(|(idx, _)| *idx);
        results.into_iter().map(|(_, result)| result).collect()
    }

    /// Stream one PDF as multipart/form-data without loading it into memory.
    fn upload(&self, task_url: &str, task: &str, pdf: &Path, idx: usize) -> Result<String> {
        let file = fs::File::open(pdf).map_err(io_err(pdf))?;
        let file_len = file.metadata().map_err(io_err(pdf))?.len();
        // 128 random bits: a collision with the PDF's bytes is practically impossible
        let random = || RandomState::new().hash_one(idx);
        let boundary = format!("pdf-compressor-{:016x}{:016x}", random(), random());
        let head = format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"task\"\r\n\r\n{task}\r\n\
             --{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{idx}.pdf\"\r\n\
             Content-Type: application/pdf\r\n\r\n"
        );
        let tail = format!("\r\n--{boundary}--\r\n");
        let content_length = head.len() as u64 + file_len + tail.len() as u64;
        let body = Cursor::new(head).chain(file).chain(Cursor::new(tail));

        let url = format!("{task_url}/upload");
        let resp = self
            .authed(self.agent.post(&url))
            .header(
                "Content-Type",
                format!("multipart/form-data; boundary={boundary}"),
            )
            // explicit length stops ureq from using chunked encoding for a streamed body
            .header("Content-Length", content_length.to_string())
            .send(SendBody::from_owned_reader(body));
        Ok(read_json::<UploadResponse>(&url, resp)?.server_filename)
    }
}

/// Multi-file downloads are a ZIP whose entries are named `{idx}.pdf` (see `run_task`).
fn unzip_by_index(bytes: Vec<u8>, n_files: usize) -> Result<Vec<Vec<u8>>> {
    let zip_err = |err: zip::result::ZipError| Error::Api(format!("bad ZIP from iLovePDF: {err}"));
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes)).map_err(zip_err)?;
    let mut outputs: Vec<Option<Vec<u8>>> = vec![None; n_files];
    for entry_idx in 0..archive.len() {
        let mut entry = archive.by_index(entry_idx).map_err(zip_err)?;
        let name = entry.name().map_err(zip_err)?;
        let name = name.rsplit('/').next().unwrap_or_default().to_owned();
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

fn transport_err(url: &str) -> impl FnOnce(ureq::Error) -> Error {
    let url = url.to_owned();
    move |source| Error::Transport {
        url,
        source: source.into(),
    }
}

fn check_status(url: &str, resp: Reply) -> Result<Response<Body>> {
    let mut resp = resp.map_err(transport_err(url))?;
    if resp.status().is_success() {
        return Ok(resp);
    }
    let body = resp
        .body_mut()
        .read_to_string()
        .unwrap_or_else(|err| format!("<unreadable response body: {err}>"));
    Err(Error::Http {
        url: url.to_owned(),
        status: resp.status().as_u16(),
        body,
    })
}

fn read_json<T: DeserializeOwned>(url: &str, resp: Reply) -> Result<T> {
    check_status(url, resp)?
        .body_mut()
        .read_json()
        .map_err(transport_err(url))
}
