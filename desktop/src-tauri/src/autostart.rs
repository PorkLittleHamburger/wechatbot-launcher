//! One owner for task/Run snapshots, validation, mutations and rollback.
use crate::{scheduler, startup_entry::Entry};
use std::path::{Path, PathBuf};
use winreg::{enums::REG_BINARY, RegValue};
const SOURCE: &str = "com.porklittlehamburger.wechat-onebot-launcher";

fn xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn verify_owned(content: &str, sid: &str, exe: &Path) -> Result<bool, String> {
    let doc = roxmltree::Document::parse(content.trim())
        .map_err(|e| format!("Invalid scheduled task XML: {e}"))?;
    let text = |name: &str| {
        doc.descendants()
            .find(|n| n.has_tag_name(name))
            .and_then(|n| n.text())
    };
    if text("Source") != Some(SOURCE)
        || text("UserId") != Some(sid)
        || !text("Command")
            .is_some_and(|command| command.eq_ignore_ascii_case(&exe.to_string_lossy()))
    {
        return Err(
            "Existing scheduled task is not owned by this desktop installation; left unchanged"
                .into(),
        );
    }
    Ok(!doc
        .descendants()
        .filter(|n| n.has_tag_name("Enabled"))
        .any(|n| n.text() == Some("false")))
}

fn task_xml(sid: &str, exe: &Path, root: &Path) -> Result<String, String> {
    let args = crate::paths::command_line(&[
        std::ffi::OsStr::new("--autostart"),
        std::ffi::OsStr::new("--workspace"),
        root.as_os_str(),
    ]);
    let args = String::from_utf16(&args[..args.len() - 1]).map_err(|e| e.to_string())?;
    let content = format!(
        r#"<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><RegistrationInfo><Source>{SOURCE}</Source><Description>WeChat OneBot Launcher current-user logon</Description></RegistrationInfo><Triggers /><Principals><Principal id="Author"><UserId>{sid}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled></Settings><Actions Context="Author"><Exec><Command>{}</Command><Arguments>{}</Arguments><WorkingDirectory>{}</WorkingDirectory></Exec></Actions></Task>"#,
        xml(&exe.to_string_lossy()),
        xml(&args),
        xml(&exe
            .parent()
            .ok_or("Executable directory missing")?
            .to_string_lossy())
    );
    Ok(content)
}

