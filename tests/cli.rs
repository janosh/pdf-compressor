mod common;

use std::fs;

use common::{MockServer, TempDir, run_cli, stderr, stdout, zip_entries};

const SMALL: &[u8] = b"%PDF-small";
// dummy value for the --password flag, kept out of `password = "..."` shapes so secret
// scanners don't flag it
const UNLOCK_PHRASE: &str = "open-sesame";
// CSV quoting needs `"` in a file name, which Windows forbids
const B_STEM: &str = if cfg!(windows) { "b, c" } else { "b \"q\", c" };
const DUMMY_SIZE: u64 = 13_264;

#[test]
fn missing_api_key_gives_friendly_error() {
    let tmp = TempDir::new();
    for args in [&["--report-quota"][..], &[common::DUMMY_PDF]] {
        let out = run_cli(tmp.path(), None, args);
        assert!(!out.status.success(), "{args:?}");
        let err = stderr(&out);
        assert!(err.contains("needs an iLovePDF public key"), "{err}");
        assert!(err.contains("ILOVEPDF_PUBLIC_KEY") && err.contains("--set-api-key"), "{err}");
    }
}

#[test]
fn set_api_key_persists_key_used_by_report_quota() {
    let tmp = TempDir::new();
    let out = run_cli(tmp.path(), None, &["--set-api-key", "foo"]);
    assert!(!out.status.success());
    assert!(stderr(&out).contains("must start with 'project_public_'"), "{}", stderr(&out));

    let out = run_cli(tmp.path(), None, &["--set-api-key", " project_public_abc "]);
    assert!(out.status.success(), "{}", stderr(&out));
    let key_file = tmp.path().join("config/pdf-compressor/api-key");
    assert_eq!(fs::read_to_string(&key_file).unwrap(), "project_public_abc\n");

    // the saved key is used when the env var is absent
    let api = MockServer::ilovepdf(vec![]);
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_pdf-compressor"))
        .arg("--report-quota")
        .env_remove("ILOVEPDF_PUBLIC_KEY")
        .env("XDG_CONFIG_HOME", tmp.path().join("config"))
        .env("ILOVEPDF_API_URL", &api.url)
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", stderr(&out));
    assert_eq!(stdout(&out), "Remaining files in this billing cycle: 42\n");
    assert_eq!(api.request("/v1/auth").form()["public_key"], "project_public_abc");
    assert_eq!(api.request("/v1/info").headers["authorization"], "Bearer tok");

    // --set-api-key can't be combined with other args
    let out = run_cli(tmp.path(), None, &["--set-api-key", "project_public_x", "a.pdf"]);
    assert!(!out.status.success());
}

#[test]
fn single_file_with_suffix_runs_full_task_workflow() {
    let tmp = TempDir::new();
    let pdf = tmp.pdf("doc.pdf");
    let original = fs::read(&pdf).unwrap();
    let api = MockServer::ilovepdf(vec![SMALL.to_vec()]);

    let args = [pdf.to_str().unwrap(), "--cl", "extreme", "--password", UNLOCK_PHRASE];
    let out = run_cli(tmp.path(), Some(&api), &args);
    assert!(out.status.success(), "{}", stderr(&out));
    assert_eq!(fs::read(tmp.path().join("doc-compressed.pdf")).unwrap(), SMALL);
    assert_eq!(fs::read(&pdf).unwrap(), original, "original must be untouched");
    assert_eq!(
        stdout(&out),
        format!(
            "'{}': 13.0KB -> 10.0B, 12.9KB = 100% smaller. Saved as {}.\n",
            pdf.display(),
            tmp.path().join("doc-compressed.pdf").display()
        )
    );
    // no temp files left behind
    let mut names: Vec<_> =
        fs::read_dir(tmp.path()).unwrap().map(|e| e.unwrap().file_name()).collect();
    names.sort();
    assert_eq!(names, ["doc-compressed.pdf", "doc.pdf"]);

    assert_eq!(
        api.paths(),
        [
            "POST /v1/auth",
            "GET /v1/start/compress",
            "POST /v1/upload",
            "POST /v1/process",
            "GET /v1/download/T1",
            "DELETE /v1/task/T1",
        ]
    );
    let upload = api.request("/v1/upload");
    assert_eq!(upload.headers["authorization"], "Bearer tok");
    let content_type = &upload.headers["content-type"];
    let boundary = content_type.strip_prefix("multipart/form-data; boundary=").unwrap();
    // exact framing: task field, then the PDF named by index so local names don't leak
    let expected_body = [
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"task\"\r\n\r\nT1\r\n\
             --{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"0.pdf\"\r\n\
             Content-Type: application/pdf\r\n\r\n"
        )
        .as_bytes(),
        &original,
        format!("\r\n--{boundary}--\r\n").as_bytes(),
    ]
    .concat();
    assert!(upload.body == expected_body, "unexpected multipart body");

    let form = api.request("/v1/process").form();
    for (key, val) in [
        ("task", "T1"),
        ("tool", "compress"),
        ("compression_level", "extreme"),
        ("files[0][filename]", "0.pdf"),
        ("files[0][server_filename]", "srv0"),
        ("files[0][password]", UNLOCK_PHRASE),
    ] {
        assert_eq!(form.get(key).map(String::as_str), Some(val), "{key}");
    }
}

