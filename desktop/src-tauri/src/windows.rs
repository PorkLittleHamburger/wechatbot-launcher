//! Only the supervisor we create is assigned to this job. Never open or assign
//! a user-supplied PID (in particular an already-running WeChat process).
use std::{ffi::OsStr, fs::File, io, mem, os::windows::{ffi::OsStrExt, io::{AsRawHandle, FromRawHandle, OwnedHandle}}, path::Path, ptr};
use windows_sys::Win32::{
    Foundation::{HANDLE, HANDLE_FLAG_INHERIT, SetHandleInformation, WAIT_OBJECT_0, WAIT_FAILED},
    Security::SECURITY_ATTRIBUTES,
    System::{
        JobObjects::{AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, TerminateJobObject},
        Pipes::CreatePipe,
        Threading::{CreateProcessW, InitializeProcThreadAttributeList, UpdateProcThreadAttribute, DeleteProcThreadAttributeList, ResumeThread, TerminateProcess, WaitForSingleObject, CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROCESS_INFORMATION, STARTF_USESTDHANDLES, STARTUPINFOEXW},
    },
};

pub struct Process {
    process: OwnedHandle,
    job: OwnedHandle,
}

fn wide(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(Some(0)).collect()
}

fn owned(handle: HANDLE) -> io::Result<OwnedHandle> {
    if handle.is_null() { return Err(io::Error::last_os_error()); }
    // SAFETY: callers transfer a newly-created, uniquely-owned kernel handle.
    Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
}

fn pipe() -> io::Result<(OwnedHandle, OwnedHandle)> {
    let mut read = ptr::null_mut();
    let mut write = ptr::null_mut();
    let attributes = SECURITY_ATTRIBUTES { nLength: mem::size_of::<SECURITY_ATTRIBUTES>() as u32, lpSecurityDescriptor: ptr::null_mut(), bInheritHandle: 1 };
    if unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) } == 0 { return Err(io::Error::last_os_error()); }
    Ok((owned(read)?, owned(write)?))
}

struct Attributes { storage: Vec<usize> }
impl Drop for Attributes {
    fn drop(&mut self) { unsafe { DeleteProcThreadAttributeList(self.storage.as_mut_ptr().cast()) }; }
}

fn supervisor_command_line(python: &Path, project: &Path, root: &Path, autostart: bool) -> Vec<u16> {
    let mut args = vec![
        python.as_os_str(), OsStr::new("-B"), OsStr::new("-u"), OsStr::new("-m"),
        OsStr::new("wechat_onebot.launcher.supervisor"), OsStr::new("--root"), root.as_os_str(),
        OsStr::new("--project"), project.as_os_str(),
    ];
    if autostart { args.push(OsStr::new("--autostart")); }
    super::paths::command_line(&args)
}

