use std::fs;
use std::path::Path;
use std::time::SystemTime;

use chrono::{DateTime, Utc};
use serde_json::Value;

pub(crate) fn portable_path(path: &Path) -> String {
    let value = path.to_string_lossy();
    #[cfg(windows)]
    {
        if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
            return format!(r"\\{rest}");
        }
        if let Some(rest) = value.strip_prefix(r"\\?\") {
            return rest.to_owned();
        }
    }
    value.into_owned()
}

pub(crate) fn covers_source(cursor: &Value, path: &Path, metadata: &fs::Metadata) -> bool {
    if cursor.get("source_path").and_then(Value::as_str) != Some(portable_path(path).as_str()) {
        return false;
    }
    let modified: DateTime<Utc> = metadata.modified().unwrap_or(SystemTime::now()).into();
    let same_mtime = cursor
        .get("source_mtime")
        .and_then(Value::as_str)
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .is_some_and(|value| value.timestamp_nanos_opt() == modified.timestamp_nanos_opt());
    if cursor.get("complete").and_then(Value::as_bool) == Some(true) {
        return same_mtime
            && cursor
                .get("committed_byte_offset")
                .or_else(|| cursor.get("source_size"))
                .and_then(Value::as_u64)
                == Some(metadata.len());
    }
    cursor.get("source_size").and_then(Value::as_u64) == Some(metadata.len()) && same_mtime
}

pub(crate) fn requires_sync(cursor: &Value, path: &Path, metadata: &fs::Metadata) -> bool {
    if cursor.get("source_path").and_then(Value::as_str) != Some(portable_path(path).as_str()) {
        return true;
    }
    let length = metadata.len();
    if cursor.get("source_size").and_then(Value::as_u64) != Some(length)
        || cursor.get("committed_byte_offset").and_then(Value::as_u64) != Some(length)
        || cursor.get("observed_source_size").and_then(Value::as_u64) != Some(length)
        || cursor.get("complete").and_then(Value::as_bool) != Some(true)
    {
        return true;
    }
    let current_modified: DateTime<Utc> = metadata.modified().unwrap_or(SystemTime::now()).into();
    !cursor
        .get("source_mtime")
        .and_then(Value::as_str)
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .is_some_and(|value| value.timestamp_nanos_opt() == current_modified.timestamp_nanos_opt())
}
