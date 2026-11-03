use std::{ffi::OsString, path::{Path, PathBuf}};

pub struct Options { pub root: PathBuf, pub autostart: bool }

fn preference_path() -> Result<PathBuf, String> {
    let local = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not set")?;
    let directory = PathBuf::from(local).join("WeChatOneBotLauncherShell");
    std::fs::create_dir_all(&directory).map_err(|e| format!("Create launcher settings directory: {e}"))?;
    Ok(directory.join("workspace.txt"))
}

fn read_workspace(path: &Path) -> Option<PathBuf> {
    let text = std::fs::read_to_string(path).ok()?;
    let value = text.trim();
    (!value.is_empty()).then(|| PathBuf::from(value))
}

fn write_workspace(path: &Path, root: &Path) -> Result<(), String> {
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, root.to_string_lossy().as_bytes())
        .map_err(|e| format!("Write workspace preference: {e}"))?;
    std::fs::rename(&temporary, path).map_err(|e| format!("Replace workspace preference: {e}"))
}

pub fn saved_workspace() -> Option<PathBuf> {
    read_workspace(&preference_path().ok()?)
}

pub fn save_workspace(root: &Path) -> Result<(), String> {
    write_workspace(&preference_path()?, root)
}

pub fn prepare_workspace(root: PathBuf) -> Result<PathBuf, String> {
    std::fs::create_dir_all(&root).map_err(|e| format!("Create workspace: {e}"))?;
    root.canonicalize().map(|path| process_path(&path)).map_err(|e| format!("Resolve workspace: {e}"))
}

// Keep filesystem identity checks canonical, but don't leak unnecessary verbatim
// DOS paths into Python or third-party validators. Preserve long/device/UNC paths.
pub fn process_path(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        use std::{os::windows::ffi::{OsStrExt, OsStringExt}, path::{Component, Prefix}};
        if matches!(path.components().next(), Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::VerbatimDisk(_))) {
            let wide: Vec<_> = path.as_os_str().encode_wide().collect();
            if wide.len() - 4 < 260 {
                let plain = PathBuf::from(OsString::from_wide(&wide[4..]));
                if let (Ok(actual), Ok(candidate)) = (path.canonicalize(), plain.canonicalize()) {
                    if actual == candidate { return plain; }
                }
            }
        }
    }
    path.to_path_buf()
}

impl Options {
    pub fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Self, String> {
        let mut args = args.into_iter();
        let mut root = None;
        let mut autostart = false;
        while let Some(arg) = args.next() {
            if arg == "--workspace" {
                if root.is_some() { return Err("--workspace was specified twice".into()); }
                root = Some(PathBuf::from(args.next().ok_or("--workspace requires a path")?));
            } else if arg == "--autostart" {
                autostart = true;
            } else { return Err(format!("Unknown argument: {}", arg.to_string_lossy())); }
        }
        let default_root = || -> Result<PathBuf, String> {
            let local = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not set")?;
            Ok(PathBuf::from(local).join("WeChatOneBotLauncher"))
        };
        let root = match root {
            Some(root) => root,
            None => match saved_workspace() {
                Some(root) => root,
                None => default_root()?,
            },
        };
        let root = prepare_workspace(root)?;
        Ok(Self { root, autostart })
    }
}

// CommandLineToArgvW / Microsoft CRT quoting, including trailing backslashes.
#[cfg(windows)]
pub fn command_line(args: &[&std::ffi::OsStr]) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    let mut output = Vec::new();
    for (index, arg) in args.iter().enumerate() {
        if index != 0 { output.push(32); }
        output.push(34);
        let mut slashes = 0;
        for unit in arg.encode_wide() {
            if unit == 92 { slashes += 1; continue; }
            output.extend(std::iter::repeat_n(92, slashes * if unit == 34 { 2 } else { 1 }));
            if unit == 34 { output.push(92); }
            output.push(unit);
            slashes = 0;
        }
        output.extend(std::iter::repeat_n(92, slashes * 2));
        output.push(34);
    }
    output.push(0);
    output
}

pub fn workspace_key(root: &std::path::Path) -> String {
    // Stable across compiler/Rust versions. Used only for task-name isolation.
    let normalized = root.to_string_lossy().replace("\\\\?\\", "").to_lowercase();
    let hash = normalized.as_bytes().iter().fold(0xcbf29ce484222325u64, |hash, byte| (hash ^ *byte as u64).wrapping_mul(0x100000001b3));
    format!("{hash:016x}")
}

#[cfg(test)]
mod tests {
    #[cfg(windows)]
    #[test]
    fn process_paths_normalize_only_equivalent_short_local_paths() {
        use std::path::{Component, Prefix};
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let root = std::env::temp_dir().join(format!("wechat process 中文 {nonce}"));
        std::fs::create_dir(&root).unwrap();
        let verbatim = root.canonicalize().unwrap();
        let plain = super::process_path(&verbatim);
        assert!(!matches!(plain.components().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::VerbatimDisk(_))));
        assert_eq!(plain.canonicalize().unwrap(), verbatim);
        assert_eq!(super::prepare_workspace(verbatim.clone()).unwrap(), plain);
        let dotted = verbatim.join("name.");
        std::fs::create_dir(verbatim.join("name")).unwrap();
        std::fs::create_dir(&dotted).unwrap();
        assert_eq!(super::process_path(&dotted), dotted);
        std::fs::remove_dir(dotted).unwrap();
        std::fs::remove_dir(verbatim.join("name")).unwrap();
        std::fs::remove_dir(root).unwrap();
        for value in [r"\\?\UNC\server\share\data", r"\\.\C:\data"] {
            assert_eq!(super::process_path(std::path::Path::new(value)), std::path::PathBuf::from(value));
        }
        let long = std::path::PathBuf::from(format!(r"\\?\C:\{}", "a".repeat(260)));
        assert_eq!(super::process_path(&long), long);
    }

    #[cfg(windows)]
    #[test]
    fn quotes_spaces_quotes_and_trailing_backslashes() {
        use std::ffi::OsStr;
        let value = super::command_line(&[OsStr::new("a b\\"), OsStr::new("c\"d")]);
        assert_eq!(String::from_utf16(&value[..value.len()-1]).unwrap(), "\"a b\\\\\" \"c\\\"d\"");
    }

    #[test]
    fn task_key_is_stable_and_case_insensitive() {
        use std::path::Path;
        assert_eq!(super::workspace_key(Path::new("C:\\Data")), super::workspace_key(Path::new("c:\\data")));
        assert_ne!(super::workspace_key(Path::new("C:\\Data")), super::workspace_key(Path::new("C:\\QA")));
    }

    #[test]
    fn workspace_preference_round_trip() {
        use std::path::Path;
        let directory = std::env::temp_dir().join(format!("wechat-launcher-preference-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let file = directory.join("workspace.txt");
        let root = Path::new("D:\\WeChatData");
        super::write_workspace(&file, root).unwrap();
        assert_eq!(super::read_workspace(&file).as_deref(), Some(root));
        std::fs::remove_file(file).unwrap();
        std::fs::remove_dir(directory).unwrap();
    }
}
