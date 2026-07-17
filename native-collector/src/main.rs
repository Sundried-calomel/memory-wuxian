use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
#[cfg(not(target_os = "macos"))]
use std::sync::mpsc::{self, RecvTimeoutError};
use std::time::{Duration, SystemTime};

#[cfg(target_os = "macos")]
use std::ffi::CString;
#[cfg(target_os = "macos")]
use std::os::fd::{AsRawFd, FromRawFd};
#[cfg(target_os = "macos")]
use std::os::unix::ffi::OsStrExt;

use anyhow::{Context, Result, anyhow, bail};
use chrono::{DateTime, FixedOffset, Local, SecondsFormat, Utc};
use clap::Parser;
use fs2::FileExt;
#[cfg(not(target_os = "macos"))]
use notify::{Event, RecommendedWatcher, RecursiveMode, Watcher};
use regex::Regex;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

const RAW_MARKER: &str = "<!-- memory-wuxian-record -->";

#[derive(Parser, Debug)]
#[command(
    version,
    about = "Event-driven native Codex collector for Memory Wuxian"
)]
struct Args {
    #[arg(long)]
    archive_root: PathBuf,
    #[arg(long)]
    config: PathBuf,
    #[arg(long, default_value = "~/.codex/sessions")]
    sessions_root: PathBuf,
    #[arg(long)]
    since: Option<String>,
    #[arg(long, default_value_t = 400)]
    debounce_ms: u64,
    #[arg(long)]
    once: bool,
    #[arg(long = "session-file")]
    session_files: Vec<PathBuf>,
}

#[derive(Debug, Default, Deserialize)]
struct Config {
    #[serde(default)]
    summaries: SummaryConfig,
    #[serde(default)]
    backup: BackupConfig,
    #[serde(default)]
    safety: SafetyConfig,
}

#[derive(Debug, Deserialize)]
struct SummaryConfig {
    #[serde(default = "default_l1_trigger")]
    level_1_trigger_rounds: u64,
}

