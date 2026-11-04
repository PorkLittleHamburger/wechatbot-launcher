//! Current-user Run entry and Task Manager override. Never touch other values.
use std::{io, path::Path};
use winreg::{enums::*, RegKey, RegValue};

const RUN: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const APPROVED: &str = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run";
const MARKER: &str = r"Software\WeChatOneBotLauncher\StartupRegistrations";

pub fn name(root: &Path) -> String {
    format!(
        "WeChat OneBot Launcher - {}",
        crate::paths::workspace_key(root)
    )
}

pub fn command(helper: &Path, task: &str) -> Result<String, String> {
    use std::path::{Component, Prefix};
    let helper = crate::paths::process_path(helper);
    // Explorer's login parser does not accept verbatim/device paths even when
    // CreateProcess and the filesystem accept them. Do not register a broken Run value.
    if !matches!(helper.components().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)))
        || !helper.is_absolute()
    {
        return Err("Windows 启动项需要普通本地路径，请将启动器安装到较短的本地目录".into());
    }
    let command = raw_command(&helper, task)?;
    if command.encode_utf16().count() >= 260 {
        return Err("Windows 启动项命令过长，请将启动器安装到较短的目录后再启用自启".into());
    }
    Ok(command)
}

pub fn raw_command(helper: &Path, task: &str) -> Result<String, String> {
    let wide = crate::paths::command_line(&[
        helper.as_os_str(),
        std::ffi::OsStr::new("--task"),
        std::ffi::OsStr::new(task),
    ]);
    String::from_utf16(&wide[..wide.len() - 1]).map_err(|e| e.to_string())
}

fn read(key: &str, name: &str) -> Result<Option<RegValue<'static>>, String> {
    match RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey_with_flags(key, KEY_READ | KEY_WOW64_64KEY)
        .and_then(|key| key.get_raw_value(name))
    {
        Ok(value) => Ok(Some(RegValue {
            vtype: value.vtype,
            bytes: value.bytes.into_owned().into(),
        })),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

fn write(key: &str, name: &str, value: Option<&RegValue>) -> Result<(), String> {
    let user = RegKey::predef(HKEY_CURRENT_USER);
    let result = if let Some(value) = value {
        user.create_subkey_with_flags(key, KEY_WRITE | KEY_WOW64_64KEY)
            .and_then(|(key, _)| key.set_raw_value(name, value))
    } else {
        user.open_subkey_with_flags(key, KEY_WRITE | KEY_WOW64_64KEY)
            .and_then(|key| key.delete_value(name))
    };
    match result {
        Ok(()) => Ok(()),
        Err(error) if value.is_none() && error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

pub struct Entry {
    pub name: String,
    pub(super) run: Option<RegValue<'static>>,
    pub(super) approval: Option<RegValue<'static>>,
    pub(super) marker: Option<RegValue<'static>>,
}

pub(super) fn owned(value: &RegValue) -> RegValue<'static> {
    RegValue {
        vtype: value.vtype.clone(),
        bytes: value.bytes.to_vec().into(),
    }
}
impl Clone for Entry {
    fn clone(&self) -> Self {
        Self {
            name: self.name.clone(),
            run: self.run.as_ref().map(owned),
            approval: self.approval.as_ref().map(owned),
            marker: self.marker.as_ref().map(owned),
        }
    }
}
impl PartialEq for Entry {
    fn eq(&self, other: &Self) -> bool {
        fn same(a: &Option<RegValue>, b: &Option<RegValue>) -> bool {
            match (a, b) {
                (Some(a), Some(b)) => a.vtype == b.vtype && a.bytes == b.bytes,
                (None, None) => true,
                _ => false,
            }
        }
        self.name == other.name
            && same(&self.run, &other.run)
            && same(&self.approval, &other.approval)
            && same(&self.marker, &other.marker)
    }
}

impl Entry {
    pub fn read(root: &Path) -> Result<Self, String> {
        let name = name(root);
        Ok(Self {
            run: read(RUN, &name)?,
            approval: read(APPROVED, &name)?,
            marker: read(MARKER, &name)?,
            name,
        })
    }
    pub fn registered(&self) -> bool {
        self.run.is_some()
    }
    pub fn known(&self) -> bool {
        self.marker.is_some() || self.registered()
    }
    pub fn enabled(&self) -> Result<bool, String> {
        if !self.registered() {
            return Ok(false);
        }
        approval_enabled(self.approval.as_ref())
    }
    pub fn verify_command(&self, command: &str, legacy: &str) -> Result<(), String> {
        use winreg::types::FromRegValue;
        if let Some(value) = &self.run {
            let actual = String::from_reg_value(value).map_err(|e| e.to_string())?;
            if actual != command && actual != legacy {
                return Err(
                    "自启入口属于其他安装路径，已保留原入口；请先在原安装中关闭自启".into(),
                );
            }
        }
        Ok(())
    }
    pub fn persist(&self, approval_written: bool) -> Result<(), String> {
        // Attempt every value, also during rollback after a partial write.
        let mut results = vec![
            write(RUN, &self.name, self.run.as_ref()),
            write(MARKER, &self.name, self.marker.as_ref()),
        ];
        if approval_written {
            results.push(write(APPROVED, &self.name, self.approval.as_ref()));
        }
        let errors: Vec<_> = results.into_iter().filter_map(Result::err).collect();
        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors.join("; "))
        }
    }
    #[cfg(test)]
    pub fn restore(&self) -> Result<(), String> {
        self.persist(true)
    }
    #[cfg(test)]
    pub fn empty(root: &Path) -> Self {
        Self {
            name: name(root),
            run: None,
            approval: None,
            marker: None,
        }
    }
    #[cfg(test)]
    pub fn disable(&mut self) {
        self.approval = Some(RegValue {
            vtype: REG_BINARY,
            bytes: vec![3, 0, 0, 0, 42, 0, 0, 0, 0, 0, 0, 0].into(),
        });
    }
}

