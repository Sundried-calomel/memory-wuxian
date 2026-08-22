use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

use anyhow::Result;
use serde_json::Value;

pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_file_name(format!(
        ".{}.tmp-{}",
        path.file_name().unwrap().to_string_lossy(),
        std::process::id()
    ));
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    Ok(())
}

pub(crate) fn atomic_write_json(path: &Path, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    atomic_write(path, &bytes)
}

pub(crate) fn atomic_write_jsonl(path: &Path, values: &[Value]) -> Result<()> {
    let mut bytes = Vec::new();
    for value in values {
        bytes.extend(serde_json::to_vec(value)?);
        bytes.push(b'\n');
    }
    atomic_write(path, &bytes)
}

pub(crate) fn append_bytes(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    append_bytes_prepared(path, bytes)
}

pub(super) fn append_bytes_prepared(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_data()?;
    Ok(())
}

pub(crate) fn append_jsonl(path: &Path, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    append_bytes(path, &bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prepared_append_preserves_parent_creation_boundary() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let path = temporary.path().join("missing").join("records.jsonl");
        assert!(append_bytes_prepared(&path, b"record\n").is_err());
        assert!(!path.parent().expect("path has parent").exists());

        append_bytes(&path, b"record\n")?;
        assert_eq!(fs::read(&path)?, b"record\n");
        Ok(())
    }
}