impl Default for SummaryConfig {
    fn default() -> Self {
        Self {
            level_1_trigger_rounds: default_l1_trigger(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct BackupConfig {
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    directory: String,
    #[serde(default = "default_backup_retention")]
    retention_count: usize,
}

impl Default for BackupConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            directory: String::new(),
            retention_count: default_backup_retention(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct SafetyConfig {
    #[serde(default = "default_true")]
    redact_secrets: bool,
}

impl Default for SafetyConfig {
    fn default() -> Self {
        Self {
            redact_secrets: true,
        }
    }
}

fn default_l1_trigger() -> u64 {
    20
}
fn default_true() -> bool {
    true
}
fn default_backup_retention() -> usize {
    1
}

#[derive(Debug, Default)]
struct FileSyncResult {
    session_id: String,
    source_path: PathBuf,
    last_line: u64,
    visible_events: u64,
    imported_messages: u64,
    duplicate_messages: u64,
    repaired_transcripts: u64,
    excluded_reason: Option<String>,
}

#[derive(Debug, Default)]
struct AppendResult {
    appended: bool,
    transcript_repaired: bool,
}

struct Store {
    root: PathBuf,
    config: Config,
    message_cache: RefCell<Option<HashMap<String, Value>>>,
}

impl Store {
    fn new(root: PathBuf, config_path: &Path) -> Result<Self> {
        let config_text = fs::read_to_string(config_path)
            .with_context(|| format!("read config {}", config_path.display()))?;
        let config: Config = serde_yaml::from_str(&config_text).context("parse config")?;
        let root = expand_tilde(&root)?.canonicalize().or_else(|_| {
            fs::create_dir_all(&root)?;
            root.canonicalize()
        })?;
        let store = Self {
            root,
            config,
            message_cache: RefCell::new(None),
        };
        store.init()?;
        Ok(store)
    }

    fn init(&self) -> Result<()> {
        for relative in [
            "raw",
            "conversations",
            "summaries",
            "indexes",
            "indexes/by-conversation",
            "retrieval",
            "pending",
            "archive",
            ".locks",
            "imports/codex",
        ] {
            fs::create_dir_all(self.root.join(relative))?;
        }
        Ok(())
    }

    fn lock<T>(&self, name: &str, operation: impl FnOnce() -> Result<T>) -> Result<T> {
        let path = self.root.join(".locks").join(name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&path)?;
        FileExt::lock_exclusive(&file)?;
        let result = operation();
        FileExt::unlock(&file)?;
        result
    }

    fn state_path(&self) -> PathBuf {
        self.root.join("state.json")
    }

    fn load_state(&self) -> Result<Value> {
        read_json(&self.state_path())
    }

    fn save_state(&self, state: &mut Value) -> Result<()> {
        state["last_successful_memory_update"] = json!(now_iso());
        atomic_write_json(&self.state_path(), state)
    }

    fn relative(&self, path: &Path) -> Result<String> {
        Ok(path
            .canonicalize()?
            .strip_prefix(&self.root)?
            .to_string_lossy()
            .into_owned())
    }

    fn raw_path(&self, timestamp: &str) -> Result<PathBuf> {
        let parsed = DateTime::parse_from_rfc3339(timestamp)
            .with_context(|| format!("invalid timestamp {timestamp}"))?;
        let date = parsed.date_naive();
        Ok(self
            .root
            .join("raw")
            .join(format!("{:04}", date.format("%Y")))
            .join(format!("{:02}", date.format("%m")))
            .join(format!("{}.md", date.format("%Y-%m-%d"))))
    }

    fn ensure_raw_header(&self, path: &Path, timestamp: &str) -> Result<()> {
        if path.exists() {
            return Ok(());
        }
        let parsed = DateTime::parse_from_rfc3339(timestamp)?;
        let date = parsed.date_naive();
        let header = format!(
            "---\nrecord_type: raw_conversation\ndate: \"{}\"\ntimezone: \"{}\"\nformat_version: 1\n---\n\n# Raw Conversation {}\n\n",
            date,
            parsed.offset(),
            date
        );
        atomic_write(path, header.as_bytes())
    }

    fn conversation_path(&self, conversation_id: &str) -> PathBuf {
        if let Some(session) = conversation_id.strip_prefix("codex:")
            && session
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-')
        {
            return self
                .root
                .join("conversations")
                .join(format!("codex-{session}.md"));
        }
        let digest = sha256_hex(conversation_id.as_bytes());
        self.root
            .join("conversations")
            .join(format!("conversation-{}.md", &digest[..16]))
    }

    fn conversation_index_dir(&self, conversation_id: &str) -> Result<PathBuf> {
        let transcript = self.conversation_path(conversation_id);
        let stem = transcript
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or_else(|| anyhow!("conversation transcript has no valid stem"))?;
        Ok(self.root.join("indexes/by-conversation").join(stem))
    }

    fn ensure_conversation_indexes(&self, conversation_id: &str) -> Result<PathBuf> {
        let directory = self.conversation_index_dir(conversation_id)?;
        fs::create_dir_all(&directory)?;
        let files = [
            ("messages.jsonl", String::new()),
            ("summaries.jsonl", String::new()),
            ("concepts.jsonl", String::new()),
            (
                "timeline.md",
                format!("# Conversation Timeline\n\n- Conversation ID: `{conversation_id}`\n"),
            ),
            (
                "summary-timeline.md",
                format!(
                    "# Conversation Summary Timeline\n\n- Conversation ID: `{conversation_id}`\n"
                ),
            ),
            (
                "concepts.md",
                format!("# Conversation Concept Index\n\n- Conversation ID: `{conversation_id}`\n"),
            ),
        ];
        for (name, content) in files {
            let path = directory.join(name);
            if !path.exists() {
                atomic_write(&path, content.as_bytes())?;
            }
        }
        Ok(directory)
    }

    fn append_conversation_message_index(&self, index: &Value) -> Result<()> {
        let conversation_id = string_field(index, "conversation_id")?;
        let directory = self.ensure_conversation_indexes(conversation_id)?;
        append_jsonl(&directory.join("messages.jsonl"), index)?;
        let phase = index
            .get("source")
            .and_then(|source| source.get("phase"))
            .and_then(Value::as_str)
            .or_else(|| index.get("speaker").and_then(Value::as_str))
            .unwrap_or("message");
        append_bytes(
            &directory.join("timeline.md"),
            format!(
                "\n- `{}` | sequence `{}` | `{phase}` | round `{}` | `{}`\n",
                string_field(index, "timestamp")?,
                u64_field(index, "sequence")?,
                index
                    .get("round_number")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
                string_field(index, "message_id")?,
            )
            .as_bytes(),
        )
    }

    fn conversation_header(&self, conversation_id: &str) -> String {
        format!(
            "---\nrecord_type: conversation_transcript\nconversation_id: {}\nformat_version: 1\n---\n\n# Conversation {}\n\nThis file contains user messages and user-visible assistant text only. The fenced JSON record preserves the exact stored text and source metadata.\n\n",
            serde_json::to_string(conversation_id).unwrap(),
            conversation_id
        )
    }

    fn transcript_block(&self, record: &Value) -> Result<String> {
        let source = record.get("source").and_then(Value::as_object);
        let phase = source
            .and_then(|value| value.get("phase"))
            .and_then(Value::as_str);
        let phase_label = phase.map(|value| format!(" / {value}")).unwrap_or_default();
        Ok(format!(
            "{RAW_MARKER}\n```json\n{}\n```\n\n## {}{}\n\n- Timestamp: `{}`\n- Message ID: `{}`\n\n{}\n\n",
            compact_json(record)?,
            string_field(record, "speaker")?,
            phase_label,
            string_field(record, "timestamp")?,
            string_field(record, "message_id")?,
            string_field(record, "text")?,
        ))
    }

    fn append_transcript(&self, record: &Value) -> Result<PathBuf> {
        let conversation_id = string_field(record, "conversation_id")?;
        let path = self.conversation_path(conversation_id);
        let lock_name = format!(
            "conversation-{}.lock",
            &sha256_hex(conversation_id.as_bytes())[..16]
        );
        self.lock(&lock_name, || {
            if !path.exists() {
                atomic_write(&path, self.conversation_header(conversation_id).as_bytes())?;
            }
            append_bytes(&path, self.transcript_block(record)?.as_bytes())
        })?;
        Ok(path)
    }

    fn recover_pending_rounds(
        &self,
        records: &[Value],
        completed_rounds: u64,
    ) -> Result<Map<String, Value>> {
        let mut pending = Map::new();
        for record in records {
            let number = record
                .get("round_number")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            if number <= completed_rounds {
                continue;
            }
            let conversation_id = string_field(record, "conversation_id")?;
            let speaker = string_field(record, "speaker")?;
            if speaker == "user" {
                let message_id = string_field(record, "message_id")?;
                let existing = pending.get(conversation_id);
                let first = existing
                    .filter(|value| value.get("number").and_then(Value::as_u64) == Some(number))
                    .and_then(|value| value.get("first_user_message_id"))
                    .cloned()
                    .unwrap_or_else(|| json!(message_id));
                pending.insert(
                    conversation_id.to_owned(),
                    json!({
                        "number": number,
                        "first_user_message_id": first,
                        "latest_user_message_id": message_id,
                    }),
                );
            } else if speaker == "assistant"
                && record
                    .get("completes_round")
                    .and_then(Value::as_bool)
                    .unwrap_or(true)
                && pending
                    .get(conversation_id)
                    .and_then(|value| value.get("number"))
                    .and_then(Value::as_u64)
                    == Some(number)
            {
                pending.remove(conversation_id);
            }
        }
        Ok(pending)
    }

    fn normalize_round_state(&self, state: &mut Value, records: &[Value]) -> Result<()> {
        let completed_rounds = u64_field(state, "completed_rounds")?;
        if !state.get("pending_rounds").is_some_and(Value::is_object) {
            state["pending_rounds"] =
                Value::Object(self.recover_pending_rounds(records, completed_rounds)?);
        }
        let max_pending_round = state
            .get("pending_rounds")
            .and_then(Value::as_object)
            .into_iter()
            .flat_map(|rounds| rounds.values())
            .filter_map(|round| round.get("number").and_then(Value::as_u64))
            .max()
            .unwrap_or(0);
        let max_out_of_order = state
            .get("completed_rounds_out_of_order")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_u64)
            .max()
            .unwrap_or(0);
        let configured_next = state
            .get("next_round_number")
            .and_then(Value::as_u64)
            .unwrap_or(1);
        state["next_round_number"] = json!(
            configured_next.max(
                completed_rounds
                    .max(max_pending_round)
                    .max(max_out_of_order)
                    + 1
            )
        );
        if !state
            .get("completed_rounds_out_of_order")
            .is_some_and(Value::is_array)
        {
            state["completed_rounds_out_of_order"] = json!([]);
        }
        state["pending_round"] = Value::Null;
        Ok(())
    }

    fn append_message(
        &self,
        speaker: &str,
        text: &str,
        timestamp: &str,
        conversation_id: &str,
        message_id: &str,
        complete_round: bool,
        source: Value,
    ) -> Result<AppendResult> {
        self.lock("state.lock", || {
            let mut state = self.load_state()?;
            let recovery_records = if state.get("pending_rounds").is_some_and(Value::is_object) {
                Vec::new()
            } else {
                self.read_all_raw()?
            };
            self.normalize_round_state(&mut state, &recovery_records)?;
            let stored_text = if self.config.safety.redact_secrets {
                redact_secrets(text)
            } else {
                text.to_owned()
            };
            if let Some(existing) = self.cached_message(message_id)? {
                let same = string_field(&existing, "speaker")? == speaker
                    && string_field(&existing, "text")? == stored_text
                    && string_field(&existing, "conversation_id")? == conversation_id
                    && string_field(&existing, "timestamp")? == timestamp
                    && existing.get("source") == Some(&source);
                if !same {
                    bail!("message ID already exists with different content: {message_id}");
                }
                let transcript = self.conversation_path(conversation_id);
                let has_message = self.read_records(&transcript)?.iter().any(|record| {
                    record.get("message_id").and_then(Value::as_str) == Some(message_id)
                });
                if !has_message {
                    self.append_transcript(&existing)?;
                }
                return Ok(AppendResult {
                    appended: false,
                    transcript_repaired: !has_message,
                });
            }

            let sequence = u64_field(&state, "total_messages")? + 1;
            let completed_rounds = u64_field(&state, "completed_rounds")?;
            let mut pending_rounds = state
                .get("pending_rounds")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let mut pending = pending_rounds.get(conversation_id).cloned();
            let round_number = if speaker == "user" {
                if pending.is_none() {
                    let number = u64_field(&state, "next_round_number")?;
                    pending = Some(json!({
                        "number": number,
                        "first_user_message_id": Value::Null,
                        "latest_user_message_id": Value::Null,
                    }));
                    state["next_round_number"] = json!(number + 1);
                }
                pending
                    .as_ref()
                    .and_then(|value| value.get("number"))
                    .and_then(Value::as_u64)
                    .ok_or_else(|| anyhow!("pending round number is missing"))?
            } else if let Some(value) = pending.as_ref() {
                u64_field(value, "number")?
            } else {
                0
            };
            let reply_to = if speaker == "assistant" {
                pending
                    .as_ref()
                    .and_then(|value| value.get("latest_user_message_id"))
                    .cloned()
                    .unwrap_or(Value::Null)
            } else {
                Value::Null
            };
            let redacted = stored_text != text;
            let mut record = json!({
                "record_type": "raw_message",
                "sequence": sequence,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "timestamp": timestamp,
                "speaker": speaker,
                "round_number": round_number,
                "round_scope": "conversation",
                "reply_to": reply_to,
                "text": stored_text,
                "redacted": redacted,
                "completes_round": speaker == "assistant" && complete_round && pending.is_some(),
                "source": source,
            });
            let digest = raw_record_sha256(&record)?;
            record["content_sha256"] = json!(digest);

            let raw_path = self.raw_path(timestamp)?;
            let raw_lock = format!(
                "raw-{}.lock",
                raw_path.file_stem().unwrap().to_string_lossy()
            );
            self.lock(&raw_lock, || {
                self.ensure_raw_header(&raw_path, timestamp)?;
                append_bytes(
                    &raw_path,
                    format!("{RAW_MARKER}\n```json\n{}\n```\n\n", compact_json(&record)?)
                        .as_bytes(),
                )
            })?;
            let transcript_path = self.append_transcript(&record)?;

            let mut index = record.clone();
            index.as_object_mut().unwrap().remove("text");
            index["path"] = json!(self.relative(&raw_path)?);
            index["conversation_path"] = json!(self.relative(&transcript_path)?);
            append_jsonl(&self.root.join("indexes/conversations.jsonl"), &index)?;
            self.append_conversation_message_index(&index)?;

            state["total_messages"] = json!(sequence);
            state["last_raw_message_id"] = json!(message_id);
            if speaker == "user" {
                let first = pending
                    .as_ref()
                    .and_then(|value| value.get("first_user_message_id"))
                    .filter(|value| !value.is_null())
                    .cloned()
                    .unwrap_or_else(|| json!(message_id));
                pending_rounds.insert(
                    conversation_id.to_owned(),
                    json!({
                        "number": round_number,
                        "first_user_message_id": first,
                        "latest_user_message_id": message_id,
                    }),
                );
            } else if speaker == "assistant" && pending.is_some() && complete_round {
                let mut completed = completed_rounds;
                let mut out_of_order: BTreeSet<u64> = state
                    .get("completed_rounds_out_of_order")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_u64)
                    .filter(|number| *number > completed)
                    .collect();
                if round_number == completed + 1 {
                    completed = round_number;
                    while out_of_order.remove(&(completed + 1)) {
                        completed += 1;
                    }
                } else if round_number > completed + 1 {
                    out_of_order.insert(round_number);
                }
                state["completed_rounds"] = json!(completed);
                state["completed_rounds_out_of_order"] = json!(out_of_order);
                pending_rounds.remove(conversation_id);
            }
            state["pending_rounds"] = Value::Object(pending_rounds);
            self.save_state(&mut state)?;
            self.cache_message(&record)?;
            Ok(AppendResult {
                appended: true,
                transcript_repaired: false,
            })
        })
    }

    fn read_records(&self, path: &Path) -> Result<Vec<Value>> {
        if !path.exists() {
            return Ok(Vec::new());
        }
        let text = fs::read_to_string(path)?;
        let lines: Vec<&str> = text.lines().collect();
        let mut records = Vec::new();
        for index in 0..lines.len().saturating_sub(2) {
            if lines[index] == RAW_MARKER && lines[index + 1] == "```json" {
                records.push(serde_json::from_str(lines[index + 2]).with_context(|| {
                    format!("invalid raw record {}:{}", path.display(), index + 3)
                })?);
            }
        }
        Ok(records)
    }

    fn read_all_raw(&self) -> Result<Vec<Value>> {
        let mut records = Vec::new();
        let raw_root = self.root.join("raw");
        if !raw_root.exists() {
            return Ok(records);
        }
        for entry in WalkDir::new(&raw_root).sort_by_file_name() {
            let entry = entry?;
            if !entry.file_type().is_file()
                || entry.path().extension().and_then(|v| v.to_str()) != Some("md")
            {
                continue;
            }
            let relative = entry
                .path()
                .strip_prefix(&self.root)?
                .to_string_lossy()
                .into_owned();
            for mut record in self.read_records(entry.path())? {
                record["_path"] = json!(relative);
                records.push(record);
            }
        }
        records.sort_by_key(|record| record.get("sequence").and_then(Value::as_u64).unwrap_or(0));
        Ok(records)
    }

    fn cached_message(&self, message_id: &str) -> Result<Option<Value>> {
        if self.message_cache.borrow().is_none() {
            let records = self.read_all_raw()?;
            let mut cache = HashMap::with_capacity(records.len());
            for record in records {
                cache.insert(string_field(&record, "message_id")?.to_owned(), record);
            }
            *self.message_cache.borrow_mut() = Some(cache);
        }
        Ok(self
            .message_cache
            .borrow()
            .as_ref()
            .and_then(|cache| cache.get(message_id))
            .cloned())
    }

    fn cache_message(&self, record: &Value) -> Result<()> {
        let message_id = string_field(record, "message_id")?.to_owned();
        if let Some(cache) = self.message_cache.borrow_mut().as_mut() {
            cache.insert(message_id, record.clone());
        }
        Ok(())
    }

    fn cursor_path(&self, session_id: &str) -> PathBuf {
        let safe: String = session_id
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || "._-".contains(c) {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        self.root.join("imports/codex").join(format!("{safe}.json"))
    }

    fn sync_file(&self, source_path: &Path) -> Result<FileSyncResult> {
        let source_path = source_path.canonicalize()?;
        let bytes = fs::read(&source_path)?;
        let text = String::from_utf8(bytes.clone()).context("Codex rollout is not UTF-8")?;
        let complete_text = if text.ends_with('\n') {
            text.as_str()
        } else {
            text.rsplit_once('\n')
                .map(|(complete, _)| complete)
                .unwrap_or("")
        };
        let lines: Vec<&str> = if complete_text.is_empty() {
            Vec::new()
        } else {
            complete_text.lines().collect()
        };
        let (session_id, is_subagent) = lines
            .iter()
            .find_map(|line| {
                let event: Value = serde_json::from_str(line).ok()?;
                if event.get("type").and_then(Value::as_str) != Some("session_meta") {
                    return None;
                }
                let payload = event.get("payload")?;
                let session_id = payload
                    .get("id")
                    .or_else(|| payload.get("session_id"))
                    .and_then(Value::as_str)
                    .map(str::to_owned)?;
                let is_subagent = payload
                    .get("source")
                    .and_then(Value::as_object)
                    .is_some_and(|source| source.contains_key("subagent"));
                Some((session_id, is_subagent))
            })
            .ok_or_else(|| {
                anyhow!(
                    "Codex session metadata is missing an ID: {}",
                    source_path.display()
                )
            })?;
        let cursor_path = self.cursor_path(&session_id);
        let cursor = if cursor_path.exists() {
            read_json(&cursor_path)?
        } else {
            json!({})
        };
        let last_line = cursor.get("last_line").and_then(Value::as_u64).unwrap_or(0);
        let total_lines = lines.len() as u64;
        if total_lines < last_line {
            bail!(
                "Codex session was truncated below its cursor: {} ({total_lines} < {last_line})",
                source_path.display()
            );
        }
        let mut result = FileSyncResult {
            session_id: session_id.clone(),
            source_path: source_path.clone(),
            last_line: total_lines,
            ..Default::default()
        };
        if is_subagent {
            let metadata = fs::metadata(&source_path)?;
            atomic_write_json(
                &cursor_path,
                &json!({
                    "format_version": 1,
                    "session_id": session_id,
                    "source_path": source_path.to_string_lossy(),
                    "last_line": total_lines,
                    "source_size": metadata.len(),
                    "excluded_reason": "subagent-session",
                    "updated_at": now_iso(),
                }),
            )?;
            result.excluded_reason = Some("subagent-session".to_owned());
            return Ok(result);
        }
        for (zero_index, line) in lines.iter().enumerate().skip(last_line as usize) {
            let line_number = zero_index as u64 + 1;
            let event: Value = serde_json::from_str(line).with_context(|| {
                format!(
                    "invalid Codex JSONL {}:{line_number}",
                    source_path.display()
                )
            })?;
            if event.get("type").and_then(Value::as_str) != Some("event_msg") {
                continue;
            }
            let payload = match event.get("payload") {
                Some(value) => value,
                None => continue,
            };
            let event_type = payload.get("type").and_then(Value::as_str);
            let incoming_phase = payload.get("phase").and_then(Value::as_str);
            let (speaker, phase, complete_round) = match (event_type, incoming_phase) {
                (Some("user_message"), _) => ("user", "user", false),
                (Some("agent_message"), Some("commentary")) => ("assistant", "commentary", false),
                (Some("agent_message"), Some("final_answer")) => {
                    ("assistant", "final_answer", true)
                }
                _ => continue,
            };
            let Some(message) = payload
                .get("message")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            else {
                continue;
            };
            result.visible_events += 1;
            let mut timestamp = event
                .get("timestamp")
                .and_then(Value::as_str)
                .map(str::to_owned)
                .unwrap_or_else(now_iso);
            if timestamp.ends_with('Z') {
                timestamp.truncate(timestamp.len() - 1);
                timestamp.push_str("+00:00");
            }
            DateTime::parse_from_rfc3339(&timestamp)?;
            let suffix = if speaker == "user" { "u" } else { "a" };
            let message_id = format!("codex-{session_id}-{line_number:08}-{suffix}");
            let append = self.append_message(
                speaker,
                message,
                &timestamp,
                &format!("codex:{session_id}"),
                &message_id,
                complete_round,
                json!({
                    "kind": "codex-rollout-jsonl",
                    "session_id": session_id,
                    "path": source_path.to_string_lossy(),
                    "line": line_number,
                    "phase": phase,
                }),
            )?;
            if append.appended {
                result.imported_messages += 1;
            } else {
                result.duplicate_messages += 1;
            }
            if append.transcript_repaired {
                result.repaired_transcripts += 1;
            }
        }
        if total_lines != last_line {
            let metadata = fs::metadata(&source_path)?;
            let modified: DateTime<Utc> = metadata.modified().unwrap_or(SystemTime::now()).into();
            atomic_write_json(
                &cursor_path,
                &json!({
                    "format_version": 1,
                    "session_id": session_id,
                    "source_path": source_path.to_string_lossy(),
                    "last_line": total_lines,
                    "source_size": metadata.len(),
                    "source_mtime": modified.to_rfc3339(),
                    "updated_at": now_iso(),
                }),
            )?;
        }
        Ok(result)
    }

    fn maybe_create_level_one_job(&self) -> Result<Option<PathBuf>> {
        self.lock("summary-jobs.lock", || self.lock("state.lock", || {
            let mut state = self.load_state()?;
            let raw_records = self.read_all_raw()?;
            let mut grouped: BTreeMap<String, BTreeMap<u64, Vec<Value>>> = BTreeMap::new();
            for record in raw_records {
                let round_number = record
                    .get("round_number")
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                if round_number == 0 {
                    continue;
                }
                let conversation_id = string_field(&record, "conversation_id")?.to_owned();
                grouped
                    .entry(conversation_id)
                    .or_default()
                    .entry(round_number)
                    .or_default()
                    .push(record);
            }
            let mut completed_by_conversation = Vec::new();
            for (conversation_id, rounds) in grouped {
                let mut completed = Vec::new();
                for (_, mut records) in rounds {
                    records.sort_by_key(|record| {
                        record.get("sequence").and_then(Value::as_u64).unwrap_or(0)
                    });
                    let has_user = records.iter().any(|record| {
                        record.get("speaker").and_then(Value::as_str) == Some("user")
                    });
                    let has_final = records.iter().any(|record| {
                        record.get("speaker").and_then(Value::as_str) == Some("assistant")
                            && record
                                .get("completes_round")
                                .and_then(Value::as_bool)
                                .unwrap_or(true)
                    });
                    if has_user && has_final {
                        completed.push(records);
                    }
                }
                completed.sort_by_key(|records| {
                    records[0]
                        .get("sequence")
                        .and_then(Value::as_u64)
                        .unwrap_or(0)
                });
                if let Some(first) = completed.first() {
                    let first_sequence = first[0]
                        .get("sequence")
                        .and_then(Value::as_u64)
                        .unwrap_or(0);
                    completed_by_conversation.push((first_sequence, conversation_id, completed));
                }
            }
            completed_by_conversation.sort_by_key(|entry| entry.0);

            let mut assigned: BTreeMap<String, u64> = state
                .get("last_summarized_rounds")
                .and_then(Value::as_object)
                .into_iter()
                .flat_map(|rounds| rounds.iter())
                .filter_map(|(conversation_id, value)| {
                    value
                        .as_u64()
                        .map(|round| (conversation_id.to_owned(), round))
                })
                .collect();
            for entry in fs::read_dir(self.root.join("pending"))? {
                let path = entry?.path();
                if path.file_name().and_then(|v| v.to_str()).is_some_and(|name| name.starts_with("job-") && name.ends_with(".json")) {
                    let job = read_json(&path)?;
                    if job.get("summary_level").and_then(Value::as_u64) == Some(1)
                        && let (Some(conversation_id), Some(end_round)) = (
                            job.get("conversation_id").and_then(Value::as_str),
                            job.get("source_round_end").and_then(Value::as_u64),
                        )
                    {
                        let current = assigned.entry(conversation_id.to_owned()).or_default();
                        *current = (*current).max(end_round);
                    }
                }
            }
            let mut selected = None;
            for (_, conversation_id, completed_rounds) in completed_by_conversation {
                let last_assigned_round = assigned.get(&conversation_id).copied().unwrap_or(0);
                let eligible_rounds: Vec<&Vec<Value>> = completed_rounds
                    .iter()
                    .filter(|records| {
                        records[0]
                            .get("round_number")
                            .and_then(Value::as_u64)
                            .unwrap_or(0)
                            > last_assigned_round
                    })
                    .collect();
                if eligible_rounds.len() < self.config.summaries.level_1_trigger_rounds as usize {
                    continue;
                }
                let selected_rounds =
                    &eligible_rounds[..self.config.summaries.level_1_trigger_rounds as usize];
                let start_round = selected_rounds
                    .first()
                    .and_then(|records| records[0].get("round_number"))
                    .and_then(Value::as_u64)
                    .ok_or_else(|| anyhow!("selected Level-1 start round is missing"))?;
                let end_round = selected_rounds
                    .last()
                    .and_then(|records| records[0].get("round_number"))
                    .and_then(Value::as_u64)
                    .ok_or_else(|| anyhow!("selected Level-1 end round is missing"))?;
                let mut records: Vec<Value> = selected_rounds
                    .iter()
                    .copied()
                    .flatten()
                    .cloned()
                    .collect();
                records.sort_by_key(|record| {
                    record.get("sequence").and_then(Value::as_u64).unwrap_or(0)
                });
                selected = Some((conversation_id, start_round, end_round, records));
                break;
            }
            let Some((conversation_id, start_round, end_round, records)) = selected else {
                return Ok(None);
            };
            let signature = format!(
                "conversation:{conversation_id}:rounds:{start_round}-{end_round}"
            );
            let job_number = u64_field(&state, "next_job_id")?;
            let summary_number = state.get("next_summary_ids").and_then(|v| v.get("1")).and_then(Value::as_u64)
                .ok_or_else(|| anyhow!("state.next_summary_ids.1 is missing"))?;
            let mut seen_files = HashSet::new();
            let source_files: Vec<String> = records
                .iter()
                .filter_map(|value| {
                    value
                        .get("_path")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .filter(|path| seen_files.insert(path.clone()))
                .collect();
            let source_records: Vec<Value> = records.iter().map(|value| {
                let mut cloned = value.clone(); cloned.as_object_mut().unwrap().remove("_path"); cloned
            }).collect();
            let source_message_ids: Vec<Value> = records
                .iter()
                .map(|record| record["message_id"].clone())
                .collect();
            let mut timestamped: Vec<(DateTime<FixedOffset>, String)> = records
                .iter()
                .map(|record| {
                    let value = string_field(record, "timestamp")?.to_owned();
                    Ok((DateTime::parse_from_rfc3339(&value)?, value))
                })
                .collect::<Result<Vec<_>>>()?;
            timestamped.sort_by_key(|entry| entry.0);
            let job = json!({
                "format_version": 1,
                "job_id": format!("job-{job_number:06}"),
                "target_summary_id": format!("L1-{summary_number:06}"),
                "summary_level": 1,
                "conversation_id": conversation_id,
                "created_at": now_iso(),
                "source_signature": signature,
                "source_round_start": start_round,
                "source_round_end": end_round,
                "source_start": records.first().unwrap()["message_id"],
                "source_end": records.last().unwrap()["message_id"],
                "source_start_sequence": records.first().unwrap()["sequence"],
                "source_end_sequence": records.last().unwrap()["sequence"],
                "start_time": timestamped.first().unwrap().1,
                "end_time": timestamped.last().unwrap().1,
                "source_files": source_files,
                "source_message_ids": source_message_ids,
                "source_sha256": raw_source_sha256(&records)?,
                "source_records": source_records,
                "required_result_keys": ["topics", "established_conclusions", "open_questions", "concepts"],
            });
            let path = self.root.join("pending").join(format!("job-{job_number:06}.json"));
            atomic_write_json(&path, &job)?;
            state["next_job_id"] = json!(job_number + 1);
            state["next_summary_ids"]["1"] = json!(summary_number + 1);
            self.save_state(&mut state)?;
            self.refresh_unsummarized()?;
            Ok(Some(path))
        }))
    }

    fn refresh_unsummarized(&self) -> Result<()> {
        let mut jobs = Vec::new();
        for entry in fs::read_dir(self.root.join("pending"))? {
            let path = entry?.path();
            if !path
                .file_name()
                .and_then(|v| v.to_str())
                .is_some_and(|name| name.starts_with("job-") && name.ends_with(".json"))
            {
                continue;
            }
            let mut job = read_json(&path)?;
            if let Some(map) = job.as_object_mut() {
                map.remove("source_records");
                map.remove("source_summary_payload");
            }
            jobs.push(job);
        }
        jobs.sort_by_key(|job| {
            job.get("job_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned()
        });
        atomic_write_json(
            &self.root.join("pending/unsummarized.json"),
            &json!({"format_version": 1, "pending_jobs": jobs}),
        )
    }

    fn prune_backup_snapshots(&self, backup_root: &Path) -> Result<Vec<String>> {
        if self.config.backup.retention_count == 0 {
            bail!("backup.retention_count must be at least 1");
        }
        let pattern = Regex::new(r"^\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2}(?:_\d{6})?)?$")?;
        let mut snapshots = Vec::new();
        for entry in fs::read_dir(backup_root)? {
            let path = entry?.path();
            if path.is_dir()
                && path
                    .file_name()
                    .and_then(|value| value.to_str())
                    .is_some_and(|name| pattern.is_match(name))
            {
                snapshots.push(path);
            }
        }
        snapshots.sort();
        let remove_count = snapshots
            .len()
            .saturating_sub(self.config.backup.retention_count);
        let mut removed = Vec::new();
        for path in snapshots.into_iter().take(remove_count) {
            fs::remove_dir_all(&path)?;
            removed.push(
                path.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned(),
            );
        }
        Ok(removed)
    }

    fn create_backup(&self, reason: &str, metadata: Value) -> Result<Option<PathBuf>> {
        if !self.config.backup.enabled {
            return Ok(None);
        }
        if self.config.backup.directory.trim().is_empty() {
            bail!("backup.enabled requires backup.directory");
        }
        let backup_root = expand_tilde(Path::new(&self.config.backup.directory))?;
        if backup_root.starts_with(&self.root) {
            bail!("backup directory must be outside the archive root");
        }
        fs::create_dir_all(&backup_root)?;
        self.lock("desktop-backup.lock", || {
            let stamp = Local::now().format("%Y-%m-%d_%H%M%S_%6f").to_string();
            let final_path = backup_root.join(&stamp);
            let temporary = backup_root.join(format!(".{stamp}.tmp-{}", std::process::id()));
            if final_path.exists() || temporary.exists() {
                bail!(
                    "backup destination already exists: {}",
                    final_path.display()
                );
            }
            fs::create_dir_all(&temporary)?;
            let mut copied_files = Vec::new();
            for entry in WalkDir::new(&self.root).sort_by_file_name() {
                let entry = entry?;
                let relative = entry.path().strip_prefix(&self.root)?;
                if relative
                    .components()
                    .next()
                    .is_some_and(|value| value.as_os_str() == ".locks")
                {
                    continue;
                }
                if entry.file_name() == ".DS_Store" {
                    continue;
                }
                let destination = temporary.join(relative);
                if entry.file_type().is_dir() {
                    fs::create_dir_all(&destination)?;
                    continue;
                }
                if entry.file_type().is_file() {
                    if let Some(parent) = destination.parent() {
                        fs::create_dir_all(parent)?;
                    }
                    fs::copy(entry.path(), &destination)?;
                    copied_files.push(json!({
                        "path": relative.to_string_lossy(),
                        "sha256": file_sha256(&destination)?,
                        "bytes": fs::metadata(&destination)?.len(),
                    }));
                }
            }
            let state = self.load_state()?;
            let created_at = now_iso();
            atomic_write_json(
                &temporary.join("backup-manifest.json"),
                &json!({
                    "format_version": 1,
                    "created_at": created_at,
                    "source_root": self.root.to_string_lossy(),
                    "reason": reason,
                    "metadata": metadata,
                    "state": state,
                    "files": copied_files,
                }),
            )?;
            fs::rename(&temporary, &final_path)?;
            append_jsonl(
                &backup_root.join("backup-log.jsonl"),
                &json!({
                    "created_at": created_at,
                    "snapshot": stamp,
                    "reason": reason,
                    "source_root": self.root.to_string_lossy(),
                    "file_count": copied_files.len(),
                    "total_messages": state["total_messages"],
                    "completed_rounds": state["completed_rounds"],
                    "metadata": metadata,
                }),
            )?;
            self.prune_backup_snapshots(&backup_root)?;
            Ok(Some(final_path))
        })
    }

    fn sync_batch(&self, paths: Vec<PathBuf>) -> Result<Value> {
        self.lock("archive.lock", || self.sync_batch_unlocked(paths))
    }

    fn sync_batch_unlocked(&self, paths: Vec<PathBuf>) -> Result<Value> {
        let mut files = Vec::new();
        let mut imported = 0;
        let mut duplicates = 0;
        let mut repaired = 0;
        for path in paths {
            let result = self.sync_file(&path)?;
            imported += result.imported_messages;
            duplicates += result.duplicate_messages;
            repaired += result.repaired_transcripts;
            files.push(json!({
                "session_id": result.session_id,
                "source_path": result.source_path.to_string_lossy(),
                "last_line": result.last_line,
                "visible_events": result.visible_events,
                "imported_messages": result.imported_messages,
                "duplicate_messages": result.duplicate_messages,
                "repaired_transcripts": result.repaired_transcripts,
                "excluded_reason": result.excluded_reason,
            }));
        }
        let created_job = if imported > 0 {
            self.maybe_create_level_one_job()?
        } else {
            None
        };
        let mutation = imported > 0 || repaired > 0;
        let metadata = json!({
            "session_count": files.len(), "imported_messages": imported,
            "duplicate_messages": duplicates, "repaired_transcripts": repaired,
            "created_summary_job": created_job.as_ref().map(|path| path.to_string_lossy()),
        });
        let backup = if mutation {
            self.create_backup("codex-native-sync", metadata.clone())?
        } else {
            None
        };
        Ok(json!({
            "status": "synced", "sessions": files, "session_count": files.len(),
            "imported_messages": imported, "duplicate_messages": duplicates,
            "repaired_transcripts": repaired,
            "created_summary_job": created_job.map(|path| path.to_string_lossy().into_owned()),
            "backup": backup.map(|path| path.to_string_lossy().into_owned()),
        }))
    }
}

fn now_iso() -> String {
    Local::now().to_rfc3339_opts(SecondsFormat::Secs, false)
}

fn expand_tilde(path: &Path) -> Result<PathBuf> {
    let text = path.to_string_lossy();
    if text == "~" || text.starts_with("~/") {
        let home = std::env::var_os("HOME").ok_or_else(|| anyhow!("HOME is not set"))?;
        return Ok(PathBuf::from(home).join(text.strip_prefix("~/").unwrap_or("")));
    }
    Ok(path.to_path_buf())
}

fn string_field<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("missing string field {key}"))
}

fn u64_field(value: &Value, key: &str) -> Result<u64> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| anyhow!("missing integer field {key}"))
}

fn compact_json(value: &Value) -> Result<String> {
    Ok(serde_json::to_string(value)?)
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn canonical_sha256(value: &Value) -> Result<String> {
    Ok(sha256_hex(&serde_json::to_vec(value)?))
}

fn raw_record_sha256(record: &Value) -> Result<String> {
    let mut payload = record.clone();
    let map = payload
        .as_object_mut()
        .ok_or_else(|| anyhow!("raw record is not an object"))?;
    map.remove("_path");
    map.remove("content_sha256");
    canonical_sha256(&payload)
}

fn raw_source_sha256(records: &[Value]) -> Result<String> {
    let mut ordered = records.to_vec();
    ordered.sort_by_key(|record| record.get("sequence").and_then(Value::as_u64).unwrap_or(0));
    let payload: Result<Vec<Value>> = ordered
        .iter()
        .map(|record| {
            Ok(json!({
                "sequence": u64_field(record, "sequence")?,
                "message_id": string_field(record, "message_id")?,
                "content_sha256": raw_record_sha256(record)?,
            }))
        })
        .collect();
    canonical_sha256(&Value::Array(payload?))
}

fn file_sha256(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
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

fn atomic_write_json(path: &Path, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    atomic_write(path, &bytes)
}

fn append_bytes(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_data()?;
    Ok(())
}

fn append_jsonl(path: &Path, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    append_bytes(path, &bytes)
}

fn read_json(path: &Path) -> Result<Value> {
    Ok(serde_json::from_slice(
        &fs::read(path).with_context(|| format!("read {}", path.display()))?,
    )?)
}

fn redact_secrets(text: &str) -> String {
    let patterns = [
        (r"(?i)(password\s*[:=]\s*)(\S+)", "$1[REDACTED]"),
        (r"(?i)(authorization\s*:\s*bearer\s+)(\S+)", "$1[REDACTED]"),
        (r"\b(sk-[A-Za-z0-9_-]{12,})\b", "[REDACTED]"),
        (r"\b(AKI[A-Z0-9]{13,})\b", "[REDACTED]"),
    ];
    patterns
        .into_iter()
        .fold(text.to_owned(), |current, (pattern, replacement)| {
            Regex::new(pattern)
                .unwrap()
                .replace_all(&current, replacement)
                .into_owned()
        })
}

fn recent_rollouts(root: &Path, since: Option<DateTime<FixedOffset>>) -> Result<Vec<PathBuf>> {
    let mut paths = Vec::new();
    for entry in WalkDir::new(root).follow_links(false) {
        let entry = entry?;
        if !entry.file_type().is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy();
        if !name.starts_with("rollout-")
            || entry.path().extension().and_then(|v| v.to_str()) != Some("jsonl")
        {
            continue;
        }
        if let Some(since) = since {
            let modified: DateTime<Utc> = entry.metadata()?.modified()?.into();
            if modified.timestamp() < since.timestamp() {
                continue;
            }
        }
        paths.push(entry.path().canonicalize()?);
    }
    paths.sort();
    paths.dedup();
    Ok(paths)
}

#[cfg(not(target_os = "macos"))]
fn event_rollouts(
    event: Event,
    sessions_root: &Path,
    since: Option<DateTime<FixedOffset>>,
) -> Result<Vec<PathBuf>> {
    let mut paths = Vec::new();
    let mut needs_scan = false;
    for path in event.paths {
        if path.is_file()
            && path
                .file_name()
                .and_then(|v| v.to_str())
                .is_some_and(|name| name.starts_with("rollout-") && name.ends_with(".jsonl"))
        {
            paths.push(path.canonicalize()?);
        } else {
            needs_scan = true;
        }
    }
    if needs_scan || paths.is_empty() {
        paths.extend(recent_rollouts(sessions_root, since)?);
    }
    paths.sort();
    paths.dedup();
    Ok(paths)
}

fn emit(value: &Value) -> Result<()> {
    println!("{}", serde_json::to_string(value)?);
    Ok(())
}

fn sync_and_emit(store: &Store, paths: Vec<PathBuf>) {
    match store.sync_batch(paths) {
        Ok(result) => {
            let changed = result
                .get("imported_messages")
                .and_then(Value::as_u64)
                .unwrap_or(0)
                > 0
                || result
                    .get("repaired_transcripts")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    > 0;
            if changed && let Err(error) = emit(&result) {
                eprintln!("output error: {error:#}");
            }
        }
        Err(error) => eprintln!("sync error: {error:#}"),
    }
}

#[cfg(target_os = "macos")]
struct KqueueWatcher {
    queue: File,
    _watched: Vec<File>,
}

#[cfg(target_os = "macos")]
impl KqueueWatcher {
    fn new(root: &Path, since: Option<DateTime<FixedOffset>>) -> Result<Self> {
        let queue_fd = unsafe { libc::kqueue() };
        if queue_fd < 0 {
            return Err(std::io::Error::last_os_error()).context("create kqueue");
        }
        let queue = unsafe { File::from_raw_fd(queue_fd) };
        let mut watched = Vec::new();
        for entry in WalkDir::new(root).follow_links(false) {
            let entry = entry?;
            let should_watch = if entry.file_type().is_dir() {
                true
            } else if entry.file_type().is_file()
                && entry
                    .file_name()
                    .to_str()
                    .is_some_and(|name| name.starts_with("rollout-") && name.ends_with(".jsonl"))
            {
                if let Some(since) = since {
                    let modified: DateTime<Utc> = entry.metadata()?.modified()?.into();
                    modified.timestamp() >= since.timestamp()
                } else {
                    true
                }
            } else {
                false
            };
            if !should_watch {
                continue;
            }
            let path = CString::new(entry.path().as_os_str().as_bytes())?;
            let fd = unsafe { libc::open(path.as_ptr(), libc::O_EVTONLY | libc::O_CLOEXEC) };
            if fd < 0 {
                return Err(std::io::Error::last_os_error())
                    .with_context(|| format!("watch {}", entry.path().display()));
            }
            let file = unsafe { File::from_raw_fd(fd) };
            let change = libc::kevent {
                ident: file.as_raw_fd() as usize,
                filter: libc::EVFILT_VNODE,
                flags: libc::EV_ADD | libc::EV_ENABLE | libc::EV_CLEAR,
                fflags: libc::NOTE_WRITE
                    | libc::NOTE_EXTEND
                    | libc::NOTE_ATTRIB
                    | libc::NOTE_RENAME
                    | libc::NOTE_DELETE,
                data: 0,
                udata: std::ptr::null_mut(),
            };
            let result = unsafe {
                libc::kevent(
                    queue.as_raw_fd(),
                    &change,
                    1,
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null(),
                )
            };
            if result < 0 {
                return Err(std::io::Error::last_os_error())
                    .with_context(|| format!("register {}", entry.path().display()));
            }
            watched.push(file);
        }
        if watched.is_empty() {
            bail!("no session directories or rollout files could be watched");
        }
        Ok(Self {
            queue,
            _watched: watched,
        })
    }

    fn wait(&self) -> Result<()> {
        loop {
            let mut event: libc::kevent = unsafe { std::mem::zeroed() };
            let result = unsafe {
                libc::kevent(
                    self.queue.as_raw_fd(),
                    std::ptr::null(),
                    0,
                    &mut event,
                    1,
                    std::ptr::null(),
                )
            };
            if result > 0 {
                return Ok(());
            }
            let error = std::io::Error::last_os_error();
            if error.kind() != std::io::ErrorKind::Interrupted {
                return Err(error).context("wait for kqueue event");
            }
        }
    }
}

#[cfg(target_os = "macos")]
fn run_event_loop(
    store: &Store,
    sessions_root: &Path,
    since: Option<DateTime<FixedOffset>>,
    debounce_ms: u64,
) -> Result<()> {
    let mut watcher = KqueueWatcher::new(sessions_root, since)?;
    eprintln!(
        "memory-wuxian-collector ready (kqueue): {}",
        sessions_root.display()
    );
    loop {
        watcher.wait()?;
        std::thread::sleep(Duration::from_millis(debounce_ms));
        sync_and_emit(store, recent_rollouts(sessions_root, since)?);
        watcher = KqueueWatcher::new(sessions_root, since)?;
    }
}

#[cfg(not(target_os = "macos"))]
fn run_event_loop(
    store: &Store,
    sessions_root: &Path,
    since: Option<DateTime<FixedOffset>>,
    debounce_ms: u64,
) -> Result<()> {
    let (sender, receiver) = mpsc::channel();
    let mut watcher: RecommendedWatcher = notify::recommended_watcher(move |event| {
        let _ = sender.send(event);
    })?;
    watcher.watch(sessions_root, RecursiveMode::Recursive)?;
    eprintln!("memory-wuxian-collector ready: {}", sessions_root.display());
    loop {
        let first = match receiver.recv() {
            Ok(value) => value,
            Err(_) => bail!("filesystem watcher stopped"),
        };
        let mut candidates = BTreeSet::new();
        match first {
            Ok(event) => candidates.extend(event_rollouts(event, sessions_root, since)?),
            Err(error) => {
                eprintln!("watch error: {error}");
                continue;
            }
        }
        loop {
            match receiver.recv_timeout(Duration::from_millis(debounce_ms)) {
                Ok(Ok(event)) => candidates.extend(event_rollouts(event, sessions_root, since)?),
                Ok(Err(error)) => eprintln!("watch error: {error}"),
                Err(RecvTimeoutError::Timeout) => break,
                Err(RecvTimeoutError::Disconnected) => bail!("filesystem watcher stopped"),
            }
        }
        if !candidates.is_empty() {
            sync_and_emit(store, candidates.into_iter().collect());
        }
    }
}

fn main() -> Result<()> {
    let args = Args::parse();
    let sessions_root = expand_tilde(&args.sessions_root)?
        .canonicalize()
        .with_context(|| {
            format!(
                "sessions root does not exist: {}",
                args.sessions_root.display()
            )
        })?;
    let archive_root = expand_tilde(&args.archive_root)?;
    let config_path = expand_tilde(&args.config)?;
    let since = args
        .since
        .as_deref()
        .map(DateTime::parse_from_rfc3339)
        .transpose()?;
    let store = Store::new(archive_root, &config_path)?;
    let initial_paths = if args.session_files.is_empty() {
        recent_rollouts(&sessions_root, since)?
    } else {
        args.session_files
            .iter()
            .map(|path| expand_tilde(path).and_then(|p| Ok(p.canonicalize()?)))
            .collect::<Result<Vec<_>>>()?
    };
    let initial = store.sync_batch(initial_paths)?;
    if args.once {
        emit(&initial)?;
        return Ok(());
    }
    if initial
        .get("imported_messages")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        > 0
    {
        emit(&initial)?;
    }

    run_event_loop(&store, &sessions_root, since, args.debounce_ms)
}
