#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(windows)]
use std::fs;
#[cfg(windows)]
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::Command;

#[cfg(windows)]
use anyhow::{Context, Result, ensure};
#[cfg(windows)]
use serde::Deserialize;

#[cfg(windows)]
#[derive(Deserialize)]
struct LauncherConfig {
    schema_version: u32,
    python_executable: PathBuf,
    archive_root: PathBuf,
}

#[cfg(windows)]
fn main() {
    if let Err(error) = launch() {
        let log = std::env::temp_dir().join("memory-wuxian-dashboard-launcher.log");
        let _ = fs::write(log, format!("{error:#}\n"));
        std::process::exit(1);
    }
}

#[cfg(not(windows))]
fn main() {}

#[cfg(windows)]
fn launch() -> Result<()> {
    let executable = std::env::current_exe().context("resolve launcher path")?;
    let bin_dir = executable
        .parent()
        .context("launcher has no parent directory")?;
    let skill_root = bin_dir
        .parent()
        .context("launcher is not inside the Skill bin directory")?;
    ensure!(
        bin_dir.file_name().is_some_and(|name| name == "bin"),
        "launcher must run from the Skill bin directory"
    );

    let codex_home = codex_home_from_skill(skill_root)?;
    let config_path = codex_home.join("memory-wuxian-dashboard-launcher.json");
    let config: LauncherConfig = serde_json::from_slice(
        &fs::read(&config_path).with_context(|| format!("read {}", config_path.display()))?,
    )
    .context("parse dashboard launcher configuration")?;
    ensure!(
        config.schema_version == 1,
        "unsupported launcher configuration version"
    );

    let python = canonical_file(&config.python_executable, "Python runtime")?;
    let dashboard = canonical_file(
        &skill_root.join("scripts").join("memory_dashboard.py"),
        "dashboard script",
    )?;
    let skill_config = canonical_file(&skill_root.join("config.yaml"), "Skill configuration")?;
    ensure!(
        config.archive_root.is_absolute(),
        "archive root must be absolute"
    );
    ensure!(config.archive_root.is_dir(), "archive root does not exist");

    let self_check = std::env::args_os().any(|argument| argument == "--self-check");
    let mut command = Command::new(python);
    command
        .arg(dashboard)
        .arg("--root")
        .arg(config.archive_root)
        .arg("--config")
        .arg(skill_config)
        .arg("--port")
        .arg("0")
        .arg("--window");
    if self_check {
        let status = command
            .arg("--self-check")
            .status()
            .context("self-check Memory Wuxian dashboard")?;
        ensure!(
            status.success(),
            "dashboard self-check failed with {status}"
        );
    } else {
        let mut child = command.spawn().context("start Memory Wuxian dashboard")?;
        std::thread::sleep(std::time::Duration::from_millis(1200));
        if let Some(status) = child.try_wait().context("inspect dashboard startup")? {
            ensure!(
                status.success(),
                "dashboard exited during startup with {status}"
            );
        }
    }
    Ok(())
}

#[cfg(windows)]
fn codex_home_from_skill(skill_root: &Path) -> Result<PathBuf> {
    let skills_root = skill_root.parent().context("Skill root has no parent")?;
    ensure!(
        skills_root.file_name().is_some_and(|name| name == "skills"),
        "Skill must be installed under .codex\\skills"
    );
    let codex_home = skills_root
        .parent()
        .context("skills directory has no parent")?;
    ensure!(
        codex_home.file_name().is_some_and(|name| name == ".codex"),
        "Skill must be installed under .codex\\skills"
    );
    Ok(codex_home.to_path_buf())
}

#[cfg(windows)]
fn canonical_file(path: &Path, label: &str) -> Result<PathBuf> {
    ensure!(path.is_absolute(), "{label} path must be absolute");
    ensure!(path.is_file(), "{label} does not exist");
    Ok(path.to_path_buf())
}
