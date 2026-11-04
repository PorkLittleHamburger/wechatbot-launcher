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
    } else if let Ok(text) = std::str::from_utf8(bytes) {
        text.trim_start_matches('\u{feff}').into()
    } else {
        // schtasks diagnostics use the Windows OEM code page, not UTF-8.
        use windows_sys::Win32::Globalization::MultiByteToWideChar;
        let length = unsafe { MultiByteToWideChar(1, 0, bytes.as_ptr(), bytes.len() as i32, std::ptr::null_mut(), 0) };
        let mut wide = vec![0u16; length.max(0) as usize];
        if length > 0 { unsafe { MultiByteToWideChar(1, 0, bytes.as_ptr(), bytes.len() as i32, wide.as_mut_ptr(), length); } }
        String::from_utf16_lossy(&wide)
    }
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

async fn configure_task(enabled: bool, root: &Path) -> Result<(), String> {
    let (ok, identity) = run("whoami.exe", &["/user", "/fo", "csv", "/nh"]).await?;
    if !ok { return Err(identity); }
    let sid = identity.trim().rsplit(',').next().unwrap_or("").trim_matches('"').trim();
    if !sid.starts_with("S-1-") || !sid.bytes().all(|c| c.is_ascii_digit() || c == b'S' || c == b'-') { return Err("Cannot resolve current Windows user SID".into()); }
    let name = format!("WeChatOneBotLauncher-{sid}-{}", crate::paths::workspace_key(root));
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let before = query(&name).await?;
    if let Some(content) = &before { verify_owned(content, sid, &exe)?; }
    let changed = async {
        let mutation = if enabled {
            let args = crate::paths::command_line(&[std::ffi::OsStr::new("--autostart"), std::ffi::OsStr::new("--workspace"), root.as_os_str()]);
            let args = String::from_utf16(&args[..args.len()-1]).map_err(|e| e.to_string())?;
            let content = format!(r#"<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><RegistrationInfo><Source>{SOURCE}</Source><Description>WeChat OneBot Launcher current-user logon</Description></RegistrationInfo><Triggers /><Principals><Principal id="Author"><UserId>{sid}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled></Settings><Actions Context="Author"><Exec><Command>{}</Command><Arguments>{}</Arguments><WorkingDirectory>{}</WorkingDirectory></Exec></Actions></Task>"#, xml(&exe.to_string_lossy()), xml(&args), xml(&exe.parent().ok_or("Executable directory missing")?.to_string_lossy()));
            install(&name, &content, root).await
        } else if before.is_some() {
            match run("schtasks.exe", &["/Delete", "/TN", &name, "/F"]).await {
                Ok((true, _)) => Ok(()), Ok((false, message)) | Err(message) => Err(message),
            }
        } else { Ok(()) };
        mutation?;
        // Verify what Windows persisted, rather than trusting the command exit code.
        match query(&name).await? {
            Some(content) if enabled => {
                verify_owned(&content, sid, &exe)?;
                let doc = roxmltree::Document::parse(content.trim()).map_err(|e| e.to_string())?;
                if doc.descendants().filter(|n| n.has_tag_name("Enabled")).any(|n| n.text() == Some("false")) {
                    return Err("Windows saved a disabled autostart task".into());
                }
            }
            None if !enabled => {}
            _ => return Err("Windows autostart task state did not match the saved setting".into()),
        }
        Ok::<(), String>(())
    }.await;
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

pub async fn configure(requested: bool, root: &Path, helper: &Path, explicit: bool) -> Result<bool, String> {
    let entry = crate::startup_entry::Entry::read(root)?;
    let (ok, identity) = run("whoami.exe", &["/user", "/fo", "csv", "/nh"]).await?;
    if !ok { return Err(identity); }
    let sid = identity.trim().rsplit(',').next().unwrap_or("").trim_matches('"').trim();
    if !sid.starts_with("S-1-") || !sid.bytes().all(|b| b.is_ascii_digit() || b == b'S' || b == b'-') { return Err("Invalid Windows SID".into()); }
    let name = format!("WeChatOneBotLauncher-{sid}-{}", crate::paths::workspace_key(root));
    let wide = crate::paths::command_line(&[helper.as_os_str(), std::ffi::OsStr::new("--task"), std::ffi::OsStr::new(&name)]);
    let command = String::from_utf16(&wide[..wide.len()-1]).map_err(|e| e.to_string())?;
    if command.encode_utf16().count() >= 260 {
        return Err("Windows 启动项命令过长，请将启动器安装到较短的目录后再启用自启".into());
    }
    entry.verify_command(&command)?;
    let enabled = if explicit || !entry.known() { requested } else { entry.enabled()? };
    // A Task Manager-disabled Run entry retains its on-demand task so Windows
    // can re-enable the entry later. The task itself has NO logon trigger.
    let need_task = enabled || (!explicit && entry.registered());
    if need_task && !helper.is_file() { return Err(format!("启动入口程序缺失：{}", helper.display())); }
    let before = query(&name).await?;
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    if let Some(content) = &before { verify_owned(content, sid, &exe)?; }
    let result = async {
        configure_task(need_task, root).await?;
        entry.apply(enabled, &command, explicit)?;
        let actual = crate::startup_entry::Entry::read(root)?;
        actual.verify_command(&command)?;
        actual.enabled()
    }.await;
    if result.is_err() {
        let registry = if explicit || !entry.known() { entry.restore_registration(explicit && enabled) } else { Ok(()) };
        let task = if let Some(content) = before { install(&name, &content, root).await }
            else { configure_task(false, root).await };
        if registry.is_err() || task.is_err() {
            return Err(format!("{}; rollback: registry={registry:?}, task={task:?}", result.unwrap_err()));
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    #[ignore = "registers only an isolated test task; never launches the application"]
    async fn missing_and_disabled_tasks_are_repaired_without_changing_checkbox() {
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root = std::env::temp_dir().join(format!("wechat task 中文 {nonce}"));
        std::fs::create_dir(&root).unwrap();
        let (_, identity) = run("whoami.exe", &["/user", "/fo", "csv", "/nh"]).await.unwrap();
        let sid = identity.trim().rsplit(',').next().unwrap().trim_matches('"');
        let name = format!("WeChatOneBotLauncher-{sid}-{}", crate::paths::workspace_key(&root));
        struct Cleanup(String, PathBuf);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::process::Command::new(system_exe("schtasks.exe").unwrap())
                    .args(["/Delete", "/TN", &self.0, "/F"]).output();
                let _ = std::fs::remove_dir(&self.1);
            }
        }
        let _cleanup = Cleanup(name.clone(), root.clone());
        assert!(query(&name).await.unwrap().is_none());
        if let Err(error) = configure_task(true, &root).await {
            // A non-elevated development shell cannot create HighestAvailable
            // tasks. Verify the failure is explicit and no partial task remains.
            assert!(error.contains("拒绝访问") || error.to_lowercase().contains("access is denied"), "{error}");
            assert!(query(&name).await.unwrap().is_none());
            return;
        }
        let first = query(&name).await.unwrap().unwrap();
        assert!(!first.contains("LogonTrigger"));
        verify_owned(&first, sid, &std::env::current_exe().unwrap()).unwrap();
        assert!(first.contains("--autostart") && first.contains("中文"));
        let (ok, message) = run("schtasks.exe", &["/Change", "/TN", &name, "/DISABLE"]).await.unwrap();
        assert!(ok, "{message}");
        configure_task(true, &root).await.unwrap();
        configure_task(false, &root).await.unwrap();
        assert!(query(&name).await.unwrap().is_none());
        configure_task(true, &root).await.unwrap();
        configure_task(false, &root).await.unwrap();
    }

    #[tokio::test]
    #[ignore = "isolated task/registry integration; never runs the task"]
    async fn task_manager_override_survives_reconcile_and_legacy_task_migration() {
        let root = std::env::temp_dir().join(format!("wechat-run-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir(&root).unwrap();
        let before = crate::startup_entry::Entry::read(&root).unwrap();
        struct Restore(crate::startup_entry::Entry, PathBuf);
        impl Drop for Restore {
            fn drop(&mut self) { self.0.restore().unwrap(); let _ = std::fs::remove_dir(&self.1); }
        }
        let _restore = Restore(before, root.clone());
        let helper = Path::new(env!("CARGO_MANIFEST_DIR")).join("resources/startup/wechat-onebot-startup.exe");
        if let Err(error) = configure(true, &root, &helper, true).await {
            assert!(error.contains("拒绝访问") || error.to_lowercase().contains("access is denied"), "{error}");
            assert!(!crate::startup_entry::Entry::read(&root).unwrap().registered());
            return;
        }
        let (_, identity) = run("whoami.exe", &["/user", "/fo", "csv", "/nh"]).await.unwrap();
        let sid = identity.trim().rsplit(',').next().unwrap().trim_matches('"');
        let name = format!("WeChatOneBotLauncher-{sid}-{}", crate::paths::workspace_key(&root));
        struct DeleteTask(String);
        impl Drop for DeleteTask {
            fn drop(&mut self) { let _ = std::process::Command::new(system_exe("schtasks.exe").unwrap()).args(["/Delete", "/TN", &self.0, "/F"]).output(); }
        }
        let _delete = DeleteTask(name.clone());
        let content = query(&name).await.unwrap().unwrap();
        let legacy = content.replace("<Triggers />", &format!("<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{sid}</UserId></LogonTrigger></Triggers>"));
        assert!(legacy.contains("LogonTrigger"));
        install(&name, &legacy, &root).await.unwrap();
        let key = winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER).create_subkey(r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run").unwrap().0;
        let entry_name = crate::startup_entry::name(&root);
        let disabled = winreg::RegValue {vtype: winreg::enums::REG_BINARY, bytes: vec![3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into()};
        key.set_raw_value(&entry_name, &disabled).unwrap();
        assert!(!configure(true, &root, &helper, false).await.unwrap());
        assert!(!query(&name).await.unwrap().unwrap().contains("LogonTrigger"));
        assert_eq!(key.get_raw_value(&entry_name).unwrap().bytes, disabled.bytes);
        assert!(!configure(false, &root, &helper, false).await.unwrap());
        key.set_raw_value(&entry_name, &winreg::RegValue {vtype: winreg::enums::REG_BINARY, bytes: vec![2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into()}).unwrap();
        assert!(configure(false, &root, &helper, false).await.unwrap());
        assert!(configure(true, &root, &helper, true).await.unwrap());
        assert!(!configure(false, &root, &helper, true).await.unwrap());
        assert!(query(&name).await.unwrap().is_none());
        assert!(!configure(true, &root, &helper, false).await.unwrap());
    }
}
