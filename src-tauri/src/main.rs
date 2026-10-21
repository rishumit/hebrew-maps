//! מפות בעברית – desktop shell (Tauri 2).
//!
//! Starts the bundled Python server (the PyInstaller `hebrew-maps.exe`, installed next to
//! this exe) on a local port, shows a loading screen until `/api/health` answers, then
//! loads `http://127.0.0.1:<port>/desktop.html` – the tabbed shell with one map iframe per tab –
//! in a window without a native frame: the pages draw their own RTL title bar (window buttons
//! on the left), using only the window commands granted to them (see `WINDOW_CONTROLS`).
//! Closing the window stops the server; on Windows the server lives in a Job Object with
//! KILL_ON_JOB_CLOSE, so it also dies (with any children) if this process crashes or is killed.
//!
//! Environment:
//!   HMAPS_URL          dev mode – just open this URL, do not start a server
//!                      (`…/map.html#…` is opened in the tabbed shell, `…/desktop.html#…`)
//!   HMAPS_SERVER_EXE   path of the server exe (default: `hebrew-maps.exe` next to this exe)
//!   HMAPS_DESKTOP_PORT preferred port (default: last used, else 48080)
//!
//! See docs/adr/0009-desktop-tauri.md.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod server;

use std::io::Write as _;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use tauri::ipc::CapabilityBuilder;
use tauri::webview::NewWindowResponse;
use tauri::{
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_window_state::StateFlags;

const MAIN: &str = "main";
const TITLE: &str = "מפות בעברית";
const START_PAGE: &str = "/desktop.html";
const MAP_PAGE: &str = "/map.html";
/// Everything the pages may ask of the window: the custom title bar's buttons and drag area
/// (`data-tauri-drag-region` uses start_dragging / internal_toggle_maximize). Nothing else –
/// no events, no file system, no shell; the window title follows `document.title` from Rust.
/// The local loading page gets these through `capabilities/window-controls-local.json`.
const WINDOW_CONTROLS: &[&str] = &[
    "core:window:allow-minimize",
    "core:window:allow-toggle-maximize",
    "core:window:allow-internal-toggle-maximize",
    "core:window:allow-close",
    "core:window:allow-start-dragging",
    "core:window:allow-is-maximized",
];
const HEALTH_TIMEOUT: Duration = Duration::from_secs(120);
/// Startup failures tolerated (each on the next port) before giving up.
const MAX_EARLY_EXITS: u32 = 3;

/// The running server (if we started one). Killed on exit.
struct ServerState(Mutex<Option<server::ServerProcess>>);

fn main() {
    tauri::Builder::default()
        // Must be registered first: a second launch just focuses the existing window.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            focus_main(app)
        }))
        .plugin(
            tauri_plugin_window_state::Builder::default()
                // DECORATIONS: an older version saved `decorations: true`; the frame is always ours now.
                .with_state_flags(
                    StateFlags::all() & !StateFlags::VISIBLE & !StateFlags::DECORATIONS,
                )
                .build(),
        )
        .manage(ServerState(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let dev_url = std::env::var("HMAPS_URL")
                .ok()
                .filter(|s| !s.trim().is_empty());
            let extra_host = dev_url
                .as_deref()
                .and_then(|u| Url::parse(u).ok())
                .and_then(|u| u.host_str().map(str::to_owned));

            let window = create_main_window(&handle, extra_host)?;
            window.show()?;
            let splash = window.url()?;

            std::thread::spawn(move || {
                let result = match dev_url {
                    Some(url) => {
                        log(&handle, &format!("dev mode: HMAPS_URL={url}"));
                        Url::parse(url.trim())
                            .map(shell_url)
                            .map_err(|e| format!("HMAPS_URL אינה כתובת תקינה: {e}"))
                    }
                    None => start_server(&handle),
                };
                let result = result.and_then(|url| {
                    grant_window_controls(&handle, &url)?;
                    Ok(url)
                });
                let started = result.is_ok();
                let target = match result {
                    Ok(url) => url,
                    Err(msg) => {
                        log(&handle, &format!("error: {msg}"));
                        error_url(&splash, &msg, &log_path(&handle))
                    }
                };
                if let Some(w) = handle.get_webview_window(MAIN) {
                    let _ = w.navigate(target);
                }
                if started {
                    watch_server(&handle, &splash);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Tauri application")
        .run(|app, event| match event {
            RunEvent::WindowEvent {
                label,
                event: WindowEvent::Destroyed,
                ..
            } if label == MAIN => {
                // Popups (e.g. a report opened in a new window) must not keep the app alive.
                app.exit(0);
            }
            RunEvent::Exit => stop_server(app),
            _ => {}
        });
}

fn create_main_window(app: &AppHandle, extra_host: Option<String>) -> tauri::Result<WebviewWindow> {
    let nav_app = app.clone();
    let popup_app = app.clone();
    let nav_host = extra_host.clone();
    let shell_app = app.clone();
    WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
        .title(TITLE)
        .inner_size(1280.0, 820.0)
        .min_inner_size(640.0, 480.0)
        .center()
        // Our own RTL title bar (web/desktop.html, ui/index.html). Windows keeps the resize borders,
        // and `shadow` keeps the native shadow and the rounded corners of Windows 11.
        .decorations(false)
        .shadow(true)
        .visible(false) // shown after window-state has restored size/position
        // The taskbar / Alt+Tab title follows the page (the shell sets "<tab title> – מפות בעברית").
        .on_document_title_changed(|w, title| {
            let title = title.trim();
            let _ = w.set_title(if title.is_empty() { TITLE } else { title });
        })
        .on_navigation(move |url| {
            if is_internal(url, nav_host.as_deref()) {
                return true;
            }
            open_external(&nav_app, url);
            false
        })
        .on_new_window(move |url, _features| {
            if is_internal(&url, extra_host.as_deref()) {
                // A map in a new window (window.open, target=_blank) becomes a tab of the shell.
                if open_in_tab(&shell_app, &url) {
                    return NewWindowResponse::Deny;
                }
                NewWindowResponse::Allow
            } else {
                open_external(&popup_app, &url);
                NewWindowResponse::Deny
            }
        })
        .build()
}

/// `…/map.html#x` → `…/desktop.html#x` (the tabbed shell opens that map in a tab). The window has no
/// native frame, so it must show a page with our title bar.
fn shell_url(mut url: Url) -> Url {
    if matches!(url.scheme(), "http" | "https") && url.path() == MAP_PAGE {
        url.set_path(START_PAGE);
    }
    url
}

/// The remote capability for the pages of the server we show: exactly its origin
/// (`http://127.0.0.1:<port>/*`), the main window only, `WINDOW_CONTROLS` only.
fn window_controls_capability(url: &Url) -> Option<CapabilityBuilder> {
    let origin = url.origin();
    if !origin.is_tuple() {
        return None; // tauri://, data: … – the local capability file covers the bundled page
    }
    let cap = CapabilityBuilder::new("window-controls-remote")
        .remote(format!("{}/*", origin.ascii_serialization()))
        .local(false)
        .window(MAIN);
    Some(WINDOW_CONTROLS.iter().fold(cap, |c, p| c.permission(*p)))
}

fn grant_window_controls(app: &AppHandle, url: &Url) -> Result<(), String> {
    match window_controls_capability(url) {
        Some(cap) => app
            .add_capability(cap)
            .map_err(|e| format!("הגדרת הרשאות החלון נכשלה: {e}")),
        None => Ok(()),
    }
}

/// If the main window shows the shell, open a local map URL as a new tab there.
fn open_in_tab(app: &AppHandle, url: &Url) -> bool {
    if url.path() != MAP_PAGE {
        return false;
    }
    let Some(w) = app.get_webview_window(MAIN) else {
        return false;
    };
    let Ok(current) = w.url() else { return false };
    if current.path() != START_PAGE || current.origin() != url.origin() {
        return false;
    }
    // JSON string literal of the URL: no way to break out of the call.
    let arg = serde_json::to_string(url.as_str()).unwrap_or_else(|_| "\"\"".into());
    w.eval(format!(
        "window.hmapsDesktop && window.hmapsDesktop.openUrl({arg})"
    ))
    .is_ok()
}

/// Our own pages: the bundled loading screen and the local server.
fn is_internal(url: &Url, extra_host: Option<&str>) -> bool {
    match url.scheme() {
        "tauri" | "about" | "data" | "blob" => true,
        "http" | "https" => match url.host_str() {
            Some("127.0.0.1" | "localhost" | "[::1]" | "tauri.localhost") => true,
            Some(h) => extra_host == Some(h),
            None => false,
        },
        _ => false,
    }
}

/// External links (http/https/mailto/tel) go to the system browser / handler.
fn open_external(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto" | "tel") {
        log(app, &format!("open externally: {url}"));
        if let Err(e) = open::that_detached(url.as_str()) {
            log(app, &format!("could not open {url}: {e}"));
        }
    }
}

fn focus_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(MAIN) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn start_server(app: &AppHandle) -> Result<Url, String> {
    let exe = server::locate_exe().ok_or_else(|| {
        "לא נמצא שרת המפות (hebrew-maps.exe) ליד האפליקציה. נסו להתקין מחדש.".to_string()
    })?;
    let port_file = app
        .path()
        .app_config_dir()
        .ok()
        .map(|d| d.join("server-port"));
    let preferred = std::env::var("HMAPS_DESKTOP_PORT")
        .ok()
        .and_then(|p| p.trim().parse::<u16>().ok())
        .or_else(|| {
            port_file
                .as_ref()
                .and_then(|f| std::fs::read_to_string(f).ok())
                .and_then(|s| s.trim().parse::<u16>().ok())
        })
        .unwrap_or(server::DEFAULT_PORT);

    let mut last_err = String::new();
    let mut early_exits = 0;
    for port in server::candidate_ports(preferred) {
        if !server::port_is_free(port) {
            // Another program (e.g. a server from an older install) – leave it alone.
            log(app, &format!("port {port} is busy, trying the next one"));
            continue;
        }
        log(app, &format!("starting {} on port {port}", exe.display()));
        let mut proc = server::ServerProcess::spawn(&exe, port)
            .map_err(|e| format!("הפעלת שרת המפות נכשלה: {e}"))?;
        match server::wait_healthy(&mut proc, port, HEALTH_TIMEOUT) {
            Ok(()) => {
                log(app, &format!("server ready on port {port}"));
                if let Some(f) = &port_file {
                    let _ = std::fs::create_dir_all(f.parent().unwrap_or(f));
                    let _ = std::fs::write(f, port.to_string());
                }
                *app.state::<ServerState>().0.lock().unwrap() = Some(proc);
                return Url::parse(&format!("http://127.0.0.1:{port}{START_PAGE}"))
                    .map_err(|e| e.to_string());
            }
            Err(server::WaitError::Exited(code)) => {
                // Lost a race for the port, or a startup failure – try the next port once or twice.
                last_err = format!("שרת המפות נסגר מיד לאחר ההפעלה (קוד {code})");
                log(app, &format!("server exited early with {code}"));
                early_exits += 1;
                if early_exits >= MAX_EARLY_EXITS {
                    break;
                }
            }
            Err(server::WaitError::Timeout) => {
                proc.kill();
                return Err("שרת המפות לא הגיב בזמן.".into());
            }
        }
    }
    Err(if last_err.is_empty() {
        "לא נמצאה כתובת פנויה להפעלת שרת המפות.".into()
    } else {
        last_err
    })
}

/// If the server dies while the window is open, show the error page instead of a broken map.
fn watch_server(app: &AppHandle, splash: &Url) {
    loop {
        std::thread::sleep(Duration::from_secs(3));
        let Some(state) = app.try_state::<ServerState>() else {
            return;
        };
        let mut guard = state.0.lock().unwrap();
        let Some(proc) = guard.as_mut() else { return }; // dev mode, or stopped on exit
        if let Some(code) = proc.exited() {
            guard.take();
            drop(guard);
            let msg =
                format!("שרת המפות נעצר באופן בלתי צפוי (קוד {code}). סגרו את החלון ופתחו מחדש.");
            log(app, &msg);
            if let Some(w) = app.get_webview_window(MAIN) {
                let _ = w.navigate(error_url(splash, &msg, &log_path(app)));
            }
            return;
        }
    }
}

fn stop_server(app: &AppHandle) {
    if let Some(state) = app.try_state::<ServerState>() {
        if let Some(mut proc) = state.0.lock().unwrap().take() {
            log(app, "stopping server");
            proc.kill();
        }
    }
}

fn error_url(splash: &Url, msg: &str, log: &std::path::Path) -> Url {
    let mut u = splash.clone();
    u.query_pairs_mut()
        .clear()
        .append_pair("error", msg)
        .append_pair("log", &log.display().to_string());
    u
}

fn log_path(app: &AppHandle) -> PathBuf {
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| std::env::temp_dir())
        .join("hebrew-maps-desktop.log")
}

fn log(app: &AppHandle, msg: &str) {
    let path = log_path(app);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let _ = writeln!(f, "{ts} {msg}");
    }
    #[cfg(debug_assertions)]
    eprintln!("[desktop] {msg}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn internal_urls() {
        let u = |s: &str| Url::parse(s).unwrap();
        assert!(is_internal(&u("http://127.0.0.1:48080/map.html"), None));
        assert!(is_internal(&u("http://localhost:8080/"), None));
        assert!(is_internal(&u("http://tauri.localhost/index.html"), None));
        assert!(is_internal(&u("tauri://localhost/index.html"), None));
        assert!(!is_internal(&u("https://he.wikipedia.org/wiki/x"), None));
        assert!(!is_internal(&u("https://127.0.0.1.evil.com/"), None));
        assert!(is_internal(
            &u("http://192.168.1.5:8080/"),
            Some("192.168.1.5")
        ));
        assert!(!is_internal(&u("mailto:a@b.c"), None));
    }

    #[test]
    fn map_url_opens_in_the_shell() {
        let u = |s: &str| Url::parse(s).unwrap();
        assert_eq!(
            shell_url(u("http://127.0.0.1:8080/map.html#map=12/32/35&q=x")).as_str(),
            "http://127.0.0.1:8080/desktop.html#map=12/32/35&q=x"
        );
        assert_eq!(
            shell_url(u("http://127.0.0.1:8080/desktop.html")).as_str(),
            "http://127.0.0.1:8080/desktop.html"
        );
        assert_eq!(
            shell_url(u("http://127.0.0.1:8080/other.html")).path(),
            "/other.html"
        );
    }

    #[test]
    fn window_controls_only_for_the_server_origin() {
        let u = |s: &str| Url::parse(s).unwrap();
        assert!(window_controls_capability(&u("tauri://localhost/index.html")).is_none());
        assert!(window_controls_capability(&u("data:text/html,x")).is_none());
        let cap = window_controls_capability(&u("http://127.0.0.1:48080/desktop.html#x"))
            .expect("capability");
        let json = serde_json::to_value(cap_inner(cap)).unwrap();
        assert_eq!(json["remote"]["urls"][0], "http://127.0.0.1:48080/*");
        assert_eq!(json["local"], false);
        assert_eq!(json["windows"][0], MAIN);
        let perms: Vec<String> = json["permissions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p.as_str().unwrap().to_owned())
            .collect();
        assert_eq!(perms, WINDOW_CONTROLS);
        assert!(perms.iter().all(|p| p.starts_with("core:window:allow-")));
    }

    /// The capability as the JSON a capability file would contain.
    fn cap_inner(cap: CapabilityBuilder) -> tauri::utils::acl::capability::Capability {
        use tauri::ipc::RuntimeCapability;
        match cap.build() {
            tauri::utils::acl::capability::CapabilityFile::Capability(c) => c,
            _ => unreachable!(),
        }
    }

    #[test]
    fn local_capability_file_matches() {
        let file: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/window-controls-local.json"))
                .unwrap();
        assert_eq!(file["windows"], serde_json::json!([MAIN]));
        assert_eq!(file["permissions"], serde_json::json!(WINDOW_CONTROLS));
        assert!(file.get("remote").is_none());
    }

    #[test]
    fn error_url_keeps_splash_and_encodes() {
        let splash = Url::parse("http://tauri.localhost/index.html").unwrap();
        let e = error_url(
            &splash,
            "שגיאה & עוד",
            std::path::Path::new("C:\\x\\log.txt"),
        );
        assert_eq!(e.path(), "/index.html");
        let pairs: Vec<_> = e.query_pairs().collect();
        assert_eq!(pairs[0].1, "שגיאה & עוד");
        assert_eq!(pairs[1].1, "C:\\x\\log.txt");
    }
}
