use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::json;

const WAL_FORMAT: &str = "memory-wuxian-capture-wal-v1";
const MAX_WAL_BYTES: u64 = 4 * 1024 * 1024;
const COMPACT_WAL_BYTES: u64 = 2 * 1024 * 1024;
const MAX_PENDING_TRANSACTIONS: usize = 256;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub(crate) struct WalIntent {
    pub(crate) transaction_id: String,
    pub(crate) session_id: String,
    pub(crate) source_path: String,
    pub(crate) cursor_before_line: u64,
    pub(crate) cursor_after_line: u64,
    pub(crate) committed_byte_offset: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct WalState {
    pub(crate) pending: BTreeMap<String, WalIntent>,
    pub(crate) last_durable_transaction: Option<String>,
}

pub(crate) struct CaptureWal {
    path: PathBuf,
}

impl CaptureWal {
    pub(crate) fn new(root: &Path) -> Self {
        Self {
            path: root.join("imports/codex/capture-wal.jsonl"),
        }
    }

    pub(crate) fn state(&self) -> Result<WalState> {
        if !self.path.exists() {
            return Ok(WalState::default());
        }
        let metadata = fs::metadata(&self.path)?;
        if metadata.len() > MAX_WAL_BYTES {
            bail!("capture WAL exceeds the bounded read limit")
        }
        let bytes = fs::read(&self.path)?;
        let mut state = WalState::default();
        for (index, line) in bytes.split(|byte| *byte == b'\n').enumerate() {
            if line.is_empty() {
                continue;
            }
            let value: serde_json::Value = serde_json::from_slice(line)
                .with_context(|| format!("invalid capture WAL line {}", index + 1))?;
            if value.get("format").and_then(serde_json::Value::as_str) != Some(WAL_FORMAT) {
                bail!("capture WAL format mismatch at line {}", index + 1)
            }
            let phase = value.get("phase").and_then(serde_json::Value::as_str);
            let transaction_id = value
                .get("transaction_id")
                .and_then(serde_json::Value::as_str)
                .context("capture WAL transaction_id is missing")?;
            match phase {
                Some("prepared") => {
                    let intent: WalIntent = serde_json::from_value(
                        value
                            .get("intent")
                            .cloned()
                            .context("capture WAL intent is missing")?,
                    )?;
                    if intent.transaction_id != transaction_id {
                        bail!("capture WAL intent identity mismatch")
                    }
                    state.pending.insert(transaction_id.to_owned(), intent);
                }
                Some("committed" | "recovered") => {
                    state.pending.remove(transaction_id);
                    state.last_durable_transaction = Some(transaction_id.to_owned());
                }
                _ => bail!("capture WAL phase is invalid"),
            }
            if state.pending.len() > MAX_PENDING_TRANSACTIONS {
                bail!("capture WAL has too many pending transactions")
            }
        }
        Ok(state)
    }

    pub(crate) fn begin(&self, intent: &WalIntent) -> Result<()> {
        if let Some(existing) = self.state()?.pending.get(&intent.transaction_id) {
            if existing != intent {
                bail!("capture WAL transaction identity was reused with different intent")
            }
            return Ok(());
        }
        self.append(&json!({
            "format": WAL_FORMAT,
            "phase": "prepared",
            "transaction_id": intent.transaction_id,
            "intent": intent,
        }))?;
        self.compact_if_needed()
    }

    pub(crate) fn commit(&self, transaction_id: &str) -> Result<()> {
        if !self.state()?.pending.contains_key(transaction_id) {
            bail!("capture WAL commit has no prepared transaction")
        }
        self.append(&json!({
            "format": WAL_FORMAT,
            "phase": "committed",
            "transaction_id": transaction_id,
        }))?;
        self.compact_if_needed()
    }

    pub(crate) fn recover(&self, transaction_id: &str) -> Result<()> {
        if !self.state()?.pending.contains_key(transaction_id) {
            return Ok(());
        }
        self.append(&json!({
            "format": WAL_FORMAT,
            "phase": "recovered",
            "transaction_id": transaction_id,
        }))?;
        self.compact_if_needed()
    }

    fn append(&self, value: &serde_json::Value) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut bytes = serde_json::to_vec(value)?;
        if bytes.len() > 4096 {
            bail!("capture WAL event exceeds the bounded event limit")
        }
        bytes.push(b'\n');
        self.ensure_append_capacity(bytes.len() as u64)?;
        super::transaction::append_bytes_prepared(&self.path, &bytes)
    }

    fn ensure_append_capacity(&self, append_bytes: u64) -> Result<()> {
        let current_bytes = Self::metadata_length(self.path.metadata().map(|value| value.len()))?;
        if current_bytes > MAX_WAL_BYTES {
            bail!("capture WAL exceeds the bounded write limit")
        }
        if current_bytes.saturating_add(append_bytes) > MAX_WAL_BYTES {
            self.compact()?;
        }
        let compacted_bytes = Self::metadata_length(self.path.metadata().map(|value| value.len()))?;
        if compacted_bytes.saturating_add(append_bytes) > MAX_WAL_BYTES {
            bail!("capture WAL append would exceed the bounded write limit")
        }
        Ok(())
    }

    fn metadata_length(result: std::io::Result<u64>) -> Result<u64> {
        match result {
            Ok(length) => Ok(length),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(0),
            Err(error) => Err(error.into()),
        }
    }

    pub(crate) fn compact_if_needed(&self) -> Result<()> {
        if !self.path.exists() || fs::metadata(&self.path)?.len() <= COMPACT_WAL_BYTES {
            return Ok(());
        }
        self.compact()
    }

    fn compact(&self) -> Result<()> {
        let state = self.state()?;
        let mut bytes = Vec::new();
        for intent in state.pending.values() {
            bytes.extend(serde_json::to_vec(&json!({
                "format": WAL_FORMAT,
                "phase": "prepared",
                "transaction_id": intent.transaction_id,
                "intent": intent,
            }))?);
            bytes.push(b'\n');
        }
        if let Some(transaction_id) = state.last_durable_transaction {
            bytes.extend(serde_json::to_vec(&json!({
                "format": WAL_FORMAT,
                "phase": "committed",
                "transaction_id": transaction_id,
            }))?);
            bytes.push(b'\n');
        }
        if bytes.len() as u64 > MAX_WAL_BYTES {
            bail!("compacted capture WAL exceeds the bounded write limit")
        }
        super::transaction::atomic_write(&self.path, &bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::OpenOptions;
    use std::io::Write;

    fn intent() -> WalIntent {
        WalIntent {
            transaction_id: "tx-1".to_owned(),
            session_id: "session-1".to_owned(),
            source_path: "C:/sessions/rollout.jsonl".to_owned(),
            cursor_before_line: 1,
            cursor_after_line: 2,
            committed_byte_offset: 128,
        }
    }

    #[test]
    fn interrupted_transaction_is_visible_and_replay_is_idempotent() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let wal = CaptureWal::new(temporary.path());
        wal.begin(&intent())?;
        wal.begin(&intent())?;
        assert_eq!(wal.state()?.pending.len(), 1);
        wal.commit("tx-1")?;
        assert!(wal.state()?.pending.is_empty());
        assert_eq!(
            wal.state()?.last_durable_transaction.as_deref(),
            Some("tx-1")
        );
        Ok(())
    }

    #[test]
    fn malformed_or_partial_wal_fails_closed() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let wal = CaptureWal::new(temporary.path());
        wal.begin(&intent())?;
        let mut file = OpenOptions::new().append(true).open(&wal.path)?;
        file.write_all(b"{\"format\":")?;
        file.sync_data()?;
        assert!(wal.state().is_err());
        Ok(())
    }

    #[test]
    fn prepared_event_bytes_remain_exact() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let wal = CaptureWal::new(temporary.path());
        wal.begin(&intent())?;
        assert_eq!(
            fs::read(&wal.path)?,
            br#"{"format":"memory-wuxian-capture-wal-v1","intent":{"committed_byte_offset":128,"cursor_after_line":2,"cursor_before_line":1,"session_id":"session-1","source_path":"C:/sessions/rollout.jsonl","transaction_id":"tx-1"},"phase":"prepared","transaction_id":"tx-1"}
"#,
        );
        Ok(())
    }

    #[test]
    fn recovery_is_idempotent_and_records_the_durable_transaction() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let wal = CaptureWal::new(temporary.path());
        wal.begin(&intent())?;
        wal.recover("tx-1")?;
        let recovered_size = fs::metadata(&wal.path)?.len();
        wal.recover("tx-1")?;
        assert_eq!(fs::metadata(&wal.path)?.len(), recovered_size);
        let state = wal.state()?;
        assert!(state.pending.is_empty());
        assert_eq!(state.last_durable_transaction.as_deref(), Some("tx-1"));
        Ok(())
    }

    #[test]
    fn near_limit_append_compacts_before_write_and_stays_bounded() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let wal = CaptureWal::new(temporary.path());
        fs::create_dir_all(wal.path.parent().expect("WAL has a parent"))?;
        let mut line = serde_json::to_vec(&json!({
            "format": WAL_FORMAT,
            "phase": "committed",
            "transaction_id": "seed",
        }))?;
        line.push(b'\n');
        let repeated = line.repeat(((MAX_WAL_BYTES - 1) / line.len() as u64) as usize);
        fs::write(&wal.path, &repeated)?;
        assert!(fs::metadata(&wal.path)?.len() > COMPACT_WAL_BYTES);

        let mut next = intent();
        next.transaction_id = "tx-after-compaction".to_owned();
        wal.begin(&next)?;

        let compacted_size = fs::metadata(&wal.path)?.len();
        assert!(compacted_size <= MAX_WAL_BYTES);
        assert!(compacted_size < COMPACT_WAL_BYTES);
        assert_eq!(wal.state()?.pending.get(&next.transaction_id), Some(&next));
        Ok(())
    }

    #[test]
    fn metadata_failures_other_than_not_found_fail_closed() {
        assert_eq!(
            CaptureWal::metadata_length(Err(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "missing",
            )))
            .expect("not found means an empty new WAL"),
            0
        );
        assert!(
            CaptureWal::metadata_length(Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "denied",
            )))
            .is_err()
        );
    }
}
