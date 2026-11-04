//! Current-user Run entry and Task Manager override. Never touch other values.
use std::{io, path::Path};
use winreg::{RegKey, RegValue, enums::*};

const RUN: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const APPROVED: &str = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run";
const MARKER: &str = r"Software\WeChatOneBotLauncher\StartupRegistrations";

pub fn name(root: &Path) -> String { format!("WeChat OneBot Launcher - {}", crate::paths::workspace_key(root)) }

fn read(key: &str, name: &str) -> Result<Option<RegValue<'static>>, String> {
    match RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(key, KEY_READ | KEY_WOW64_64KEY)
        .and_then(|key| key.get_raw_value(name)) {
        Ok(value) => Ok(Some(RegValue { vtype: value.vtype, bytes: value.bytes.into_owned().into() })),
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
        user.open_subkey_with_flags(key, KEY_WRITE | KEY_WOW64_64KEY).and_then(|key| key.delete_value(name))
    };
    match result {
        Ok(()) => Ok(()),
        Err(error) if value.is_none() && error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

pub struct Entry {
    pub name: String,
    run: Option<RegValue<'static>>,
    approval: Option<RegValue<'static>>,
    marker: Option<RegValue<'static>>,
}

impl Entry {
    pub fn read(root: &Path) -> Result<Self, String> {
        let name = name(root);
        Ok(Self { run: read(RUN, &name)?, approval: read(APPROVED, &name)?, marker: read(MARKER, &name)?, name })
    }
    pub fn registered(&self) -> bool { self.run.is_some() }
    pub fn known(&self) -> bool { self.marker.is_some() || self.registered() }
    pub fn enabled(&self) -> Result<bool, String> {
        if !self.registered() { return Ok(false); }
        approval_enabled(self.approval.as_ref())
    }
    pub fn verify_command(&self, command: &str) -> Result<(), String> {
        use winreg::types::FromRegValue;
        if let Some(value) = &self.run {
            let actual = String::from_reg_value(value).map_err(|e| e.to_string())?;
            if actual != command { return Err("自启入口属于其他安装路径，已保留原入口；请先在原安装中关闭自启".into()); }
        }
        Ok(())
    }
    pub fn apply(&self, enabled: bool, command: &str, explicit: bool) -> Result<(), String> {
        use winreg::types::ToRegValue;
        if explicit || !self.known() {
            write(RUN, &self.name, enabled.then(|| command.to_reg_value()).as_ref())?;
            // Only an explicit Enable may undo a Task Manager disable.
            if explicit && enabled {
                write(APPROVED, &self.name, Some(&RegValue { vtype: REG_BINARY, bytes: vec![2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into() }))?;
            }
            write(MARKER, &self.name, Some(&1u32.to_reg_value()))?;
        }
        Ok(())
    }
    #[cfg(test)]
    pub fn restore(&self) -> Result<(), String> {
        let results = [write(RUN, &self.name, self.run.as_ref()), write(APPROVED, &self.name, self.approval.as_ref()), write(MARKER, &self.name, self.marker.as_ref())];
        let errors: Vec<_> = results.into_iter().filter_map(Result::err).collect();
        if errors.is_empty() { Ok(()) } else { Err(errors.join("; ")) }
    }
    pub fn restore_registration(&self, approval_written: bool) -> Result<(), String> {
        // Reconciliation never writes StartupApproved. Do not overwrite an
        // external Task Manager change when rolling back an unrelated failure.
        let mut results = vec![write(RUN, &self.name, self.run.as_ref()), write(MARKER, &self.name, self.marker.as_ref())];
        if approval_written { results.push(write(APPROVED, &self.name, self.approval.as_ref())); }
        let errors: Vec<_> = results.into_iter().filter_map(Result::err).collect();
        if errors.is_empty() { Ok(()) } else { Err(errors.join("; ")) }
    }
}

fn approval_enabled(value: Option<&RegValue>) -> Result<bool, String> {
    let Some(value) = value else { return Ok(true); };
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
    fn task_manager_disable_is_recognized_even_without_timestamp() {
        assert!(approval_enabled(None).unwrap());
        for (flag, enabled) in [(2, true), (3, false), (6, true), (7, false)] {
            assert_eq!(approval_enabled(Some(&RegValue {vtype: REG_BINARY, bytes: vec![flag, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into()})).unwrap(), enabled);
        }
        assert!(approval_enabled(Some(&RegValue {vtype: REG_BINARY, bytes: vec![0].into()})).is_err());
    }
    #[test]
    fn real_registry_disable_delete_and_explicit_enable_are_distinct() {
        let root = std::env::temp_dir().join(format!("startup-registry-{}-{:?}", std::process::id(), std::time::SystemTime::now()));
        let before = Entry::read(&root).unwrap();
        struct Restore(Entry);
        impl Drop for Restore { fn drop(&mut self) { self.0.restore().unwrap(); } }
        let _restore = Restore(Entry::read(&root).unwrap());
        assert!(!before.known());
        before.apply(true, "isolated fixture never executed", true).unwrap();
        write(APPROVED, &before.name, Some(&RegValue {vtype: REG_BINARY, bytes: vec![3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into()})).unwrap();
        let disabled = Entry::read(&root).unwrap();
        assert!(!disabled.enabled().unwrap());
        disabled.apply(true, "isolated fixture never executed", false).unwrap();
        assert!(!Entry::read(&root).unwrap().enabled().unwrap());
        disabled.apply(true, "isolated fixture never executed", true).unwrap();
        assert!(Entry::read(&root).unwrap().enabled().unwrap());
        write(RUN, &before.name, None).unwrap();
        let deleted = Entry::read(&root).unwrap();
        assert!(deleted.known());
        deleted.apply(true, "isolated fixture never executed", false).unwrap();
        assert!(!Entry::read(&root).unwrap().registered());
    }
}
