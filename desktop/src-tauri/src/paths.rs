use std::{ffi::OsString, path::PathBuf};

pub struct Options { pub root: PathBuf, pub autostart: bool }

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
        let root = match root {
            Some(root) => root,
            None => PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not set")?).join("WeChatOneBotLauncher"),
        };
        std::fs::create_dir_all(&root).map_err(|e| format!("Create workspace: {e}"))?;
        let root = root.canonicalize().map_err(|e| format!("Resolve workspace: {e}"))?;
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
}