impl Process {
    pub fn spawn(python: &Path, project: &Path, root: &Path, autostart: bool) -> io::Result<(Self, File, File)> {
        let python = super::paths::process_path(python);
        let project = super::paths::process_path(project);
        let root = super::paths::process_path(root);
        let job = owned(unsafe { CreateJobObjectW(ptr::null(), ptr::null()) })?;
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe { SetInformationJobObject(job.as_raw_handle(), JobObjectExtendedLimitInformation, (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(), mem::size_of_val(&limits) as u32) } == 0 { return Err(io::Error::last_os_error()); }
        let (stdin_read, stdin_write) = pipe()?;
        let (stdout_read, stdout_write) = pipe()?;
        let logs = root.join("logs");
        std::fs::create_dir_all(&logs)?;
        let log_path = logs.join("supervisor.stderr.log");
        if std::fs::metadata(&log_path).is_ok_and(|m| m.len() > 10 * 1024 * 1024) {
            let previous = logs.join("supervisor.stderr.previous.log");
            if previous.exists() { std::fs::remove_file(&previous)?; }
            std::fs::rename(&log_path, previous)?;
        }
        let stderr_write = File::options().create(true).append(true).open(log_path)?;
        if unsafe { SetHandleInformation(stderr_write.as_raw_handle(), HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) } == 0 { return Err(io::Error::last_os_error()); }
        for handle in [&stdin_write, &stdout_read] {
            if unsafe { SetHandleInformation(handle.as_raw_handle(), HANDLE_FLAG_INHERIT, 0) } == 0 { return Err(io::Error::last_os_error()); }
        }
        // Restrict inheritance to exactly the three child pipe ends.
        let mut bytes = 0;
        unsafe { InitializeProcThreadAttributeList(ptr::null_mut(), 1, 0, &mut bytes); }
        let mut storage = vec![0usize; bytes.div_ceil(mem::size_of::<usize>())];
        if unsafe { InitializeProcThreadAttributeList(storage.as_mut_ptr().cast(), 1, 0, &mut bytes) } == 0 { return Err(io::Error::last_os_error()); }
        let mut attributes = Attributes { storage };
        let handles = [stdin_read.as_raw_handle(), stdout_write.as_raw_handle(), stderr_write.as_raw_handle()];
        if unsafe { UpdateProcThreadAttribute(attributes.storage.as_mut_ptr().cast(), 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize, handles.as_ptr().cast(), mem::size_of_val(&handles), ptr::null_mut(), ptr::null()) } == 0 { return Err(io::Error::last_os_error()); }
        let mut startup: STARTUPINFOEXW = unsafe { mem::zeroed() };
        startup.StartupInfo.cb = mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = stdin_read.as_raw_handle();
        startup.StartupInfo.hStdOutput = stdout_write.as_raw_handle();
        startup.StartupInfo.hStdError = stderr_write.as_raw_handle();
        startup.lpAttributeList = attributes.storage.as_mut_ptr().cast();
        let exe = wide(python.as_os_str());
        let cwd = wide(project.as_os_str());
        let mut command = supervisor_command_line(&python, &project, &root, autostart);
        let mut environment: Vec<_> = std::env::vars_os().filter(|(key, _)| {
            !["PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING", "PYTHONUTF8"].iter().any(|name| key.to_string_lossy().eq_ignore_ascii_case(name))
        }).collect();
        environment.extend([("PYTHONDONTWRITEBYTECODE".into(), "1".into()), ("PYTHONIOENCODING".into(), "utf-8".into()), ("PYTHONUTF8".into(), "1".into())]);
        environment.sort_by_key(|(key, _)| key.to_string_lossy().to_uppercase());
        let mut env_block = Vec::new();
        for (key, value) in environment { env_block.extend(key.encode_wide()); env_block.push(61); env_block.extend(value.encode_wide()); env_block.push(0); }
        env_block.push(0);
        let mut info: PROCESS_INFORMATION = unsafe { mem::zeroed() };
        if unsafe { CreateProcessW(exe.as_ptr(), command.as_mut_ptr(), ptr::null(), ptr::null(), 1, CREATE_NO_WINDOW | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT, env_block.as_ptr().cast(), cwd.as_ptr(), &startup.StartupInfo, &mut info) } == 0 { return Err(io::Error::last_os_error()); }
        let process = owned(info.hProcess)?;
        let thread = owned(info.hThread)?;
        if unsafe { AssignProcessToJobObject(job.as_raw_handle(), process.as_raw_handle()) } == 0 {
            let error = io::Error::last_os_error();
            unsafe { TerminateProcess(process.as_raw_handle(), 1); }
            return Err(error);
        }
        let owner = Self { process, job };
        if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX { return Err(io::Error::last_os_error()); }
        // The job is already active before any Python/user code can execute.
        Ok((owner, File::from(stdin_write), File::from(stdout_read)))
    }

    pub fn exited(&self) -> io::Result<bool> {
        match unsafe { WaitForSingleObject(self.process.as_raw_handle(), 0) } {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_FAILED => Err(io::Error::last_os_error()),
            _ => Ok(false),
        }
    }

    pub fn kill_tree(&self) -> io::Result<()> {
        if unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } == 0 { return Err(io::Error::last_os_error()); }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_supervisor_command(autostart: bool, expected: &str) {
        let command = supervisor_command_line(
            Path::new(r"C:\Launcher 中文\python\python.exe"),
            Path::new(r"C:\Launcher 中文"),
            Path::new(r"D:\Workspace 中文"),
            autostart,
        );
        assert_eq!(command.last(), Some(&0));
        assert_eq!(String::from_utf16(&command[..command.len() - 1]).unwrap(), expected);
    }

    #[test]
    fn manual_supervisor_command_omits_autostart() {
        assert_supervisor_command(false, r#""C:\Launcher 中文\python\python.exe" "-B" "-u" "-m" "wechat_onebot.launcher.supervisor" "--root" "D:\Workspace 中文" "--project" "C:\Launcher 中文""#);
    }

    #[test]
    fn autostart_supervisor_command_forwards_flag() {
        assert_supervisor_command(true, r#""C:\Launcher 中文\python\python.exe" "-B" "-u" "-m" "wechat_onebot.launcher.supervisor" "--root" "D:\Workspace 中文" "--project" "C:\Launcher 中文" "--autostart""#);
    }
}
