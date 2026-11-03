use std::{io::Write, path::{Path, PathBuf}, process::Command, os::windows::process::CommandExt};

fn shell_path(path: &Path) -> Result<PathBuf, String> {
    let value = path.to_str().ok_or("Update path is not valid Unicode")?;
    let value = if let Some(tail) = value.strip_prefix("\\\\?\\UNC\\") {
        format!("\\\\{tail}")
    } else { value.strip_prefix("\\\\?\\").unwrap_or(value).to_owned() };
    if value.encode_utf16().count() >= 260 { return Err("更新安装器路径过长，请使用较短的数据目录或手动安装".into()); }
    Ok(PathBuf::from(value))
}

pub fn handoff(installer: &Path, sha256: &str, workspace: &Path) -> Result<(), String> {
    if sha256.len() != 64 || !sha256.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid installer checksum".into());
    }
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|e| e.to_string())?.as_nanos();
    let script = installer.parent().ok_or("Invalid installer path")?.join(format!("install-update-{nonce}.ps1"));
    std::fs::OpenOptions::new().write(true).create_new(true).open(&script)
        .and_then(|mut file| file.write_all(include_bytes!("../windows/install-update.ps1"))).map_err(|e| e.to_string())?;
    let workspace = shell_path(workspace)?;
    let args = crate::paths::command_line(&[std::ffi::OsStr::new("--workspace"), workspace.as_os_str()]);
    let restart = String::from_utf16(&args[..args.len()-1]).map_err(|e| e.to_string())?;
    let shell = std::env::var_os("SystemRoot").ok_or("SystemRoot is missing")?;
    let shell = Path::new(&shell).join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let script_arg = shell_path(&script)?;
    let installer_arg = shell_path(installer)?;
    let launcher_arg = shell_path(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let lease = installer.parent().ok_or("Invalid installer path")?.join(".handoff");
    std::fs::OpenOptions::new().write(true).create_new(true).open(&lease).map_err(|e| e.to_string())?;
    let spawned = Command::new(shell)
        .env_remove("PSModulePath")
        .args(["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(script_arg).arg("-LauncherPid").arg(std::process::id().to_string())
        .arg("-Installer").arg(installer_arg).arg("-Sha256").arg(sha256)
        .arg("-LauncherExe").arg(launcher_arg)
        .arg("-RestartArguments").arg(restart)
        .creation_flags(0x08000000).spawn();
    if let Err(error) = spawned {
        let _ = std::fs::remove_file(lease);
        return Err(format!("Start update installer helper: {error}"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shell_handoff_preserves_drive_unc_and_spaces() {
        assert_eq!(shell_path(Path::new(r"\\?\D:\Launcher Data\setup.exe")).unwrap(), PathBuf::from(r"D:\Launcher Data\setup.exe"));
        assert_eq!(shell_path(Path::new(r"\\?\UNC\server\share\setup.exe")).unwrap(), PathBuf::from(r"\\server\share\setup.exe"));
        assert!(shell_path(Path::new(&format!("D:\\{}\\setup.exe", "a".repeat(260)))).is_err());
    }
}