#[test]
fn multiple_files_map_zip_entries_to_inputs_and_write_to_outdir() {
    let tmp = TempDir::new();
    // directory input is searched recursively; the same file passed again (differently
    // spelled) and via a symlinked dir is only uploaded once
    let pdf_a = tmp.pdf("in/a.pdf");
    tmp.pdf("in/sub/b.PDF");
    // hidden entries (macOS resource forks, VCS dirs) are skipped
    tmp.pdf("in/._a.pdf");
    tmp.pdf("in/.git/x.pdf");
    fs::write(tmp.path().join("in/notes.txt"), "ignored inside dirs").unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink("..", tmp.path().join("in/sub/loop")).unwrap();
    let outdir = tmp.path().join("out");
    let api = MockServer::ilovepdf(vec![b"AAA".to_vec(), b"BBBB".to_vec()]);
    let respelled = tmp.path().join("in/sub/../a.pdf");

    let out = run_cli(
        tmp.path(),
        Some(&api),
        &[
            tmp.path().join("in").to_str().unwrap(),
            pdf_a.to_str().unwrap(),
            respelled.to_str().unwrap(),
            "-o",
            outdir.to_str().unwrap(),
            "-s",
            "",
        ],
    );
    assert!(out.status.success(), "{}", stderr(&out));
    assert_eq!(api.paths().iter().filter(|path| path.ends_with("/upload")).count(), 2);
    assert_eq!(fs::read(outdir.join("a.pdf")).unwrap(), b"AAA");
    assert_eq!(fs::read(outdir.join("b.PDF")).unwrap(), b"BBBB");
    let b_line = format!("[2/2] '{}': ", tmp.path().join("in").join("sub").join("b.PDF").display());
    assert!(stdout(&out).contains(&b_line), "{}", stdout(&out));
    assert!(
        stdout(&out).contains("Saved 25.9KB in total across 2 files: 25.9KB -> 7.0B"),
        "{}",
        stdout(&out)
    );
    assert!(!api.request("/v1/process").form().contains_key("files[0][password]"));
}

