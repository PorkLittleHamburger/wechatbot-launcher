#![windows_subsystem = "windows"]

#[cfg(not(windows))]
compile_error!("WeChat OneBot Launcher desktop currently targets Windows only");

mod ipc;
mod paths;
mod scheduler;
mod startup_entry;
mod windows;
mod updater;

use serde_json::{json, Value};
use std::{path::PathBuf, sync::{Arc, atomic::{AtomicBool, Ordering}}};
use tauri::{Manager, Emitter, WebviewWindow, WebviewUrl};
use tauri_plugin_opener::OpenerExt;
use tokio::sync::Mutex;
use tokio::sync::RwLock;

struct Shell {
    ipc: Arc<ipc::Ipc>,
    root: PathBuf,
    startup_helper: PathBuf,
    settings: Mutex<()>,
    autostart_error: Mutex<Option<String>>,
    update_gate: RwLock<()>,
    exiting: AtomicBool,
    exit_complete: AtomicBool,
}

fn require_main(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" { return Err("Native commands are restricted to the launcher main window".into()); }
    let url = window.url().map_err(|e| e.to_string())?;
    // Label alone is insufficient if a local window navigates to remote content.
    let local = matches!(url.scheme(), "tauri")
        || matches!((url.scheme(), url.host_str(), url.port()), ("https" | "http", Some("tauri.localhost"), None));
    let dev = cfg!(debug_assertions) && url.scheme() == "http" && url.host_str() == Some("localhost") && url.port() == Some(1420);
    if !local && !dev { return Err("Native commands require the bundled launcher origin".into()); }
    Ok(())
}

fn setting(root: &std::path::Path, name: &str, fallback: bool) -> bool {
    std::fs::read(root.join("settings.json")).ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|value| value.get(name).and_then(Value::as_bool)).unwrap_or(fallback)
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[tauri::command]
async fn rpc(window: WebviewWindow, state: tauri::State<'_, Shell>, method: String, mut params: Value) -> Result<Value, String> {
    require_main(&window)?;
    let _update_gate = state.update_gate.read().await;
    if state.exiting.load(Ordering::Acquire) { return Err("Launcher is shutting down".into()); }
    if method == "shutdown" { return Err("Use exit_app for desktop shutdown".into()); }
    if !params.is_object() { return Err("RPC params must be an object".into()); }
    if method == "snapshot" {
        let _transaction = state.settings.lock().await;
        let mut snapshot = state.ipc.request(&method, params).await?;
        snapshot["autostart_error"] = json!(*state.autostart_error.lock().await);
        match startup_entry::Entry::read(&state.root).and_then(|entry| {
            let enabled = if entry.known() { Some(entry.enabled()?) } else { None };
            Ok((entry.name.clone(), entry.registered(), enabled))
        }) {
            Ok((name, registered, enabled)) => {
                snapshot["autostart_status"] = json!({"entry_name": name, "registered": registered, "enabled": enabled});
                if let Some(enabled) = enabled { snapshot["settings"]["autostart"] = json!(enabled); }
            }
            Err(error) => { snapshot["autostart_error"] = json!(error); }
        }
        return Ok(snapshot);
    }
    if method != "settings_set" { return state.ipc.request(&method, params).await; }
    let explicit = match params.as_object_mut().unwrap().remove("autostart_explicit") {
        None => false,
        Some(Value::Bool(value)) => value,
        _ => return Err("Invalid autostart intent".into()),
    };
    let _transaction = state.settings.lock().await;
    let before = state.ipc.request("settings_get", json!({})).await?;
    let mut after = state.ipc.request("settings_set", params).await?;
    {
        let enabled = after.get("autostart").and_then(Value::as_bool).ok_or("Invalid autostart setting from supervisor")?;
        let result = scheduler::configure(enabled, &state.root, &state.startup_helper, explicit).await;
        if let Err(error) = &result {
            *state.autostart_error.lock().await = Some(error.clone());
            let _ = std::fs::write(state.root.join("autostart.log"), &error);
            let rollback = state.ipc.request("settings_set", before).await;
            return Err(match rollback {
                Ok(_) => format!("Autostart task update failed; Python settings restored: {error}"),
                Err(rollback) => format!("Autostart task update failed: {error}; settings rollback also failed: {rollback}"),
            });
        }
        let actual = result?;
        if actual != enabled {
            after["autostart"] = json!(actual);
            state.ipc.request("settings_set", after.clone()).await?;
        }
        *state.autostart_error.lock().await = None;
    }
    Ok(after)
}

