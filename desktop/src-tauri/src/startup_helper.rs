//! Unelevated, console-free Run entry. The task was authorized by the launcher.
#![windows_subsystem = "windows"]
use std::{os::windows::process::CommandExt, process::Command};
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 || args[0] != "--task" || !args[1].starts_with("WeChatOneBotLauncher-S-1-")
        || !args[1].bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') { std::process::exit(2); }
    let Some(system) = std::env::var_os("SystemRoot") else { std::process::exit(2); };
    let result = Command::new(std::path::Path::new(&system).join("System32/schtasks.exe"))
        .args(["/Run", "/TN", &args[1]]).creation_flags(0x08000000).output();
    if result.as_ref().is_ok_and(|r| r.status.success()) { return; }
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        use std::io::Write;
        let directory = std::path::Path::new(&local).join("WeChatOneBotLauncherShell");
        let _ = std::fs::create_dir_all(&directory);
        if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(directory.join("startup.log")) {
            let _ = writeln!(file, "{:?} {}: {:?}", std::time::SystemTime::now(), args[1], result);
        }
    }
    std::process::exit(1);
}