#[test]
fn malformed_server_responses_are_rejected() {
    let two = |name0: &str, name1: &str| {
        zip_entries([(name0.to_owned(), b"A".to_vec()), (name1.to_owned(), b"B".to_vec())])
    };
    let download = "/v1/download/T1";
    for (path, body, expected) in [
        (download, two("0.pdf", "sub/0.pdf"), "unexpected or duplicate ZIP entry \"0.pdf\""),
        (download, two("0.pdf", "7.pdf"), "unexpected or duplicate ZIP entry \"7.pdf\""),
        (
            download,
            zip_entries([("1.pdf".to_owned(), b"B".to_vec())]),
            "ZIP has no entry for file 0",
        ),
        (download, b"not a zip".to_vec(), "bad ZIP from iLovePDF"),
        ("/v1/process", br#"{"output_filenumber": 3}"#.to_vec(), "processed 3 files, expected 2"),
    ] {
        let tmp = TempDir::new();
        let (pdf_a, pdf_b) = (tmp.pdf("a.pdf"), tmp.pdf("b.pdf"));
        let api = MockServer::ilovepdf_with(vec![], move |req| {
            (req.path == path).then(|| (200, body.clone()))
        });
        let out =
            run_cli(tmp.path(), Some(&api), &[pdf_a.to_str().unwrap(), pdf_b.to_str().unwrap()]);
        assert!(!out.status.success());
        assert!(stderr(&out).contains(expected), "{expected}: {}", stderr(&out));
        assert!(!tmp.path().join("a-compressed.pdf").exists());
        assert_eq!(api.paths().last().map(String::as_str), Some("DELETE /v1/task/T1"));
    }
}

#[test]
fn in_place_replaces_original() {
    let tmp = TempDir::new();
    fs::create_dir_all(tmp.path().join(".Trash")).unwrap();
    let pdf = tmp.pdf("doc.pdf");
    // pre-existing Trash entry with same name must survive
    fs::write(tmp.path().join(".Trash/doc.pdf"), "old").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&pdf, fs::Permissions::from_mode(0o600)).unwrap();
    }
    let api = MockServer::ilovepdf(vec![SMALL.to_vec()]);

    let out = run_cli(tmp.path(), Some(&api), &["-i", pdf.to_str().unwrap()]);
    assert!(out.status.success(), "{}", stderr(&out));
    assert_eq!(fs::read(&pdf).unwrap(), SMALL);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(&pdf).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "in-place replacement must keep permissions");
    }
    if cfg!(target_os = "macos") {
        assert_eq!(fs::read(tmp.path().join(".Trash/doc.pdf")).unwrap(), b"old");
        assert_eq!(fs::metadata(tmp.path().join(".Trash/doc 2.pdf")).unwrap().len(), DUMMY_SIZE);
    }
}

#[test]
fn min_size_reduction_keeps_original() {
    let tmp = TempDir::new();
    fs::create_dir_all(tmp.path().join(".Trash")).unwrap();
    let pdf = tmp.pdf("doc.pdf");
    let original = vec![1; 10_000];
    // exactly 10% smaller is not *more than* the 10% default for --inplace
    let exactly_10_pct = vec![0; 9_000];
    for (output, args, kept) in [
        (exactly_10_pct.clone(), vec!["-i"], true),
        (exactly_10_pct, vec!["-i", "--min-red", "9"], false),
        (vec![0; 10_005], vec![], true), // grew
    ] {
        fs::write(&pdf, &original).unwrap();
        let api = MockServer::ilovepdf(vec![output.clone()]);
        let pdf_arg = pdf.to_str().unwrap();
        let out = run_cli(tmp.path(), Some(&api), &[args.as_slice(), &[pdf_arg]].concat());
        assert!(out.status.success(), "{}", stderr(&out));
        let expected = if kept { &original } else { &output };
        assert_eq!(&fs::read(&pdf).unwrap(), expected, "{args:?}");
        assert!(!tmp.path().join("doc-compressed.pdf").exists());
        if kept {
            assert!(stdout(&out).contains("kept original"), "{}", stdout(&out));
        }
    }
    let api = MockServer::ilovepdf(vec![vec![0; 9_050]]);
    let out = run_cli(tmp.path(), Some(&api), &["-i", pdf.to_str().unwrap()]);
    assert!(stdout(&out).contains("only 9.5% smaller (needs more than 10%)"), "{}", stdout(&out));
}