#[tauri::command(rename_all = "camelCase")]
async fn open_instance(window: WebviewWindow, app: tauri::AppHandle, state: tauri::State<'_, Shell>, instance_id: String) -> Result<(), String> {
    require_main(&window)?;
    if state.exiting.load(Ordering::Acquire) { return Err("Launcher is shutting down".into()); }
    if instance_id.len() != 32 || !instance_id.bytes().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()) { return Err("Invalid instance ID".into()); }
    let row = state.ipc.request("instance_info", json!({"id": instance_id})).await?;
    if row.get("id").and_then(Value::as_str) != Some(instance_id.as_str()) || row.get("state").and_then(Value::as_str) != Some("running") { return Err("Instance is not running".into()); }
    let url: tauri::Url = row.get("url").and_then(Value::as_str).ok_or("Instance has no URL")?.parse().map_err(|e| format!("Invalid instance URL: {e}"))?;
    if url.scheme() != "http" || url.host_str() != Some("127.0.0.1") || url.port().is_none() || !url.username().is_empty() || url.password().is_some() { return Err("Instance URL must use HTTP on 127.0.0.1 with an explicit port".into()); }
    let label = format!("instance-{instance_id}");
    if let Some(existing) = app.get_webview_window(&label) {
        existing.navigate(url).map_err(|e| e.to_string())?;
        existing.show().map_err(|e| e.to_string())?;
        existing.set_focus().map_err(|e| e.to_string())?;
        return Ok(());
    }
    // No capability matches this label, and no remote origins receive grants.
    tauri::WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url.clone()))
        .title("WeChat OneBot — 实例管理")
        .inner_size(1180.0, 800.0)
        .on_navigation(move |next| next.origin() == url.origin())
        .build().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn reveal_workspace(window: WebviewWindow, app: tauri::AppHandle, state: tauri::State<'_, Shell>) -> Result<(), String> {
    require_main(&window)?;
    app.opener().open_path(state.root.to_string_lossy().as_ref(), None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
async fn change_workspace(window: WebviewWindow, app: tauri::AppHandle, state: tauri::State<'_, Shell>, path: String) -> Result<(), String> {
    require_main(&window)?;
    let _update_gate = state.update_gate.read().await;
    if state.exiting.load(Ordering::Acquire) { return Err("Launcher is shutting down".into()); }
    let requested = PathBuf::from(path);
    if !requested.is_absolute() { return Err("Workspace path must be absolute".into()); }
    let root = paths::prepare_workspace(requested)?;
    if root == state.root { return Ok(()); }
    let snapshot = state.ipc.request("snapshot", json!({})).await?;
    let active = snapshot.get("instances").and_then(Value::as_array).map(|rows| rows.iter().any(|row| {
        matches!(row.get("state").and_then(Value::as_str), Some("running" | "starting" | "stopping" | "restarting"))
    })).unwrap_or(false);
    if active { return Err("请先停止所有实例，再更改数据目录".into()); }
    scheduler::configure(false, &state.root, &state.startup_helper, true).await.map_err(|e| format!("Remove old autostart task: {e}"))?;
    paths::save_workspace(&root)?;
    if state.exiting.swap(true, Ordering::AcqRel) { return Err("Launcher is shutting down".into()); }
    let ipc = state.ipc.clone();
    tauri::async_runtime::spawn(async move {
        ipc.shutdown().await;
        app.state::<Shell>().exit_complete.store(true, Ordering::Release);
        app.restart();
    });
    Ok(())
}

fn begin_exit(app: tauri::AppHandle) {
    let Some(state) = app.try_state::<Shell>() else { app.exit(1); return; };
    if state.exiting.swap(true, Ordering::AcqRel) { return; }
    let ipc = state.ipc.clone();
    tauri::async_runtime::spawn(async move {
        ipc.shutdown().await;
        app.state::<Shell>().exit_complete.store(true, Ordering::Release);
        app.exit(0);
    });
}

#[tauri::command]
async fn install_launcher_update(window: WebviewWindow, app: tauri::AppHandle, state: tauri::State<'_, Shell>) -> Result<(), String> {
    require_main(&window)?;
    let _update_gate = state.update_gate.write().await;
    let _transaction = state.settings.lock().await;
    if state.exiting.load(Ordering::Acquire) { return Err("Launcher is shutting down".into()); }
    let snapshot = state.ipc.request("snapshot", json!({})).await?;
    let rows = snapshot.get("instances").and_then(Value::as_array).ok_or("Invalid instance snapshot")?;
    if rows.iter().any(|row| !matches!(row.get("state").and_then(Value::as_str), Some("stopped" | "created" | "error" | "failed"))) {
        return Err("请先停止所有实例，再安装启动器更新".into());
    }
    let ready = state.ipc.request("launcher_update_prepare_install", json!({})).await?;
    let path = PathBuf::from(ready.get("path").and_then(Value::as_str).ok_or("Installer path missing")?);
    let checksum = ready.get("sha256").and_then(Value::as_str).ok_or("Installer checksum missing")?;
    let directory = state.root.join("launcher-updates").canonicalize().map_err(|e| e.to_string())?;
    if !path.canonicalize().map_err(|e| e.to_string())?.starts_with(&directory) { return Err("Installer is outside update cache".into()); }
    updater::handoff(&path, checksum, &state.root)?;
    begin_exit(app);
    Ok(())
}

#[tauri::command]
async fn exit_app(window: WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    require_main(&window)?;
    begin_exit(app);
    Ok(())
}

fn main() {
    let options = paths::Options::parse(std::env::args_os().skip(1)).expect("Invalid launcher arguments/workspace");
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_window_state::Builder::default()
            .with_state_flags(tauri_plugin_window_state::StateFlags::all() & !tauri_plugin_window_state::StateFlags::VISIBLE)
            .with_filter(|label| label == "main").build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![rpc, open_instance, reveal_workspace, change_workspace, exit_app, install_launcher_update])
        .setup(move |app| {
            let project = app.path().resource_dir()?.join("runtime");
            let ipc = ipc::Ipc::spawn(app.handle().clone(), &project, &options.root).map_err(std::io::Error::other)?;
            let hidden = options.autostart && setting(&options.root, "autostart_hidden", false);
            app.manage(Shell { ipc: ipc.clone(), root: options.root, startup_helper: app.path().resource_dir()?.join("startup/wechat-onebot-startup.exe"), settings: Mutex::new(()), autostart_error: Mutex::new(None), update_gate: RwLock::new(()), exiting: AtomicBool::new(false), exit_complete: AtomicBool::new(false) });
            let show = tauri::menu::MenuItem::with_id(app, "show", "显示启动器", true, None::<&str>)?;
            let exit = tauri::menu::MenuItem::with_id(app, "exit", "退出", true, None::<&str>)?;
            let menu = tauri::menu::Menu::with_items(app, &[&show, &exit])?;
            let mut tray = tauri::tray::TrayIconBuilder::new().tooltip("WeChat OneBot Launcher").menu(&menu).show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() { "show" => show_main(app), "exit" => begin_exit(app.clone()), _ => {} })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::Click { button: tauri::tray::MouseButton::Left, button_state: tauri::tray::MouseButtonState::Up, .. } = event { show_main(tray.app_handle()); }
                });
            if let Some(icon) = app.default_window_icon() { tray = tray.icon(icon.clone()); }
            tray.build(app)?;
            if !hidden { show_main(app.handle()); }
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(error) = ipc.ready().await {
                    let _ = handle.emit_to("main", "fatal", json!({"message": error}));
                    show_main(&handle);
                    ipc.shutdown().await;
                    return;
                }
                // Repair tasks lost during upgrades even when the saved checkbox
                // has not changed. Serialize against saves and workspace changes.
                let state = handle.state::<Shell>();
                let _transaction = state.settings.lock().await;
                if state.exiting.load(Ordering::Acquire) { return; }
                let result = match ipc.request("settings_get", json!({})).await {
                    Ok(mut settings) => match settings.get("autostart").and_then(Value::as_bool) {
                        Some(enabled) => match scheduler::configure(enabled, &state.root, &state.startup_helper, false).await {
                            Ok(actual) => {
                                settings["autostart"] = json!(actual);
                                ipc.request("settings_set", settings).await.map(|_| ())
                            }
                            Err(error) => Err(error),
                        },
                        None => Err("Invalid autostart setting from supervisor".into()),
                    },
                    Err(error) => Err(error),
                };
                if let Err(error) = result {
                    let _ = std::fs::write(state.root.join("autostart.log"), &error);
                    *state.autostart_error.lock().await = Some(error);
                    show_main(&handle);
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != "main" { return; }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle();
                if let Some(state) = app.try_state::<Shell>() {
                    if state.exiting.load(Ordering::Acquire) { return; }
                    if setting(&state.root, "close_to_tray", true) { let _ = window.hide(); }
                    else { begin_exit(app.clone()); }
                }
            }
        })
        .build(tauri::generate_context!()).expect("Failed to build WeChat OneBot Launcher");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            if let Some(state) = app.try_state::<Shell>() {
                if !state.exit_complete.load(Ordering::Acquire) { api.prevent_exit(); begin_exit(app.clone()); }
            }
        }
    });
}
