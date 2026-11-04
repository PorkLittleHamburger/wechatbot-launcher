//! Opt-in real Windows spawn/job/stdio test using only a new offline workspace.
//! WECHAT_SHELL_SMOKE_RUNTIME must point at the staged runtime directory.
#[path = "../src/paths.rs"]
mod paths;
#[path = "../src/windows.rs"]
mod windows;

use std::{io::{BufRead, BufReader, Write}, path::PathBuf, sync::mpsc, time::{Duration, Instant}};

#[test]
#[ignore = "opt-in packaged runtime smoke in an empty isolated workspace"]
fn packaged_supervisor_json_lines_and_graceful_exit() {
    let runtime = PathBuf::from(std::env::var_os("WECHAT_SHELL_SMOKE_RUNTIME").expect("Set WECHAT_SHELL_SMOKE_RUNTIME")).canonicalize().unwrap();
    let build = PathBuf::from(std::env::var_os("CARGO_TARGET_DIR").expect("Set CARGO_TARGET_DIR"));
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let root = build.join(format!("shell-job-smoke-{}-{nonce}", std::process::id()));
    std::fs::create_dir_all(&root).unwrap();
    let (process, mut stdin, stdout) = windows::Process::spawn(&runtime.join("python/python.exe"), &runtime, &root, false).unwrap();
    let (tx, rx) = mpsc::channel();
    let reader = std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            if tx.send(line).is_err() { break; }
        }
    });
    let mut ready = false;
    while !ready {
        let line = rx.recv_timeout(Duration::from_secs(180)).expect("ready timeout").unwrap();
        let value: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_ne!(value["event"], "fatal", "{line}");
        ready = value["event"] == "ready";
    }
    stdin.write_all(b"{\"id\":1,\"method\":\"snapshot\",\"params\":{}}\n{\"id\":2,\"method\":\"settings_get\",\"params\":{}}\n").unwrap();
    stdin.flush().unwrap();
    let mut ids = std::collections::HashSet::new();
    while ids.len() != 2 {
        let line = rx.recv_timeout(Duration::from_secs(15)).unwrap().unwrap();
        let value: serde_json::Value = serde_json::from_str(&line).unwrap();
        if let Some(id) = value["id"].as_u64() {
            assert!(value.get("result").is_some(), "{line}");
            if id == 1 {
                assert_eq!(value["result"]["versions"], serde_json::json!([]), "Fresh workspace must not auto-install a gateway");
            }
            ids.insert(id);
        }
    }
    assert_eq!(ids, [1, 2].into_iter().collect());
    // A verified installer is required; probing this boundary never launches one.
    stdin.write_all(b"{\"id\":4,\"method\":\"launcher_update_prepare_install\",\"params\":{}}\n").unwrap();
    stdin.flush().unwrap();
    loop {
        let line = rx.recv_timeout(Duration::from_secs(15)).unwrap().unwrap();
        let value: serde_json::Value = serde_json::from_str(&line).unwrap();
        if value["id"] == 4 {
            assert!(value["error"].as_str().unwrap().contains("请先下载"), "{line}");
            break;
        }
    }
    stdin.write_all(b"{\"id\":3,\"method\":\"shutdown\",\"params\":{}}\n").unwrap();
    stdin.flush().unwrap();
    drop(stdin);
    let deadline = Instant::now() + Duration::from_secs(30);
    while !process.exited().unwrap() {
        assert!(Instant::now() < deadline, "supervisor did not exit gracefully");
        std::thread::sleep(Duration::from_millis(50));
    }
    drop(process); // kill-on-close also reaps any surviving owned descendants.
    reader.join().unwrap();
    assert!(root.join("logs/supervisor.stderr.log").is_file());
}