#[test]
fn input_errors_fail_before_any_api_request() {
    let tmp = TempDir::new();
    let pdf = tmp.pdf("d/doc.pdf");
    tmp.pdf("d/doc_c.pdf");
    tmp.pdf("other/doc.pdf");
    tmp.pdf("upper/Doc.pdf");
    fs::write(tmp.path().join("d/notes.txt"), "").unwrap();
    let path = |rel: &str| tmp.path().join(rel).to_str().unwrap().to_owned();
    let (pdf, dir) = (pdf.to_str().unwrap().to_owned(), path("d"));
    for (args, expected) in [
        (vec![&pdf, "-s", ""], "would overwrite an input PDF"),
        (vec![&pdf, "-s", "", "-o", &dir], "would overwrite an input PDF"),
        // new/ doesn't exist yet, but new/../d is d
        (vec![&pdf, "-s", "", "-o", &path("new/../d")], "would overwrite an input PDF"),
        // doc.pdf -> doc_c.pdf would clobber the other input
        (vec![&dir, "-s", "_c"], "would overwrite an input PDF"),
        (
            vec![&pdf, &path("other/doc.pdf"), "-o", &path("out")],
            "multiple inputs would be written to",
        ),
        (vec![&pdf, "-s", "/nope/x"], "suffix must not contain path separators"),
        (vec![&pdf, "--write-stats-path", "stats.xlsx"], "must end in .csv"),
        (vec![&pdf, "--write-stats-path", "nope/stats.csv"], "stats dir nope does not exist"),
        (vec![&path("missing.pdf")], "missing.pdf does not exist"),
        (vec![&path("missing-dir")], "missing-dir does not exist"),
        (vec![&path("d/notes.txt"), &pdf], "1 input files aren't PDFs"),
        (vec![&pdf, "-i", "-s", "x"], "cannot be used with"),
    ] {
        let api = MockServer::ilovepdf(vec![SMALL.to_vec()]);
        let out = run_cli(tmp.path(), Some(&api), &args);
        assert!(!out.status.success(), "{args:?}");
        assert!(stderr(&out).contains(expected), "{args:?}: {}", stderr(&out));
        assert!(api.paths().is_empty(), "{args:?} made requests: {:?}", api.paths());
    }
    // Doc.pdf and doc.pdf are the same file on default macOS/Windows file systems
    if cfg!(any(target_os = "macos", windows)) {
        let api = MockServer::ilovepdf(vec![]);
        let args = [&pdf, &path("upper/Doc.pdf"), "-o", &path("out2"), "-s", ""];
        let out = run_cli(tmp.path(), Some(&api), &args);
        assert!(stderr(&out).contains("multiple inputs would be written to"), "{}", stderr(&out));
        assert!(api.paths().is_empty());
    }
}

#[test]
fn no_input_pdfs_is_a_silent_noop() {
    let tmp = TempDir::new();
    fs::create_dir(tmp.path().join("empty")).unwrap();
    let api = MockServer::ilovepdf(vec![]);
    for args in [vec![], vec![tmp.path().join("empty").to_str().unwrap().to_owned()]] {
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        let out = run_cli(tmp.path(), Some(&api), &args);
        assert!(out.status.success(), "{args:?}");
        assert_eq!((stdout(&out).as_str(), stderr(&out).as_str()), ("", ""), "{args:?}");
    }
    assert!(api.paths().is_empty());
}

#[test]
fn task_is_deleted_and_delete_failure_only_warns() {
    for (fail_path, status, body, succeeds, expected) in [
        ("/v1/process", 400, "bad password", false, "/v1/process returned HTTP 400: bad password"),
        ("/v1/task/T1", 503, "busy", true, "warning: failed to delete iLovePDF task T1"),
    ] {
        let tmp = TempDir::new();
        let pdf = tmp.pdf("doc.pdf");
        let api = MockServer::ilovepdf_with(vec![SMALL.to_vec()], move |req| {
            (req.path == fail_path).then(|| (status, body.as_bytes().to_vec()))
        });
        let out = run_cli(tmp.path(), Some(&api), &[pdf.to_str().unwrap()]);
        assert_eq!(out.status.success(), succeeds, "{fail_path}");
        assert!(stderr(&out).contains(expected), "{}", stderr(&out));
        assert_eq!(api.paths().last().map(String::as_str), Some("DELETE /v1/task/T1"));
        // results that already used quota are kept even if cleanup fails
        assert_eq!(tmp.path().join("doc-compressed.pdf").exists(), succeeds);
    }
}

