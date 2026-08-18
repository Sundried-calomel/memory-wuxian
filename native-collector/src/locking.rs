use std::fs::{self, OpenOptions};
use std::path::Path;

use anyhow::Result;
use fs2::FileExt;

pub(crate) fn with_exclusive_lock<T>(
    path: &Path,
    operation: impl FnOnce() -> Result<T>,
) -> Result<T> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(path)?;
    FileExt::lock_exclusive(&file)?;
    let result = operation();
    FileExt::unlock(&file)?;
    result
}