fn plan_entry(entry: &Entry, enabled: bool, command: &str, explicit: bool) -> Entry {
    use winreg::types::ToRegValue;
    let mut next = entry.clone();
    if explicit || !entry.known() {
        next.run = enabled.then(|| crate::startup_entry::owned(&command.to_reg_value()));
        if explicit && enabled {
            next.approval = Some(RegValue {
                vtype: REG_BINARY,
                bytes: vec![2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into(),
            });
        }
        next.marker = Some(crate::startup_entry::owned(&1u32.to_reg_value()));
    } else if entry.registered() {
        next.run = Some(crate::startup_entry::owned(&command.to_reg_value()));
    }
    next
}

// No ownership or rollback policy in the system adapter. Tests use memory.
trait System {
    async fn query(&mut self, name: &str) -> Result<Option<String>, String>;
    async fn install(&mut self, name: &str, xml: &str, root: &Path) -> Result<(), String>;
    async fn delete(&mut self, name: &str) -> Result<(), String>;
    fn read_entry(&mut self, root: &Path) -> Result<Entry, String>;
    fn write_entry(&mut self, entry: &Entry, approval: bool) -> Result<(), String>;
}
struct Windows;
impl System for Windows {
    async fn query(&mut self, name: &str) -> Result<Option<String>, String> {
        scheduler::query(name).await
    }
    async fn install(&mut self, name: &str, xml: &str, root: &Path) -> Result<(), String> {
        scheduler::install(name, xml, root).await
    }
    async fn delete(&mut self, name: &str) -> Result<(), String> {
        scheduler::delete(name).await
    }
    fn read_entry(&mut self, root: &Path) -> Result<Entry, String> {
        Entry::read(root)
    }
    fn write_entry(&mut self, entry: &Entry, approval: bool) -> Result<(), String> {
        entry.persist(approval)
    }
}

struct Transaction<'a> {
    root: &'a Path,
    sid: String,
    name: String,
    exe: PathBuf,
    command: String,
    entry: Entry,
    task: Option<String>,
}
impl<'a> Transaction<'a> {
    async fn capture(
        system: &mut impl System,
        root: &'a Path,
        helper: &Path,
        sid: String,
        exe: PathBuf,
    ) -> Result<Self, String> {
        let name = format!(
            "WeChatOneBotLauncher-{sid}-{}",
            crate::paths::workspace_key(root)
        );
        let command = crate::startup_entry::command(helper, &name)?;
        // Accept exactly the old 0.8.6 spelling for this same helper and task.
        // Keep disable/removal available when the helper is already missing.
        let legacy = crate::startup_entry::raw_command(
            &helper
                .canonicalize()
                .unwrap_or_else(|_| helper.to_path_buf()),
            &name,
        )?;
        let entry = system.read_entry(root)?;
        entry.verify_command(&command, &legacy)?;
        let task = system.query(&name).await?;
        if let Some(content) = &task {
            verify_owned(content, &sid, &exe)?;
        }
        Ok(Self {
            root,
            sid,
            name,
            exe,
            command,
            entry,
            task,
        })
    }
    async fn apply(
        &self,
        system: &mut impl System,
        requested: bool,
        explicit: bool,
        helper: &Path,
    ) -> Result<bool, String> {
        let enabled = if explicit || !self.entry.known() {
            requested
        } else {
            self.entry.enabled()?
        };
        // A disabled Run entry keeps its on-demand task without logon triggers.
        let need_task = enabled || (!explicit && self.entry.registered());
        if need_task && !helper.is_file() {
            return Err(format!("启动入口程序缺失：{}", helper.display()));
        }
        let desired = plan_entry(&self.entry, enabled, &self.command, explicit);
        let approval_written = explicit && enabled;
        let content = need_task
            .then(|| task_xml(&self.sid, &self.exe, self.root))
            .transpose()?;
        let mut task_attempted = false;
        let mut registry_attempted = false;
        let result = async {
            if let Some(content) = &content {
                task_attempted = true;
                system.install(&self.name, content, self.root).await?;
            } else if self.task.is_some() {
                task_attempted = true;
                system.delete(&self.name).await?;
            }
            match system.query(&self.name).await? {
                Some(content) if need_task => {
                    if !verify_owned(&content, &self.sid, &self.exe)? {
                        return Err("Windows saved a disabled autostart task".into());
                    }
                }
                None if !need_task => {}
                _ => {
                    return Err(
                        "Windows autostart task state did not match the saved setting".into(),
                    )
                }
            }
            if desired != self.entry {
                registry_attempted = true;
                system.write_entry(&desired, approval_written)?;
            }
            let actual = system.read_entry(self.root)?;
            actual.verify_command(&self.command, &self.command)?;
            actual.enabled()
        }
        .await;
        match result {
            Ok(actual) => Ok(actual),
            Err(error) => {
                // Restore both resources even if one fails. Never overwrite a
                // task replaced by another owner after a failed system call.
                let registry = if registry_attempted {
                    system.write_entry(&self.entry, approval_written)
                } else {
                    Ok(())
                };
                let task = if task_attempted {
                    self.restore_task(system).await
                } else {
                    Ok(())
                };
                let mut errors = vec![error];
                if let Err(e) = registry {
                    errors.push(format!("registry rollback failed: {e}"));
                }
                if let Err(e) = task {
                    errors.push(format!("task rollback failed: {e}"));
                }
                Err(errors.join("; "))
            }
        }
    }
    async fn restore_task(&self, system: &mut impl System) -> Result<(), String> {
        let current = system.query(&self.name).await?;
        if let Some(content) = &current {
            verify_owned(content, &self.sid, &self.exe)?;
        }
        if self.task == current {
            return Ok(());
        }
        match (&self.task, current) {
            (Some(before), _) => system.install(&self.name, before, self.root).await,
            (None, Some(_)) => system.delete(&self.name).await,
            (None, None) => Ok(()),
        }
    }
}

