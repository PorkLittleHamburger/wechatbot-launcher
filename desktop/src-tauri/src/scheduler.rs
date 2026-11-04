use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{process::Command, time::timeout};

fn system_exe(name: &str) -> Result<PathBuf, String> {
    Ok(
        PathBuf::from(std::env::var_os("SystemRoot").ok_or("SystemRoot missing")?)
            .join("System32")
            .join(name),
    )
}

fn decode(bytes: &[u8]) -> String {
    if bytes.starts_with(&[0xff, 0xfe]) || bytes.get(1) == Some(&0) {
        let bytes = bytes.strip_prefix(&[0xff, 0xfe]).unwrap_or(bytes);
        String::from_utf16_lossy(
            &bytes
                .chunks_exact(2)
                .map(|p| u16::from_le_bytes([p[0], p[1]]))
                .collect::<Vec<_>>(),
        )
    } else if let Ok(text) = std::str::from_utf8(bytes) {
        text.trim_start_matches('\u{feff}').into()
    } else {
        // schtasks diagnostics use the Windows OEM code page, not UTF-8.
        use windows_sys::Win32::Globalization::MultiByteToWideChar;
        let length = unsafe {
            MultiByteToWideChar(
                1,
                0,
                bytes.as_ptr(),
                bytes.len() as i32,
                std::ptr::null_mut(),
                0,
            )
        };
        let mut wide = vec![0u16; length.max(0) as usize];
        if length > 0 {
            unsafe {
                MultiByteToWideChar(
                    1,
                    0,
                    bytes.as_ptr(),
                    bytes.len() as i32,
                    wide.as_mut_ptr(),
                    length,
                );
            }
        }
        String::from_utf16_lossy(&wide)
    }
}

async fn run(program: &str, args: &[&str]) -> Result<(bool, String), String> {
    let mut cmd = Command::new(system_exe(program)?);
    cmd.args(args).creation_flags(0x08000000).kill_on_drop(true);
    let output = timeout(Duration::from_secs(10), cmd.output())
        .await
        .map_err(|_| format!("{program} timed out"))?
        .map_err(|e| e.to_string())?;
    let message = format!("{}{}", decode(&output.stdout), decode(&output.stderr));
    Ok((output.status.success(), message))
}

pub async fn query(name: &str) -> Result<Option<String>, String> {
    // schtasks /Query encodes XML in the machine's OEM code page. English
    // Windows can irreversibly replace Chinese paths with '?'. COM returns a
    // Unicode BSTR; write UTF-8 bytes directly, independently of console locale.
    let script = format!(
        r#"$ErrorActionPreference='Stop';$code=0;$text='';try{{$service=[Activator]::CreateInstance([Type]::GetTypeFromProgID('Schedule.Service'));$service.Connect();$text=$service.GetFolder('\').GetTask('{}').Xml}}catch{{$taskException=$_.Exception.GetBaseException();if($taskException.HResult -ne -2147024894 -and $taskException.HResult -ne -2147024893){{$text=$taskException.Message;$code=1}}}};$bytes=[Text.Encoding]::UTF8.GetBytes($text);$stream=[Console]::OpenStandardOutput();$stream.Write($bytes,0,$bytes.Length);exit $code"#,
        name.replace('\'', "''")
    );
    let (ok, content) = run(
        "WindowsPowerShell/v1.0/powershell.exe",
        &[
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            &script,
        ],
    )
    .await?;
    if !ok {
        return Err(format!("Cannot inspect scheduled task: {content}"));
    }
    Ok((!content.trim().is_empty()).then_some(content))
}

pub async fn install(name: &str, content: &str, root: &Path) -> Result<(), String> {
    let path = root.join(format!(".desktop-task-{}.xml", std::process::id()));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|e| format!("Create task XML: {e}"))?;
    use std::io::Write;
    let result = (|| {
        file.write_all(&[0xff, 0xfe])?;
        for unit in content.encode_utf16() {
            file.write_all(&unit.to_le_bytes())?;
        }
        file.flush()
    })();
    drop(file);
    if let Err(error) = result {
        let _ = std::fs::remove_file(&path);
        return Err(error.to_string());
    }
    let result = run(
        "schtasks.exe",
        &[
            "/Create",
            "/TN",
            name,
            "/XML",
            path.to_str().ok_or("Task XML path is not Unicode")?,
            "/F",
        ],
    )
    .await;
    let _ = std::fs::remove_file(path);
    let (ok, message) = result?;
    if ok {
        Ok(())
    } else {
        Err(message)
    }
}

pub async fn current_sid() -> Result<String, String> {
    let (ok, identity) = run("whoami.exe", &["/user", "/fo", "csv", "/nh"]).await?;
    if !ok {
        return Err(identity);
    }
    let sid = identity
        .trim()
        .rsplit(',')
        .next()
        .ok_or("Windows SID missing")?
        .trim_matches('"')
        .trim();
    if !sid.starts_with("S-1-")
        || !sid
            .bytes()
            .all(|b| b.is_ascii_digit() || b == b'S' || b == b'-')
    {
        return Err("Invalid Windows SID".into());
    }
    Ok(sid.into())
}

pub async fn delete(name: &str) -> Result<(), String> {
    match run("schtasks.exe", &["/Delete", "/TN", name, "/F"]).await? {
        (true, _) => Ok(()),
        (false, message) => Err(message),
    }
}
