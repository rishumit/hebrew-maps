//! Starting, health-checking and stopping the bundled Python server.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// First port tried when nothing was remembered. A fixed port keeps the page origin
/// (`http://127.0.0.1:<port>`) stable, so localStorage (history, settings) and the
/// service-worker cache survive restarts.
pub const DEFAULT_PORT: u16 = 48080;
const PORT_ATTEMPTS: u16 = 20;

/// Server exe: $HMAPS_SERVER_EXE, else `hebrew-maps.exe` next to this exe (Inno Setup layout),
/// else `server/hebrew-maps.exe`.
pub fn locate_exe() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("HMAPS_SERVER_EXE") {
        let p = PathBuf::from(p);
        return p.is_file().then_some(p);
    }
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let name = if cfg!(windows) {
        "hebrew-maps.exe"
    } else {
        "hebrew-maps"
    };
    [dir.join(name), dir.join("server").join(name)]
        .into_iter()
        .find(|p| p.is_file())
}

/// `preferred`, then the next ports of the block, then DEFAULT_PORT's block.
pub fn candidate_ports(preferred: u16) -> Vec<u16> {
    let mut out: Vec<u16> = Vec::new();
    for base in [preferred, DEFAULT_PORT] {
        for i in 0..PORT_ATTEMPTS {
            if let Some(p) = base.checked_add(i) {
                if p >= 1024 && !out.contains(&p) {
                    out.push(p);
                }
            }
        }
    }
    out
}

pub fn port_is_free(port: u16) -> bool {
    // Something answering on the port (any program) means busy, even if bind would succeed
    // because the other side listens on a different address.
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
        return false;
    }
    TcpListener::bind(addr).is_ok()
}

pub struct ServerProcess {
    child: Child,
    #[cfg(windows)]
    _job: Option<win::Job>,
}

impl ServerProcess {
    pub fn spawn(exe: &Path, port: u16) -> std::io::Result<Self> {
        let mut cmd = Command::new(exe);
        cmd.args([
            "--no-browser",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
        ])
        // Same settings via env, for older server builds that do not parse arguments.
        .env("HMAPS_NO_BROWSER", "1")
        .env("HMAPS_HOST", "127.0.0.1")
        .env("HMAPS_PORT", port.to_string())
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
        if let Some(dir) = exe.parent() {
            cmd.current_dir(dir);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        #[cfg(target_os = "linux")]
        {
            // Development on Linux: the kernel sends SIGTERM to the server if the spawning thread
            // dies (it lives on in watch_server() for as long as the server runs).
            use std::os::unix::process::CommandExt;
            unsafe {
                cmd.pre_exec(|| {
                    libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                    Ok(())
                });
            }
        }
        let child = cmd.spawn()?;
        #[cfg(windows)]
        {
            let job = win::Job::kill_on_close()
                .and_then(|j| j.assign(&child).map(|_| j))
                .ok();
            Ok(Self { child, _job: job })
        }
        #[cfg(not(windows))]
        Ok(Self { child })
    }

    /// Exit code if the process has ended.
    pub fn exited(&mut self) -> Option<i32> {
        match self.child.try_wait() {
            Ok(Some(st)) => Some(st.code().unwrap_or(-1)),
            Ok(None) => None,
            Err(_) => Some(-1),
        }
    }

    /// Stop the server and everything it started.
    pub fn kill(&mut self) {
        #[cfg(windows)]
        if let Some(job) = &self._job {
            job.terminate();
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        if self.exited().is_none() {
            self.kill();
        }
    }
}

#[derive(Debug)]
pub enum WaitError {
    Exited(i32),
    Timeout,
}

/// Poll `/api/health` until our server answers. Returns early if the process dies
/// (so a foreign program on the same port is never mistaken for it).
pub fn wait_healthy(
    proc: &mut ServerProcess,
    port: u16,
    timeout: Duration,
) -> Result<(), WaitError> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Some(code) = proc.exited() {
            return Err(WaitError::Exited(code));
        }
        if health(port) {
            // Re-check: the answer must come while our process is alive.
            return match proc.exited() {
                Some(code) => Err(WaitError::Exited(code)),
                None => Ok(()),
            };
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err(WaitError::Timeout)
}

/// True if `GET /api/health` answers like hmaps: 200 (ok) or 503 (degraded, e.g. no
/// dataset yet – the page still loads and explains it), with a JSON `status`.
pub fn health(port: u16) -> bool {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = s.set_write_timeout(Some(Duration::from_secs(3)));
    let req = format!("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n");
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = Vec::with_capacity(1024);
    let _ = s.take(64 * 1024).read_to_end(&mut buf);
    is_hmaps_health(&String::from_utf8_lossy(&buf))
}

fn is_hmaps_health(resp: &str) -> bool {
    let status_ok = resp
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .is_some_and(|c| c == "200" || c == "503");
    status_ok && resp.contains("\"status\"") && resp.contains("\"version\"")
}

#[cfg(windows)]
mod win {
    //! Windows Job Object: the server is assigned to a job with KILL_ON_JOB_CLOSE. The job
    //! handle is owned by this process only, so when this process exits – normally, by crash
    //! or by Task Manager – Windows closes the handle and kills the server and its children.
    //! BREAKAWAY_OK lets exactly one kind of child leave the job: the installer of an in-app
    //! update, which the server starts with CREATE_BREAKAWAY_FROM_JOB (hmaps/api/appupdate.py)
    //! and which closes this window – it must not die with it. Ordinary children stay in the job.

    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct Job(HANDLE);

    // The handle is only used through the Win32 API, which is thread-safe for job objects.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn kill_on_close() -> std::io::Result<Self> {
            unsafe {
                let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if h.is_null() {
                    return Err(std::io::Error::last_os_error());
                }
                let job = Job(h);
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags =
                    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
                let ok = SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(job)
            }
        }

        pub fn assign(&self, child: &Child) -> std::io::Result<()> {
            let ok = unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) };
            if ok == 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        }

        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 0);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ports() {
        let c = candidate_ports(50000);
        assert_eq!(c[0], 50000);
        assert!(c.contains(&DEFAULT_PORT));
        let d = candidate_ports(DEFAULT_PORT);
        assert_eq!(d.len(), PORT_ATTEMPTS as usize);
        assert!(candidate_ports(65530).iter().all(|&p| p >= 1024));
        assert!(!candidate_ports(80).contains(&80));
    }

    #[test]
    fn health_parsing() {
        let ok = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"status\":\"ok\",\"version\":\"1.0\"}";
        let degraded =
            "HTTP/1.1 503 Service Unavailable\r\n\r\n{\"status\":\"degraded\",\"version\":\"1.0\"}";
        assert!(is_hmaps_health(ok));
        assert!(is_hmaps_health(degraded));
        assert!(!is_hmaps_health(
            "HTTP/1.1 200 OK\r\n\r\n<html>other app</html>"
        ));
        assert!(!is_hmaps_health(
            "HTTP/1.1 404 Not Found\r\n\r\n{\"status\":1,\"version\":2}"
        ));
        assert!(!is_hmaps_health(""));
    }

    #[test]
    fn busy_port_detected() {
        let l = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = l.local_addr().unwrap().port();
        assert!(!port_is_free(port));
        drop(l);
    }
}