pub async fn configure(
    requested: bool,
    root: &Path,
    helper: &Path,
    explicit: bool,
) -> Result<bool, String> {
    let sid = scheduler::current_sid().await?;
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut system = Windows;
    Transaction::capture(&mut system, root, helper, sid, exe)
        .await?
        .apply(&mut system, requested, explicit, helper)
        .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scheduler::{current_sid, install, query};
    struct Memory {
        entry: Entry,
        task: Option<String>,
        queries: usize,
        installs: usize,
        deletes: usize,
        writes: usize,
        fail: Option<&'static str>,
        rollback_fails: bool,
        external_disable: bool,
    }
    impl System for Memory {
        async fn query(&mut self, _: &str) -> Result<Option<String>, String> {
            self.queries += 1;
            if self.queries == 2 && self.fail == Some("query") {
                return Err("query failure".into());
            }
            Ok(self.task.clone())
        }
        async fn install(&mut self, _: &str, xml: &str, _: &Path) -> Result<(), String> {
            self.installs += 1;
            if self.installs > 1 && self.rollback_fails {
                return Err("restore task failure".into());
            }
            if self.fail == Some("install_unchanged") {
                return Err("install rejected without mutation".into());
            }
            self.task = Some(xml.into());
            if self.installs == 1 {
                match self.fail {
                    Some("install") => return Err("partial install failure".into()),
                    Some("disabled") => {
                        self.task =
                            Some(xml.replace("<Enabled>true</Enabled>", "<Enabled>false</Enabled>"))
                    }
                    Some("foreign") => {
                        self.task = Some(xml.replace(SOURCE, "another owner"));
                        return Err("install failure".into());
                    }
                    _ => {}
                }
            }
            Ok(())
        }
        async fn delete(&mut self, _: &str) -> Result<(), String> {
            self.deletes += 1;
            self.task = None;
            if self.deletes == 1 && self.fail == Some("delete") {
                return Err("partial delete failure".into());
            }
            Ok(())
        }
        fn read_entry(&mut self, _: &Path) -> Result<Entry, String> {
            if self.writes > 0 && self.fail == Some("registry_read") {
                return Err("registry readback failure".into());
            }
            Ok(self.entry.clone())
        }
        fn write_entry(&mut self, entry: &Entry, approval: bool) -> Result<(), String> {
            self.writes += 1;
            if self.writes > 1 && self.rollback_fails {
                return Err("restore registry failure".into());
            }
            let previous_approval = self.entry.approval.take();
            self.entry = entry.clone();
            if !approval {
                self.entry.approval = previous_approval;
            }
            if self.writes == 1 {
                if self.external_disable {
                    self.entry.disable();
                }
                if matches!(self.fail, Some("registry" | "registry_external")) {
                    return Err("partial registry failure".into());
                }
            }
            Ok(())
        }
    }
    struct Fixture {
        root: PathBuf,
        helper: PathBuf,
        exe: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            static NEXT_FIXTURE: std::sync::atomic::AtomicU64 =
                std::sync::atomic::AtomicU64::new(0);
            let sequence = NEXT_FIXTURE.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let nonce = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = std::env::temp_dir().join(format!(
                "autostart 中文 {}-{nonce}-{sequence}",
                std::process::id()
            ));
            std::fs::create_dir(&root).unwrap();
            let helper = root.join("startup.exe");
            std::fs::write(&helper, []).unwrap();
            let exe = root.join("launcher.exe");
            Self { root, helper, exe }
        }
        fn memory(&self) -> Memory {
            Memory {
                entry: Entry::empty(&self.root),
                task: None,
                queries: 0,
                installs: 0,
                deletes: 0,
                writes: 0,
                fail: None,
                rollback_fails: false,
                external_disable: false,
            }
        }
        async fn capture<'a>(&'a self, system: &mut Memory) -> Result<Transaction<'a>, String> {
            Transaction::capture(
                system,
                &self.root,
                &self.helper,
                "S-1-5-21-123".into(),
                self.exe.clone(),
            )
            .await
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.helper);
            let _ = std::fs::remove_dir(&self.root);
        }
    }

    #[tokio::test]
    async fn task_manager_disable_and_exact_legacy_migration_keep_approval_bytes() {
        let f = Fixture::new();
        let mut m = f.memory();
        let tx = f.capture(&mut m).await.unwrap();
        let legacy =
            crate::startup_entry::raw_command(&f.helper.canonicalize().unwrap(), &tx.name).unwrap();
        m.entry = plan_entry(&m.entry, true, &legacy, true);
        m.entry.disable();
        let approval = m.entry.approval.as_ref().unwrap().bytes.to_vec();
        let old_task = task_xml(&tx.sid, &tx.exe, &f.root)
            .unwrap()
            .replace("<Triggers />", "<Triggers><LogonTrigger /></Triggers>");
        m.task = Some(old_task);
        let tx = f.capture(&mut m).await.unwrap();
        assert!(!tx.apply(&mut m, true, false, &f.helper).await.unwrap());
        m.entry.verify_command(&tx.command, &tx.command).unwrap();
        assert_eq!(
            m.entry.approval.as_ref().unwrap().bytes.as_ref(),
            approval.as_slice()
        );
        assert!(!m.task.as_ref().unwrap().contains("LogonTrigger"));
        // Reconcile repairs a missing/disabled task, preserving Windows choice.
        m.task = None;
        assert!(!f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, true, false, &f.helper)
            .await
            .unwrap());
        m.task = m
            .task
            .map(|xml| xml.replace("<Enabled>true</Enabled>", "<Enabled>false</Enabled>"));
        assert!(!f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, false, false, &f.helper)
            .await
            .unwrap());
        assert!(verify_owned(m.task.as_ref().unwrap(), &tx.sid, &tx.exe).unwrap());
        assert!(f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, true, true, &f.helper)
            .await
            .unwrap());
        assert!(!f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, false, true, &f.helper)
            .await
            .unwrap());
        assert!(m.task.is_none());
        assert!(!f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, true, false, &f.helper)
            .await
            .unwrap());
        assert!(!m.entry.registered());
    }

    #[tokio::test]
    async fn failures_restore_original_task_and_registry_once() {
        for failure in ["install", "query", "disabled", "registry", "registry_read"] {
            for existing in [false, true] {
                let f = Fixture::new();
                let mut m = f.memory();
                let tx = f.capture(&mut m).await.unwrap();
                if existing {
                    m.task = Some(
                        task_xml(&tx.sid, &tx.exe, &f.root)
                            .unwrap()
                            .replace("<Triggers />", "<Triggers><LogonTrigger /></Triggers>"),
                    );
                    m.entry = plan_entry(&m.entry, true, &tx.command, true);
                    m.entry.disable();
                }
                m.queries = 0;
                let tx = f.capture(&mut m).await.unwrap();
                let before = m.entry.clone();
                let task = m.task.clone();
                m.fail = Some(failure);
                assert!(
                    tx.apply(&mut m, true, true, &f.helper).await.is_err(),
                    "{failure}"
                );
                assert!(m.entry == before, "registry: {failure}");
                assert_eq!(m.task, task, "task: {failure}");
                assert_eq!(m.installs, if existing { 2 } else { 1 });
                assert_eq!(
                    m.writes,
                    if matches!(failure, "registry" | "registry_read") {
                        2
                    } else {
                        0
                    }
                );
            }
        }
    }

    #[tokio::test]
    async fn failed_delete_restores_task_without_touching_registry() {
        let f = Fixture::new();
        let mut m = f.memory();
        let tx = f.capture(&mut m).await.unwrap();
        m.task = Some(task_xml(&tx.sid, &tx.exe, &f.root).unwrap());
        let task = m.task.clone();
        m.fail = Some("delete");
        assert!(f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, false, true, &f.helper)
            .await
            .is_err());
        assert_eq!(m.task, task);
        assert_eq!(m.writes, 0);
        assert_eq!(m.installs, 1);
    }

    #[tokio::test]
    async fn foreign_or_malformed_snapshots_are_rejected_before_mutation() {
        let f = Fixture::new();
        for foreign in [
            "not XML".to_string(),
            task_xml("S-1-5-OTHER", &f.exe, &f.root).unwrap(),
            task_xml("S-1-5-21-123", Path::new(r"C:\other.exe"), &f.root).unwrap(),
            task_xml("S-1-5-21-123", &f.exe, &f.root)
                .unwrap()
                .replace(SOURCE, "other"),
        ] {
            let mut m = f.memory();
            m.task = Some(foreign);
            assert!(f.capture(&mut m).await.is_err());
            assert_eq!((m.installs, m.deletes, m.writes), (0, 0, 0));
        }
        let mut m = f.memory();
        m.entry = plan_entry(&m.entry, true, "other installation", true);
        assert!(f.capture(&mut m).await.is_err());
        assert_eq!(m.queries, 0);
    }

    #[tokio::test]
    async fn rollback_protects_replaced_task_and_reports_both_restore_failures() {
        let f = Fixture::new();
        let mut m = f.memory();
        let tx = f.capture(&mut m).await.unwrap();
        m.fail = Some("foreign");
        let error = tx.apply(&mut m, true, true, &f.helper).await.unwrap_err();
        assert!(error.contains("not owned"));
        assert_eq!(m.deletes, 0);
        assert_eq!(m.installs, 1);
        let mut m = f.memory();
        let tx = f.capture(&mut m).await.unwrap();
        m.task = Some(
            task_xml(&tx.sid, &tx.exe, &f.root)
                .unwrap()
                .replace("<Triggers />", "<Triggers><LogonTrigger /></Triggers>"),
        );
        let tx = f.capture(&mut m).await.unwrap();
        m.fail = Some("registry");
        m.rollback_fails = true;
        let error = tx.apply(&mut m, true, true, &f.helper).await.unwrap_err();
        assert!(error.contains("registry rollback failed"));
        assert!(error.contains("task rollback failed"));
        assert_eq!((m.installs, m.writes), (2, 2));
    }

    #[tokio::test]
    async fn reconciliation_rollback_preserves_external_task_manager_change() {
        let f = Fixture::new();
        let mut m = f.memory();
        let tx = f.capture(&mut m).await.unwrap();
        let legacy =
            crate::startup_entry::raw_command(&f.helper.canonicalize().unwrap(), &tx.name).unwrap();
        m.entry = plan_entry(&m.entry, true, &legacy, true);
        let tx = f.capture(&mut m).await.unwrap();
        m.fail = Some("registry_external");
        m.external_disable = true;
        assert!(tx.apply(&mut m, true, false, &f.helper).await.is_err());
        assert!(!m.entry.enabled().unwrap());
        m.entry.verify_command(&legacy, &legacy).unwrap();
    }

    #[tokio::test]
    async fn unchanged_reconcile_does_not_rewrite_registry_or_rollback_unchanged_task() {
        let f = Fixture::new();
        let mut m = f.memory();
        let tx = f.capture(&mut m).await.unwrap();
        m.task = Some(task_xml(&tx.sid, &tx.exe, &f.root).unwrap());
        m.entry = plan_entry(&m.entry, true, &tx.command, true);
        let tx = f.capture(&mut m).await.unwrap();
        assert!(tx.apply(&mut m, true, false, &f.helper).await.unwrap());
        assert_eq!(m.writes, 0);
        assert_eq!(m.queries, 3); // two captures, one persistence readback
        m.queries = 0;
        m.installs = 0;
        m.fail = Some("install_unchanged");
        let tx = f.capture(&mut m).await.unwrap();
        assert!(tx.apply(&mut m, true, false, &f.helper).await.is_err());
        assert_eq!(m.installs, 1); // rollback read found the original task intact
        assert_eq!(m.writes, 0);
    }

    #[tokio::test]
    async fn disabling_does_not_require_a_present_helper() {
        let f = Fixture::new();
        let mut m = f.memory();
        std::fs::remove_file(&f.helper).unwrap();
        let tx = f.capture(&mut m).await.unwrap();
        assert!(!tx.apply(&mut m, false, true, &f.helper).await.unwrap());
        assert_eq!(m.installs, 0);
        assert!(f
            .capture(&mut m)
            .await
            .unwrap()
            .apply(&mut m, true, true, &f.helper)
            .await
            .is_err());
    }

    #[tokio::test]
    #[ignore = "isolated task/registry integration; never runs the task"]
    async fn task_manager_override_survives_reconcile_and_legacy_task_migration() {
        let root = std::env::temp_dir().join(format!(
            "wechat-run-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let before = crate::startup_entry::Entry::read(&root).unwrap();
        struct Restore(crate::startup_entry::Entry, PathBuf);
        impl Drop for Restore {
            fn drop(&mut self) {
                self.0.restore().unwrap();
                let _ = std::fs::remove_dir(&self.1);
            }
        }
        let _restore = Restore(before, root.clone());
        let helper = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("resources/startup/wechat-onebot-startup.exe");
        if let Err(error) = configure(true, &root, &helper, true).await {
            assert!(
                error.contains("拒绝访问") || error.to_lowercase().contains("access is denied"),
                "{error}"
            );
            assert!(!crate::startup_entry::Entry::read(&root)
                .unwrap()
                .registered());
            return;
        }
        let sid = current_sid().await.unwrap();
        let name = format!(
            "WeChatOneBotLauncher-{sid}-{}",
            crate::paths::workspace_key(&root)
        );
        struct DeleteTask(String);
        impl Drop for DeleteTask {
            fn drop(&mut self) {
                let _ = std::process::Command::new(
                    PathBuf::from(std::env::var_os("SystemRoot").unwrap())
                        .join("System32/schtasks.exe"),
                )
                .args(["/Delete", "/TN", &self.0, "/F"])
                .output();
            }
        }
        let _delete = DeleteTask(name.clone());
        let content = query(&name).await.unwrap().unwrap();
        let legacy = content.replace("<Triggers />", &format!("<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{sid}</UserId></LogonTrigger></Triggers>"));
        assert!(legacy.contains("LogonTrigger"));
        install(&name, &legacy, &root).await.unwrap();
        let key = winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER)
            .create_subkey(
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
            )
            .unwrap()
            .0;
        let entry_name = crate::startup_entry::name(&root);
        let run = winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER)
            .open_subkey_with_flags(
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                winreg::enums::KEY_WRITE | winreg::enums::KEY_WOW64_64KEY,
            )
            .unwrap();
        let old_command =
            crate::startup_entry::raw_command(&helper.canonicalize().unwrap(), &name).unwrap();
        run.set_value(&entry_name, &old_command).unwrap();
        let disabled = winreg::RegValue {
            vtype: winreg::enums::REG_BINARY,
            bytes: vec![3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into(),
        };
        key.set_raw_value(&entry_name, &disabled).unwrap();
        assert!(!configure(true, &root, &helper, false).await.unwrap());
        let migrated = crate::startup_entry::Entry::read(&root).unwrap();
        let command = crate::startup_entry::command(&helper, &name).unwrap();
        migrated.verify_command(&command, &command).unwrap();
        assert!(!query(&name)
            .await
            .unwrap()
            .unwrap()
            .contains("LogonTrigger"));
        assert_eq!(
            key.get_raw_value(&entry_name).unwrap().bytes,
            disabled.bytes
        );
        assert!(!configure(false, &root, &helper, false).await.unwrap());
        key.set_raw_value(
            &entry_name,
            &winreg::RegValue {
                vtype: winreg::enums::REG_BINARY,
                bytes: vec![2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0].into(),
            },
        )
        .unwrap();
        assert!(configure(false, &root, &helper, false).await.unwrap());
        assert!(configure(true, &root, &helper, true).await.unwrap());
        assert!(!configure(false, &root, &helper, true).await.unwrap());
        assert!(query(&name).await.unwrap().is_none());
        assert!(!configure(true, &root, &helper, false).await.unwrap());
    }
}
