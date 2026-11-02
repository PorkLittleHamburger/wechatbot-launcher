use std::{path::{Path, PathBuf}, time::Duration};
use tokio::{process::Command, time::timeout};

const SOURCE: &str = "com.porklittlehamburger.wechat-onebot-launcher";

fn system_exe(name: &str) -> Result<PathBuf, String> {
    Ok(PathBuf::from(std::env::var_os("SystemRoot").ok_or("SystemRoot missing")?).join("System32").join(name))
}

fn decode(bytes: &[u8]) -> String {
    if bytes.starts_with(&[0xff, 0xfe]) || bytes.get(1) == Some(&0) {
        let bytes = bytes.strip_prefix(&[0xff, 0xfe]).unwrap_or(bytes);
        String::from_utf16_lossy(&bytes.chunks_exact(2).map(|p| u16::from_le_bytes([p[0], p[1]])).collect::<Vec<_>>())
    } else { String::from_utf8_lossy(bytes).trim_start_matches('\u{feff}').into() }
}

async fn run(program: &str, args: &[&str]) -> Result<(bool, String), String> {
    let mut cmd = Command::new(system_exe(program)?);
    cmd.args(args).creation_flags(0x08000000).kill_on_drop(true);
    let output = timeout(Duration::from_secs(10), cmd.output()).await.map_err(|_| format!("{program} timed out"))?.map_err(|e| e.to_string())?;
    let message = format!("{}{}", decode(&output.stdout), decode(&output.stderr));
    Ok((output.status.success(), message))
}

fn xml(value: &str) -> String {
    value.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&apos;")
}

async fn query(name: &str) -> Result<Option<String>, String> {
    let (ok, content) = run("schtasks.exe", &["/Query", "/TN", name, "/XML"]).await?;
    if ok { return Ok(Some(content)); }
    // Do not mistake access denied or a broken scheduler for an absent task.
    let (ok, list) = run("schtasks.exe", &["/Query", "/FO", "CSV", "/NH"]).await?;
    if !ok { return Err(format!("Cannot inspect scheduled tasks: {list}")); }
    if list.lines().any(|line| line.split(',').next().unwrap_or("").trim_matches('"').trim_start_matches('\\') == name) { return Err(format!("Cannot read existing task: {content}")); }
    Ok(None)
}

fn verify_owned(content: &str, sid: &str, exe: &Path) -> Result<(), String> {
    let doc = roxmltree::Document::parse(content.trim()).map_err(|e| format!("Invalid scheduled task XML: {e}"))?;
    let text = |name: &str| doc.descendants().find(|n| n.has_tag_name(name)).and_then(|n| n.text()).unwrap_or("");
    if text("Source") != SOURCE || text("UserId") != sid || !text("Command").eq_ignore_ascii_case(&exe.to_string_lossy()) { return Err("Existing scheduled task is not owned by this desktop installation; left unchanged".into()); }
    Ok(())
}

async fn install(name: &str, content: &str, root: &Path) -> Result<(), String> {
    let path = root.join(format!(".desktop-task-{}.xml", std::process::id()));
    let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&path).map_err(|e| format!("Create task XML: {e}"))?;
    use std::io::Write;
    let result = (|| {
        file.write_all(&[0xff, 0xfe])?;
        for unit in content.encode_utf16() { file.write_all(&unit.to_le_bytes())?; }
        file.flush()
    })();
    drop(file);
    if let Err(error) = result { let _ = std::fs::remove_file(&path); return Err(error.to_string()); }
    let result = run("schtasks.exe", &["/Create", "/TN", name, "/XML", path.to_str().ok_or("Task XML path is not Unicode")?, "/F"]).await;
    let _ = std::fs::remove_file(path);
    let (ok, message) = result?;
    if ok { Ok(()) } else { Err(message) }
}

pub async fn configure(enabled: bool, root: &Path) -> Result<(), String> {
    let (ok, identity) = run("whoami.exe", &["/user", "/fo", "csv", "/nh"]).await?;
    if !ok { return Err(identity); }
    let sid = identity.trim().rsplit(',').next().unwrap_or("").trim_matches('"').trim();
    if !sid.starts_with("S-1-") || !sid.bytes().all(|c| c.is_ascii_digit() || c == b'S' || c == b'-') { return Err("Cannot resolve current Windows user SID".into()); }
    let name = format!("WeChatOneBotLauncher-{sid}-{}", crate::paths::workspace_key(root));
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let before = query(&name).await?;
    if let Some(content) = &before { verify_owned(content, sid, &exe)?; }
    let changed = if enabled {
        let args = crate::paths::command_line(&[std::ffi::OsStr::new("--autostart"), std::ffi::OsStr::new("--workspace"), root.as_os_str()]);
        let args = String::from_utf16(&args[..args.len()-1]).map_err(|e| e.to_string())?;
        let content = format!(r#"<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><RegistrationInfo><Source>{SOURCE}</Source><Description>WeChat OneBot Launcher current-user logon</Description></RegistrationInfo><Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{sid}</UserId></LogonTrigger></Triggers><Principals><Principal id="Author"><UserId>{sid}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled></Settings><Actions Context="Author"><Exec><Command>{}</Command><Arguments>{}</Arguments><WorkingDirectory>{}</WorkingDirectory></Exec></Actions></Task>"#, xml(&exe.to_string_lossy()), xml(&args), xml(&exe.parent().ok_or("Executable directory missing")?.to_string_lossy()));
        install(&name, &content, root).await
    } else if before.is_some() {
        match run("schtasks.exe", &["/Delete", "/TN", &name, "/F"]).await {
            Ok((true, _)) => Ok(()), Ok((false, message)) | Err(message) => Err(message),
        }
    } else { Ok(()) };
    if let Err(error) = changed {
        // Restore the prior task as well as the Python settings transaction.
        let rollback = match before {
            Some(content) => install(&name, &content, root).await,
            None => match query(&name).await {
                Ok(Some(content)) => {
                    verify_owned(&content, sid, &exe)?;
                    match run("schtasks.exe", &["/Delete", "/TN", &name, "/F"]).await { Ok((true, _)) => Ok(()), Ok((false, e)) | Err(e) => Err(e) }
                }
                Ok(None) => Ok(()), Err(error) => Err(error),
            },
        };
        return Err(format!("{error}{}", rollback.err().map(|e| format!("; task rollback failed: {e}")).unwrap_or_default()));
    }
    Ok(())
}
