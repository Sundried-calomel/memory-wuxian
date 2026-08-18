use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde_json::Value;

pub(crate) mod cursor;
pub(crate) mod transaction;

pub(crate) use transaction::{
    append_bytes, append_jsonl, atomic_write, atomic_write_json, atomic_write_jsonl,
};

pub(crate) fn read_json(path: &Path) -> Result<Value> {
    Ok(serde_json::from_slice(
        &fs::read(path).with_context(|| format!("read {}", path.display()))?,
    )?)
}
