use serde_json::{json, Value};
use std::{collections::HashMap, io::{BufRead, BufReader, Read}, path::Path, sync::{Arc, Mutex, atomic::{AtomicBool, AtomicU64, Ordering}}, time::Duration};
use tauri::Emitter;
use tokio::{io::AsyncWriteExt, sync::{oneshot, watch, Mutex as AsyncMutex}, time::{timeout, sleep}};

type Reply = Result<Value, String>;
type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Reply>>>>;
const READY_TIMEOUT: Duration = Duration::from_secs(180);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(900);
const MAX_FRAME: usize = 1024 * 1024;

#[derive(Clone)]
enum Status { Starting, Ready(Value), Failed(String) }

pub struct Ipc {
    writer: AsyncMutex<Option<tokio::fs::File>>,
    pending: Pending,
    next_id: AtomicU64,
    status: watch::Sender<Status>,
    closing: AtomicBool,
    shutdown_lock: AsyncMutex<()>,
    process: Mutex<Option<crate::windows::Process>>,
}

struct Registration { pending: Pending, id: u64 }
impl Drop for Registration {
    fn drop(&mut self) { if let Ok(mut pending) = self.pending.lock() { pending.remove(&self.id); } }
}

impl Ipc {
    pub fn spawn(app: tauri::AppHandle, project: &Path, root: &Path, autostart: bool) -> Result<Arc<Self>, String> {
        let python = project.join("python/python.exe");
        if !python.is_file() { return Err(format!("Bundled Python missing: {}", python.display())); }
        let (process, stdin, stdout) = crate::windows::Process::spawn(&python, project, root, autostart).map_err(|e| format!("Start supervisor: {e}"))?;
        let (status, _) = watch::channel(Status::Starting);
        let ipc = Arc::new(Self {
            writer: AsyncMutex::new(Some(tokio::fs::File::from_std(stdin))),
            pending: Arc::new(Mutex::new(HashMap::new())), next_id: AtomicU64::new(1),
            status, closing: AtomicBool::new(false), shutdown_lock: AsyncMutex::new(()), process: Mutex::new(Some(process)),
        });
        let weak = Arc::downgrade(&ipc);
        std::thread::Builder::new().name("supervisor-stdout".into()).spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut frame = Vec::new();
                let read = reader.by_ref().take((MAX_FRAME + 1) as u64).read_until(b'\n', &mut frame);
                let Some(ipc) = weak.upgrade() else { break };
                match read {
                    Ok(0) => { ipc.fail("Supervisor stdout closed".into()); break; }
                    Ok(_) if frame.len() > MAX_FRAME || !frame.ends_with(b"\n") => {
                        ipc.fail("Supervisor sent an oversized or incomplete JSON frame".into());
                        let _ = app.emit_to("main", "fatal", json!({"message": "Invalid supervisor frame"}));
                        break;
                    }
                    Err(error) => { ipc.fail(format!("Read supervisor: {error}")); break; }
                    _ => {}
                }
                let value: Value = match serde_json::from_slice(&frame) {
                    Ok(value) => value,
                    Err(error) => { ipc.fail(format!("Invalid supervisor JSON: {error}")); break; }
                };
                if let Some(event) = value.get("event").and_then(Value::as_str) {
                    let data = value.get("data").cloned().unwrap_or(Value::Null);
                    match event {
                        "ready" => { ipc.status.send_replace(Status::Ready(data.clone())); }
                        "fatal" => { ipc.fail(data.get("message").and_then(Value::as_str).unwrap_or("Supervisor failed").into()); }
                        "operation-progress" => {}
                        _ => continue,
                    }
                    let _ = app.emit_to("main", event, data);
                } else if let Some(id) = value.get("id").and_then(Value::as_u64) {
                    let sender = ipc.pending.lock().ok().and_then(|mut p| p.remove(&id));
                    if let Some(sender) = sender {
                        let result = if let Some(error) = value.get("error") {
                            Err(error.as_str().map(str::to_owned).unwrap_or_else(|| error.to_string()))
                        } else if let Some(result) = value.get("result") { Ok(result.clone()) }
                        else { Err("Malformed supervisor response".into()) };
                        let _ = sender.send(result);
                    }
                }
            }
        }).map_err(|e| format!("Start stdout reader: {e}"))?;
        Ok(ipc)
    }

    fn fail(&self, message: String) {
        self.status.send_replace(Status::Failed(message.clone()));
        if let Ok(mut pending) = self.pending.lock() {
            for (_, sender) in pending.drain() { let _ = sender.send(Err(message.clone())); }
        }
    }

    pub async fn ready(&self) -> Reply {
        let mut status = self.status.subscribe();
        timeout(READY_TIMEOUT, async {
            loop {
                let current = status.borrow_and_update().clone();
                match current {
                    Status::Ready(data) => return Ok(data),
                    Status::Failed(error) => return Err(error),
                    Status::Starting => {}
                }
                status.changed().await.map_err(|_| "Supervisor readiness channel closed".to_string())?;
            }
        }).await.map_err(|_| "Supervisor did not become ready within 180 seconds; see workspace logs".to_string())?
    }

    pub async fn request(&self, method: &str, params: Value) -> Reply {
        if self.closing.load(Ordering::Acquire) { return Err("Launcher is shutting down".into()); }
        self.ready().await?;
        if self.closing.load(Ordering::Acquire) { return Err("Launcher is shutting down".into()); }
        self.raw_request(method, params, REQUEST_TIMEOUT).await
    }

    async fn raw_request(&self, method: &str, params: Value, deadline: Duration) -> Reply {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let mut frame = serde_json::to_vec(&json!({"id": id, "method": method, "params": params})).map_err(|e| e.to_string())?;
        frame.push(b'\n');
        if frame.len() > MAX_FRAME { return Err("RPC request exceeds 1 MiB".into()); }
        let (sender, receiver) = oneshot::channel();
        {
            let mut pending = self.pending.lock().map_err(|_| "Pending RPC map poisoned")?;
            if method != "shutdown" {
                if let Status::Failed(error) = &*self.status.borrow() { return Err(error.clone()); }
            }
            if pending.len() >= 256 { return Err("Too many pending RPC requests".into()); }
            pending.insert(id, sender);
        }
        let _registration = Registration { pending: self.pending.clone(), id };
        timeout(Duration::from_secs(5), async {
            let mut writer = self.writer.lock().await;
            let stdin = writer.as_mut().ok_or("Supervisor stdin closed")?;
            stdin.write_all(&frame).await.map_err(|e| format!("Write supervisor: {e}"))?;
            stdin.flush().await.map_err(|e| format!("Flush supervisor: {e}"))
        }).await.map_err(|_| "Supervisor write timed out".to_string())??;
        timeout(deadline, receiver).await.map_err(|_| format!("RPC {method} timed out; operation may still complete"))?
            .map_err(|_| "Supervisor response channel closed".to_string())?
    }

    pub async fn shutdown(&self) {
        // Every caller waits for the first shutdown to finish. In particular,
        // an exit during startup failure must not bypass backend cleanup.
        let _shutdown = self.shutdown_lock.lock().await;
        if self.closing.swap(true, Ordering::AcqRel) { return; }
        let _ = self.raw_request("shutdown", json!({}), Duration::from_secs(8)).await;
        // EOF is a second graceful shutdown signal, even if the request failed.
        if let Ok(mut writer) = timeout(Duration::from_secs(6), self.writer.lock()).await { writer.take(); }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(22);
        loop {
            let exited = self.process.lock().ok().and_then(|p| p.as_ref().map(|p| p.exited().unwrap_or(false))).unwrap_or(true);
            if exited || tokio::time::Instant::now() >= deadline { break; }
            sleep(Duration::from_millis(100)).await;
        }
        // Dropping the job also kills any descendants remaining after Python
        // exits. Existing user WeChat processes were never members of this job.
        if let Ok(mut process) = self.process.lock() {
            if let Some(process) = process.take() { let _ = process.kill_tree(); }
        }
        self.fail("Launcher shut down".into());
    }
}
