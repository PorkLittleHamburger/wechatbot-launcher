//! Opt-in real WebView2 smoke; no supervisor, account, injection, or startup task.
#[path = "../src/instance_window.rs"]
mod instance_window;
use tauri::Manager;
use std::io::{Read, Write};

fn server() -> tauri::Url {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/", listener.local_addr().unwrap()).parse().unwrap();
    std::thread::spawn(move || {
        for mut stream in listener.incoming().flatten() {
            let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(1)));
            let _ = stream.read(&mut [0u8; 4096]);
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\n<h1>test</h1>");
        }
    });
    url
}

fn main() {
    let report = std::env::args().nth(1).expect("Pass an absolute report file path");
    let url = server();
    let changed_url = server();
    let mut context = tauri::generate_context!();
    context.config_mut().identifier = format!("com.wechat.launcher.smoke.p{}", std::process::id());
    context.config_mut().app.windows[0].url = tauri::WebviewUrl::External(url.clone());
    tauri::Builder::default().setup(move |app| {
        let handle = app.handle().clone();
        std::thread::spawn(move || {
            let result = (|| -> Result<(), String> {
                for id in ["offline-a", "offline-a", "offline-b"] {
                    instance_window::open(&handle, id, url.clone())?;
                }
                if handle.webview_windows().len() != 3 { return Err("Duplicate window".into()); }
                instance_window::open(&handle, "offline-b", changed_url)?;
                handle.get_webview_window("instance-offline-a").unwrap().destroy().map_err(|e| e.to_string())?;
                // Destruction is asynchronous; wait until the runtime removes the label.
                let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
                while handle.get_webview_window("instance-offline-a").is_some() {
                    if std::time::Instant::now() > until { return Err("Destroy timeout".into()); }
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                instance_window::open(&handle, "offline-a", url)?;
                Ok(())
            })();
            std::fs::write(report, format!("{result:?}")).unwrap();
            handle.exit(if result.is_ok() { 0 } else { 1 });
        });
        Ok(())
    }).run(context).expect("WebView smoke runtime");
}
