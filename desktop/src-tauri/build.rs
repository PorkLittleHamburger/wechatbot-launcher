fn main() {
    println!("cargo:rerun-if-changed=windows/app.manifest");
    println!("cargo:rerun-if-changed=windows/debug.manifest");
    let manifest = if std::env::var("PROFILE").as_deref() == Ok("release") {
        include_str!("windows/app.manifest")
    } else {
        include_str!("windows/debug.manifest")
    };
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(&[
            "rpc", "open_instance", "reveal_workspace", "exit_app",
        ]))
        .windows_attributes(tauri_build::WindowsAttributes::new().app_manifest(manifest));
    tauri_build::try_build(attributes).expect("Tauri build configuration failed");
}