fn approval_enabled(value: Option<&RegValue>) -> Result<bool, String> {
    let Some(value) = value else {
        return Ok(true);
    };
    if value.vtype == REG_BINARY && value.bytes.len() >= 4 {
        match u32::from_le_bytes(value.bytes[..4].try_into().unwrap()) {
            2 | 6 => return Ok(true),
            3 | 7 => return Ok(false),
            _ => {}
        }
    }
    Err("无法识别 Windows 启动项状态，未自动覆盖；请在任务管理器中确认，或明确重新开启自启".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn run_command_requires_short_local_path_and_preserves_identity() {
        let root = std::env::temp_dir().join(format!("startup path 中文 {}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        let helper = root.join("startup.exe");
        std::fs::write(&helper, []).unwrap();
        let verbatim = helper.canonicalize().unwrap();
        let task = "WeChatOneBotLauncher-S-1-5-isolated";
        let expected = command(&verbatim, task).unwrap();
        let normalized = expected
            .strip_prefix('"')
            .unwrap()
            .split_once('"')
            .unwrap()
            .0;
        assert_eq!(Path::new(normalized).canonicalize().unwrap(), verbatim);
        assert!(expected.ends_with(&format!("\"--task\" \"{task}\"")));
        assert!(!expected.contains(r"\\?\"));
        assert_ne!(raw_command(&verbatim, task).unwrap(), expected);
        for path in [
            r"\\?\UNC\server\share\startup.exe",
            r"\\.\C:\startup.exe",
            "startup.exe",
        ] {
            assert!(command(Path::new(path), task).is_err());
        }
        std::fs::remove_file(helper).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn task_manager_disable_is_recognized_even_without_timestamp() {
        assert!(approval_enabled(None).unwrap());
        for (flag, enabled) in [(2, true), (3, false), (6, true), (7, false)] {
            assert_eq!(
                approval_enabled(Some(&RegValue {
                    vtype: REG_BINARY,
                    bytes: vec![flag, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into()
                }))
                .unwrap(),
                enabled
            );
        }
        assert!(approval_enabled(Some(&RegValue {
            vtype: REG_BINARY,
            bytes: vec![0].into()
        }))
        .is_err());
    }
}
