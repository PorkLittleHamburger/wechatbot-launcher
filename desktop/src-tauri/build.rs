fn main() {
    println!("cargo:rerun-if-changed=src/startup_helper.rs");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap()).join("wechat-onebot-startup.exe");
    let status = std::process::Command::new(std::env::var_os("RUSTC").unwrap())
        .args(["--edition=2021", "--crate-name", "wechat_onebot_startup", "--target", &std::env::var("TARGET").unwrap(), "-O", "src/startup_helper.rs", "-o"])
        .arg(&output)
        .args(["-C", "link-arg=/MANIFEST:EMBED", "-C", "link-arg=/MANIFESTUAC:level='asInvoker' uiAccess='false'"])
        .status().expect("Compile startup helper");
    assert!(status.success(), "Startup helper compilation failed");
    let resource = std::path::Path::new("resources/startup/wechat-onebot-startup.exe");
    std::fs::create_dir_all(resource.parent().unwrap()).unwrap();
    let bytes = std::fs::read(output).unwrap();
    if std::fs::read(resource).ok().as_deref() != Some(bytes.as_slice()) { std::fs::write(resource, bytes).unwrap(); }
    println!("cargo:rerun-if-changed=windows/app.manifest");
    println!("cargo:rerun-if-changed=windows/debug.manifest");
    let manifest = if std::env::var("PROFILE").as_deref() == Ok("release") {
        include_str!("windows/app.manifest")
    } else {
        include_str!("windows/debug.manifest")
    };
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(&[
            "rpc", "open_instance", "reveal_workspace", "change_workspace", "exit_app", "install_launcher_update",
        ]))
        .windows_attributes(tauri_build::WindowsAttributes::new().app_manifest(manifest));
    tauri_build::try_build(attributes).expect("Tauri build configuration failed");
}
