// Test helpers: a scratch dir and a tiny in-process HTTP server mocking the iLovePDF API.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

pub const DUMMY_PDF: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/assets/dummy.pdf");

// Directory under the system temp dir, removed on drop.
pub struct TempDir(pub PathBuf);

impl TempDir {
    pub fn new() -> Self {
        static COUNTER: AtomicUsize = AtomicUsize::new(0);
        let idx = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir =
            std::env::temp_dir().join(format!("pdf-compressor-test-{}-{idx}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("create temp dir");
        Self(dir)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    // Copy the dummy PDF to `name` inside this dir and return its path.
    pub fn pdf(&self, name: &str) -> PathBuf {
        let path = self.0.join(name);
        std::fs::create_dir_all(path.parent().expect("has parent")).expect("mkdir");
        std::fs::copy(DUMMY_PDF, &path).expect("copy dummy PDF");
        path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[derive(Clone, Debug)]
pub struct Request {
    pub method: String,
    pub path: String,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
}

impl Request {
    pub fn body_str(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }

    // Parts of a multipart/form-data body as (name, filename, content). Panics on malformed
    // framing, e.g. a missing closing boundary.
    pub fn multipart(&self) -> Vec<(String, Option<String>, Vec<u8>)> {
        let content_type = &self.headers["content-type"];
        let boundary = content_type.split_once("boundary=").expect("boundary").1;
        let delim = format!("--{boundary}");
        let closing = format!("\r\n--{boundary}--\r\n");
        assert!(
            self.body.starts_with(delim.as_bytes()),
            "body must open with boundary"
        );
        assert!(
            self.body.ends_with(closing.as_bytes()),
            "body must end with closing boundary"
        );
        let inner = &self.body[delim.len()..self.body.len() - closing.len()];
        let sep = format!("\r\n--{boundary}");
        let mut parts = Vec::new();
        let mut rest = inner;
        loop {
            let end = rest
                .windows(sep.len())
                .position(|win| win == sep.as_bytes());
            let part = &rest[..end.unwrap_or(rest.len())];
            let part = part.strip_prefix(b"\r\n").expect("CRLF after boundary");
            let head_end = part
                .windows(4)
                .position(|win| win == b"\r\n\r\n")
                .expect("part head");
            let head = String::from_utf8_lossy(&part[..head_end]).into_owned();
            let attr = |key: &str| {
                head.split(&format!("{key}=\""))
                    .nth(1)
                    .map(|val| val.split('"').next().unwrap_or_default().to_owned())
            };
            parts.push((
                attr("name").expect("part name"),
                attr("filename"),
                part[head_end + 4..].to_vec(),
            ));
            match end {
                Some(end) => rest = &rest[end + sep.len()..],
                None => break,
            }
        }
        parts
    }

    // Decoded application/x-www-form-urlencoded body.
    pub fn form(&self) -> HashMap<String, String> {
        let decode = |s: &str| {
            let bytes = s.replace('+', " ").into_bytes();
            let mut out = Vec::new();
            let mut idx = 0;
            while idx < bytes.len() {
                if bytes[idx] == b'%' {
                    let hex = std::str::from_utf8(&bytes[idx + 1..idx + 3]).expect("utf8");
                    out.push(u8::from_str_radix(hex, 16).expect("hex"));
                    idx += 3;
                } else {
                    out.push(bytes[idx]);
                    idx += 1;
                }
            }
            String::from_utf8(out).expect("utf8 form")
        };
        self.body_str()
            .split('&')
            .filter_map(|pair| pair.split_once('='))
            .map(|(key, val)| (decode(key), decode(val)))
            .collect()
    }
}

type Handler = dyn Fn(&Request) -> (u16, Vec<u8>) + Send + Sync;

pub struct MockServer {
    pub url: String,
    pub requests: Arc<Mutex<Vec<Request>>>,
}

impl MockServer {
    pub fn start(handler: impl Fn(&Request) -> (u16, Vec<u8>) + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let url = format!("http://{}", listener.local_addr().expect("addr"));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let recorded = Arc::clone(&requests);
        let handler: Arc<Handler> = Arc::new(handler);
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
                let mut line = String::new();
                if reader.read_line(&mut line).unwrap_or(0) == 0 {
                    continue;
                }
                let mut parts = line.split_whitespace();
                let method = parts.next().unwrap_or_default().to_owned();
                let path = parts.next().unwrap_or_default().to_owned();
                let mut headers = HashMap::new();
                loop {
                    let mut header = String::new();
                    reader.read_line(&mut header).expect("read header");
                    let header = header.trim_end();
                    if header.is_empty() {
                        break;
                    }
                    let (key, val) = header.split_once(':').expect("header colon");
                    headers.insert(key.trim().to_lowercase(), val.trim().to_owned());
                }
                assert!(
                    !headers.contains_key("transfer-encoding"),
                    "chunked bodies unsupported"
                );
                let len = headers
                    .get("content-length")
                    .map_or(0, |len| len.parse().expect("len"));
                let mut body = vec![0; len];
                reader.read_exact(&mut body).expect("read body");
                let request = Request {
                    method,
                    path,
                    headers,
                    body,
                };
                let (status, resp_body) = handler(&request);
                recorded.lock().expect("lock").push(request);
                let head = format!(
                    "HTTP/1.1 {status} X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    resp_body.len()
                );
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&resp_body);
            }
        });
        Self { url, requests }
    }

    // Mock of a well-behaved iLovePDF: compresses each upload to `outputs[upload_idx]`.
    // Multi-file downloads are ZIPs with entries in reverse order to exercise index mapping.
    pub fn ilovepdf(outputs: Vec<Vec<u8>>) -> Self {
        Self::ilovepdf_with(outputs, |_| None)
    }

    // Like `ilovepdf` but `override_fn` can answer any request first.
    pub fn ilovepdf_with(
        outputs: Vec<Vec<u8>>,
        override_fn: impl Fn(&Request) -> Option<(u16, Vec<u8>)> + Send + Sync + 'static,
    ) -> Self {
        let host = Arc::new(Mutex::new(String::new()));
        let host_for_handler = Arc::clone(&host);
        let n_uploads = AtomicUsize::new(0);
        let server = Self::start(move |req| {
            if let Some(resp) = override_fn(req) {
                return resp;
            }
            let json = |val: serde_json::Value| (200, val.to_string().into_bytes());
            let path = req.path.as_str();
            if path == "/v1/auth" {
                json(serde_json::json!({"token": "tok"}))
            } else if path == "/v1/info" {
                json(serde_json::json!({"remaining_credits": 42}))
            } else if path == "/v1/start/compress" {
                let host = host_for_handler.lock().expect("lock").clone();
                json(serde_json::json!({"server": host, "task": "T1"}))
            } else if path == "/v1/upload" {
                let idx = n_uploads.fetch_add(1, Ordering::SeqCst);
                json(serde_json::json!({"server_filename": format!("srv{idx}")}))
            } else if path == "/v1/process" {
                let n_files = req
                    .form()
                    .keys()
                    .filter(|key| key.ends_with("[server_filename]"))
                    .count();
                json(serde_json::json!({"output_filenumber": n_files, "download_filename": "x"}))
            } else if path == "/v1/download/T1" {
                if outputs.len() == 1 {
                    (200, outputs[0].clone())
                } else {
                    (
                        200,
                        zip_entries(
                            outputs
                                .iter()
                                .enumerate()
                                .rev()
                                .map(|(idx, out)| (format!("{idx}.pdf"), out.clone())),
                        ),
                    )
                }
            } else if path == "/v1/task/T1" && req.method == "DELETE" {
                json(serde_json::json!({}))
            } else {
                (
                    404,
                    format!("no mock for {} {path}", req.method).into_bytes(),
                )
            }
        });
        *host.lock().expect("lock") = server.url.trim_start_matches("http://").to_owned();
        server
    }

    pub fn paths(&self) -> Vec<String> {
        self.requests
            .lock()
            .expect("lock")
            .iter()
            .map(|req| format!("{} {}", req.method, req.path))
            .collect()
    }

    pub fn request(&self, path: &str) -> Request {
        self.requests
            .lock()
            .expect("lock")
            .iter()
            .find(|req| req.path == path)
            .unwrap_or_else(|| panic!("no request to {path}"))
            .clone()
    }
}

pub fn zip_entries(entries: impl IntoIterator<Item = (String, Vec<u8>)>) -> Vec<u8> {
    let mut writer = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    for (name, content) in entries {
        writer
            .start_file(name, zip::write::SimpleFileOptions::default())
            .expect("zip entry");
        writer.write_all(&content).expect("zip write");
    }
    writer.finish().expect("zip finish").into_inner()
}

// Run the CLI with an isolated HOME/config dir and (optionally) a mock API + key.
pub fn run_cli(home: &Path, api: Option<&MockServer>, args: &[&str]) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_pdf-compressor"));
    cmd.args(args)
        .env_remove("ILOVEPDF_PUBLIC_KEY")
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home.join("config"))
        .env("APPDATA", home.join("config"))
        .env(
            "ILOVEPDF_API_URL",
            api.map_or("http://127.0.0.1:9", |api| api.url.as_str()),
        );
    if api.is_some() {
        cmd.env("ILOVEPDF_PUBLIC_KEY", "project_public_test");
    }
    cmd.output().expect("run pdf-compressor")
}

pub fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

pub fn stderr(out: &Output) -> String {
    String::from_utf8_lossy(&out.stderr).into_owned()
}
