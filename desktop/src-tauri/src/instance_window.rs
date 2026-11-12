//! Instance WebViews have their own browser profile and no native capability.
use tauri::{Manager, WebviewUrl};

pub fn open(app: &tauri::AppHandle, instance_id: &str, url: tauri::Url) -> Result<(), String> {
    let label = format!("instance-{instance_id}");
    if let Some(existing) = app.get_webview_window(&label) {
        let current = existing
            .url()
            .map_err(|e| format!("读取实例窗口地址失败：{e}"))?;
        if current.origin() != url.origin() {
            // The navigation guard belongs to the original server origin. Rebuild
            // it when an instance port changes, keeping the same browser profile.
            existing
                .destroy()
                .map_err(|e| format!("关闭旧地址实例窗口失败：{e}"))?;
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while app.get_webview_window(&label).is_some() {
                if std::time::Instant::now() >= deadline {
                    return Err("旧实例窗口尚未关闭，请稍后重试".into());
                }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        } else {
            // Reopening a window must not reload an in-progress login/configuration.
            if current != url {
                existing
                    .navigate(url)
                    .map_err(|e| format!("实例窗口导航失败：{e}"))?;
            }
            existing
                .unminimize()
                .map_err(|e| format!("还原实例窗口失败：{e}"))?;
            existing
                .show()
                .map_err(|e| format!("显示实例窗口失败：{e}"))?;
            return existing
                .set_focus()
                .map_err(|e| format!("聚焦实例窗口失败：{e}"));
        }
    }
    let profile = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("instance-webviews")
        .join(instance_id);
    std::fs::create_dir_all(&profile).map_err(|e| format!("创建实例浏览器数据目录失败：{e}"))?;
    tauri::WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url.clone()))
        .title("WeChat OneBot — 实例管理")
        .inner_size(1180.0, 800.0)
        .data_directory(profile)
        .on_navigation(move |next| next.origin() == url.origin())
        .build()
        .map_err(|e| format!("创建实例 WebUI 窗口失败（WebView2）：{e}"))?;
    Ok(())
}