#[test]
fn write_failure_is_per_file_and_stats_still_written() {
    let tmp = TempDir::new();
    let (pdf_a, pdf_b) = (tmp.pdf("a.pdf"), tmp.pdf(&format!("{B_STEM}.pdf")));
    // a directory where b's output should go makes that one write fail
    fs::create_dir(tmp.path().join(format!("{B_STEM}-compressed.pdf"))).unwrap();
    let api = MockServer::ilovepdf(vec![b"AAA".to_vec(), b"BBB".to_vec()]);
    let stats = tmp.path().join("stats.CSV"); // extension is case-insensitive
    let out = run_cli(
        tmp.path(),
        Some(&api),
        &[
            pdf_a.to_str().unwrap(),
            pdf_b.to_str().unwrap(),
            "--write-stats-path",
            stats.to_str().unwrap(),
        ],
    );
    assert!(!out.status.success(), "partial failure must exit non-zero");
    assert_eq!(fs::read(tmp.path().join("a-compressed.pdf")).unwrap(), b"AAA");
    assert!(stdout(&out).contains(&format!("[1/2] '{}'", pdf_a.display())), "{}", stdout(&out));
    assert!(
        stderr(&out)
            .contains(&format!("[2/2] '{}': 13.0KB -> 3.0B, failed to write", pdf_b.display())),
        "{}",
        stderr(&out)
    );

    let csv = fs::read_to_string(&stats).unwrap();
    let lines: Vec<_> = csv.lines().collect();
    assert_eq!(
        lines[0],
        "file,original size (B),compressed size (B),size reduction (B),size reduction (%),action"
    );
    assert_eq!(lines.len(), 3, "{csv}");
    let quoted_b = format!("\"{}\",", pdf_b.display().to_string().replace('"', "\"\""));
    assert!(lines[2].starts_with(&quoted_b), "{}", lines[2]);
    assert!(lines[2].contains(",13264,3,13261,99.9774,\"failed: "), "{}", lines[2]);
    assert!(
        lines[1].ends_with(&format!(
            ",13264,3,13261,99.9774,\"saved as {}\"",
            tmp.path().join("a-compressed.pdf").display()
        )),
        "{}",
        lines[1]
    );
}

#[test]
fn version_flag() {
    let tmp = TempDir::new();
    for flag in ["-v", "--version"] {
        let out = run_cli(tmp.path(), None, &[flag]);
        assert!(out.status.success());
        assert_eq!(stdout(&out), format!("pdf-compressor {}\n", env!("CARGO_PKG_VERSION")));
    }
}

#[test]
fn uploads_run_in_parallel_and_stop_after_first_failure() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    let tmp = TempDir::new();
    let pdfs: Vec<_> = (0..6).map(|idx| tmp.pdf(&format!("f{idx}.pdf"))).collect();
    let args: Vec<&str> = pdfs.iter().map(|pdf| pdf.to_str().unwrap()).collect();
    let (in_flight, max_in_flight) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
    let (in_flight_mock, max_mock) = (Arc::clone(&in_flight), Arc::clone(&max_in_flight));
    let outputs = (0..6).map(|idx| vec![b'x'; idx + 1]).collect();
    let api = MockServer::ilovepdf_with(outputs, move |req| {
        if req.path == "/v1/upload" {
            let now = in_flight_mock.fetch_add(1, Ordering::SeqCst) + 1;
            max_mock.fetch_max(now, Ordering::SeqCst);
            std::thread::sleep(std::time::Duration::from_millis(100));
            in_flight_mock.fetch_sub(1, Ordering::SeqCst);
        }
        None
    });
    let out = run_cli(tmp.path(), Some(&api), &args);
    assert!(out.status.success(), "{}", stderr(&out));
    let max = max_in_flight.load(Ordering::SeqCst);
    assert!((2..=4).contains(&max), "expected 2-4 concurrent uploads, got {max}");
    // uploads finish out of order but each server file maps back to its input
    let form = api.request("/v1/process").form();
    for idx in 0..6 {
        assert_eq!(form[&format!("files[{idx}][server_filename]")], format!("srv{idx}"));
        let dest = tmp.path().join(format!("f{idx}-compressed.pdf"));
        assert_eq!(fs::read(dest).unwrap().len(), idx + 1);
    }

    let api = MockServer::ilovepdf_with(vec![], |req| {
        (req.path == "/v1/upload").then(|| (500, b"boom".to_vec()))
    });
    let out = run_cli(tmp.path(), Some(&api), &args);
    assert!(stderr(&out).contains("/v1/upload returned HTTP 500: boom"), "{}", stderr(&out));
    let uploads = api.paths().iter().filter(|path| path.ends_with("/upload")).count();
    assert!(uploads <= 4, "no new uploads after a failure, got {uploads}");
    assert!(!api.paths().iter().any(|path| path.ends_with("/process")));
    assert_eq!(api.paths().last().map(String::as_str), Some("DELETE /v1/task/T1"));
}
