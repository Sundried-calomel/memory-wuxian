use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
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
use serde::de::{Error as DeError, IgnoredAny, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

const RAW_MARKER: &str = "<!-- memory-wuxian-record -->";
const TOKEN_USAGE_FORMAT_VERSION: u64 = 1;
const ROLLOUT_BATCH_MAX_LINES: usize = 512;
const ROLLOUT_BATCH_MAX_BYTES: u64 = 4 * 1024 * 1024;
const TOKEN_USAGE_FIELDS: [&str; 6] = [
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
];

#[derive(Debug, Default)]
struct RolloutRoute {
    outer: Option<String>,
    payload: Option<String>,
}

const ROUTE_CAPTURED: &str = "memory-wuxian-rollout-route-captured";

struct PayloadTypeVisitor<'a> {
    route: &'a RefCell<RolloutRoute>,
}

impl<'de> Visitor<'de> for PayloadTypeVisitor<'_> {
    type Value = ();

    fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
        formatter.write_str("a rollout payload object")
    }

    fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        while let Some(key) = map.next_key::<String>()? {
            if key == "type" {
                self.route.borrow_mut().payload = Some(map.next_value::<String>()?);
                return Err(A::Error::custom(ROUTE_CAPTURED));
            }
            map.next_value::<IgnoredAny>()?;
        }
        Ok(())
    }
}

struct RolloutRouteVisitor<'a> {
    route: &'a RefCell<RolloutRoute>,
}

impl<'de> Visitor<'de> for RolloutRouteVisitor<'_> {
    type Value = ();

    fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
        formatter.write_str("a Codex rollout event object")
    }

    fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        while let Some(key) = map.next_key::<String>()? {
            match key.as_str() {
                "type" => self.route.borrow_mut().outer = Some(map.next_value::<String>()?),
                "payload" if self.route.borrow().outer.is_some() => {
                    if !matches!(
                        self.route.borrow().outer.as_deref(),
                        Some("event_msg" | "response_item")
                    ) {
                        return Err(A::Error::custom(ROUTE_CAPTURED));
                    }
                    map.next_value_seed(PayloadTypeSeed { route: self.route })?;
                }
                _ => {
                    map.next_value::<IgnoredAny>()?;
                }
            }
        }
        Ok(())
    }
}

struct PayloadTypeSeed<'a> {
    route: &'a RefCell<RolloutRoute>,
}

impl<'de> serde::de::DeserializeSeed<'de> for PayloadTypeSeed<'_> {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_map(PayloadTypeVisitor { route: self.route })
    }
}

fn rollout_route(text: &str) -> Result<RolloutRoute> {
    let route = RefCell::new(RolloutRoute::default());
    let mut deserializer = serde_json::Deserializer::from_str(text);
    match deserializer.deserialize_map(RolloutRouteVisitor { route: &route }) {
        Ok(()) => Ok(route.into_inner()),
        Err(error) if error.to_string().starts_with(ROUTE_CAPTURED) => Ok(route.into_inner()),
        Err(error) => Err(error.into()),
    }
}

fn empty_usage() -> BTreeMap<String, u64> {
    TOKEN_USAGE_FIELDS
        .into_iter()
        .map(|field| (field.to_owned(), 0))
        .collect()
}

fn normalized_usage(value: Option<&Value>) -> Option<BTreeMap<String, u64>> {
    let value = value?.as_object()?;
    let mut usage = empty_usage();
    for field in TOKEN_USAGE_FIELDS {
        let number = value.get(field).and_then(Value::as_u64).unwrap_or(0);
        usage.insert(field.to_owned(), number);
    }
    Some(usage)
}

fn usage_value(usage: &BTreeMap<String, u64>) -> Value {
    Value::Object(
        usage
            .iter()
            .map(|(key, value)| (key.clone(), json!(value)))
            .collect(),
    )
}

fn add_usage(left: &BTreeMap<String, u64>, right: &BTreeMap<String, u64>) -> BTreeMap<String, u64> {
    TOKEN_USAGE_FIELDS
        .into_iter()
        .map(|field| {
            (
                field.to_owned(),
                left.get(field).copied().unwrap_or(0) + right.get(field).copied().unwrap_or(0),
            )
        })
        .collect()
}

fn usage_total(usage: &BTreeMap<String, u64>) -> u64 {
    usage.get("total_tokens").copied().unwrap_or(0)
}

fn usage_signature(usage: &BTreeMap<String, u64>) -> [u64; 6] {
    TOKEN_USAGE_FIELDS.map(|field| usage.get(field).copied().unwrap_or(0))
}

fn portable_path(path: &Path) -> String {
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

fn summarize_tool_activity(payload: &Value) -> Option<String> {
    let event_type = payload.get("type")?.as_str()?;
    if !matches!(
        event_type,
        "custom_tool_call" | "function_call" | "local_shell_call" | "web_search_call"
    ) {
        return None;
    }
    let tool_name = payload
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or(event_type);
    let input_value = payload
        .get("input")
        .or_else(|| payload.get("arguments"))
        .or_else(|| payload.get("command"));
    let raw_input = match input_value {
        Some(Value::String(value)) => value.clone(),
        Some(value) => serde_json::to_string(value).unwrap_or_default(),
        None => String::new(),
    };
    let nested_re = Regex::new(r"tools\.([A-Za-z0-9_]+)").expect("valid tool regex");
    let nested: BTreeSet<&str> = nested_re
        .captures_iter(&raw_input)
        .filter_map(|capture| capture.get(1).map(|value| value.as_str()))
        .collect();
    let command = serde_json::from_str::<Value>(&raw_input)
        .ok()
        .and_then(|value| {
            value
                .get("command")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .or_else(|| {
            let command_re = Regex::new(r#"command\s*:\s*\"((?:\\.|[^\"])*)\""#).ok()?;
            let encoded = command_re.captures(&raw_input)?.get(1)?.as_str();
            serde_json::from_str::<String>(&format!(r#""{encoded}""#)).ok()
        });
    if let Some(command) = command.filter(|value| !value.is_empty()) {
        return Some(format!(
            "Ran {}",
            command.chars().take(1000).collect::<String>()
        ));
    }
    let mut text = format!("Called tool: {tool_name}");
    if !nested.is_empty() {
        text.push_str(&format!(
            " (invokes {})",
            nested.into_iter().collect::<Vec<_>>().join(", ")
        ));
    }
    Some(text)
}

fn summarize_file_change(payload: &Value) -> Option<String> {
    if payload.get("type").and_then(Value::as_str) != Some("patch_apply_end")
        || payload.get("success").and_then(Value::as_bool) != Some(true)
    {
        return None;
    }
    let changes = payload.get("changes")?.as_object()?;
    if changes.is_empty() {
        return None;
    }

    let mut rendered = Vec::new();
    let mut total_additions = 0usize;
    let mut total_deletions = 0usize;
    let mut paths = changes.keys().collect::<Vec<_>>();
    paths.sort();
    for path in paths {
        let change = changes.get(path).and_then(Value::as_object);
        let diff = change
            .and_then(|value| value.get("unified_diff"))
            .and_then(Value::as_str)
            .unwrap_or("");
        let additions = diff
            .lines()
            .filter(|line| line.starts_with('+') && !line.starts_with("+++"))
            .count();
        let deletions = diff
            .lines()
            .filter(|line| line.starts_with('-') && !line.starts_with("---"))
            .count();
        total_additions += additions;
        total_deletions += deletions;
        let change_type = change
            .and_then(|value| value.get("type"))
            .and_then(Value::as_str)
            .unwrap_or("update");
        let mut detail = format!("File: {path} [{change_type}] (+{additions} -{deletions})");
        if let Some(move_path) = change
            .and_then(|value| value.get("move_path"))
            .and_then(Value::as_str)
        {
            detail.push_str(&format!(" -> {move_path}"));
        }
        if !diff.is_empty() {
            detail.push_str(&format!("\n```diff\n{}\n```", diff.trim_end()));
        }
        rendered.push(detail);
    }
    let noun = if rendered.len() == 1 { "file" } else { "files" };
    Some(format!(
        "Edited {} {noun}: +{total_additions} -{total_deletions}\n\n{}",
        rendered.len(),
        rendered.join("\n\n")
    ))
}

#[derive(Parser, Debug)]
#[command(
    name = "memory-wuxian-collector",
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
    #[arg(long)]
    python_executable: Option<PathBuf>,
    #[arg(long)]
    codex_cli: Option<PathBuf>,
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
    #[serde(default)]
    ai_summary: AiSummaryConfig,
}

#[derive(Debug, Deserialize)]
struct AiSummaryConfig {
    #[serde(default)]
    enabled: bool,
    #[serde(default = "default_python_path")]
    python_path: String,
    #[serde(default)]
    python_path_windows: Option<String>,
    #[serde(default = "default_semantic_dispatch_path")]
    dispatcher_path: String,
}

impl Default for AiSummaryConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            python_path: default_python_path(),
            python_path_windows: None,
            dispatcher_path: default_semantic_dispatch_path(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct SummaryConfig {
    #[serde(default = "default_l1_trigger")]
    level_1_trigger_rounds: u64,
    #[serde(default = "default_l1_character_trigger")]
    level_1_trigger_characters: u64,
    #[serde(default = "default_l1_token_trigger")]
    level_1_trigger_tokens: u64,
    #[serde(default)]
    automatic_semantic_jobs: bool,
    #[serde(default = "default_higher_level_trigger")]
    higher_level_trigger_count: usize,
    #[serde(default = "default_maximum_summary_depth")]
    maximum_summary_depth: u64,
}

impl Default for SummaryConfig {
    fn default() -> Self {
        Self {
            level_1_trigger_rounds: default_l1_trigger(),
            level_1_trigger_characters: default_l1_character_trigger(),
            level_1_trigger_tokens: default_l1_token_trigger(),
            automatic_semantic_jobs: false,
            higher_level_trigger_count: default_higher_level_trigger(),
            maximum_summary_depth: default_maximum_summary_depth(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct BackupConfig {
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    directory: String,
}

impl Default for BackupConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            directory: String::new(),
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
    5
}
fn default_l1_character_trigger() -> u64 {
    20_000
}
fn default_l1_token_trigger() -> u64 {
    6_667
}

fn default_higher_level_trigger() -> usize {
    10
}

fn default_maximum_summary_depth() -> u64 {
    8
}

fn default_python_path() -> String {
    if cfg!(windows) {
        "python.exe".to_owned()
    } else {
        "/opt/homebrew/bin/python3".to_owned()
    }
}

fn default_semantic_dispatch_path() -> String {
    "scripts/semantic_dispatch.py".to_owned()
}
fn default_true() -> bool {
    true
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
    token_usage_changed_events: u64,
    reported_total_tokens: Option<u64>,
    token_usage_ledger: Option<PathBuf>,
    excluded_reason: Option<String>,
    committed_byte_offset: u64,
    observed_source_size: u64,
    complete: bool,
    caught_up: bool,
}

#[derive(Debug)]
struct RolloutLine {
    number: u64,
    text: String,
}

#[derive(Debug)]
struct RolloutBatch {
    session_id: String,
    excluded_reason: Option<String>,
    lines: Vec<RolloutLine>,
    committed_byte_offset: u64,
    observed_source_size: u64,
    observed_source_mtime: SystemTime,
    complete: bool,
    caught_up: bool,
}

#[derive(Debug)]
struct PreparedSync {
    source_path: PathBuf,
    hinted_cursor: Value,
    last_line: u64,
    backfill_file_changes: bool,
    backfill_token_usage: bool,
    resume_line: u64,
    batch: RolloutBatch,
}

#[derive(Debug, Default)]
struct TokenUsageUpdate {
    changed_events: u64,
    reported_total_tokens: Option<u64>,
    ledger_path: Option<PathBuf>,
}

#[derive(Debug, Default)]
struct AppendResult {
    appended: bool,
    transcript_repaired: bool,
}

struct Store {
    root: PathBuf,
    config_path: PathBuf,
    config: Config,
    message_cache: RefCell<Option<HashMap<String, Value>>>,
    python_executable: Option<PathBuf>,
    codex_cli: Option<PathBuf>,
}

fn session_metadata(event: &Value) -> Option<(String, Option<String>)> {
    if event.get("type").and_then(Value::as_str) != Some("session_meta") {
        return None;
    }
    let payload = event.get("payload")?;
    let session_id = payload
        .get("id")
        .or_else(|| payload.get("session_id"))
        .and_then(Value::as_str)?
        .to_owned();
    let is_subagent = payload
        .get("source")
        .and_then(Value::as_object)
        .is_some_and(|source| source.contains_key("subagent"));
    let is_exec = payload.get("source").and_then(Value::as_str) == Some("exec");
    let reason = if is_subagent {
        Some("subagent-session".to_owned())
    } else if is_exec {
        Some("exec-session".to_owned())
    } else {
        None
    };
    Some((session_id, reason))
}

fn read_complete_line(reader: &mut BufReader<File>, buffer: &mut Vec<u8>) -> Result<Option<u64>> {
    buffer.clear();
    let start = reader.stream_position()?;
    if reader.read_until(b'\n', buffer)? == 0 {
        return Ok(None);
    }
    if !buffer.ends_with(b"\n") {
        reader.seek(SeekFrom::Start(start))?;
        buffer.clear();
        return Ok(None);
    }
    Ok(Some(reader.stream_position()?))
}

fn read_rollout_batch(
    source_path: &Path,
    start_line: u64,
    start_offset: Option<u64>,
) -> Result<RolloutBatch> {
    let file = File::open(source_path)?;
    let source_metadata = file.metadata()?;
    let observed_source_size = source_metadata.len();
    let observed_source_mtime = source_metadata.modified()?;
    let mut reader = BufReader::new(file);
    let mut buffer = Vec::new();
    let mut session = None;
    let mut line_number = 0u64;
    let mut committed_byte_offset = 0u64;

    while (start_offset.is_none() && line_number < start_line) || session.is_none() {
        let Some(end_offset) = read_complete_line(&mut reader, &mut buffer)? else {
            break;
        };
        line_number += 1;
        committed_byte_offset = end_offset;
        if session.is_none() {
            let decoded = std::str::from_utf8(buffer.strip_suffix(b"\n").unwrap_or(&buffer))?;
            let text = decoded.strip_suffix('\r').unwrap_or(decoded);
            if let Ok(event) = serde_json::from_str::<Value>(text) {
                session = session_metadata(&event);
            }
        }
    }

    let (session_id, excluded_reason) = session.ok_or_else(|| {
        anyhow!(
            "Codex session metadata is missing an ID: {}",
            source_path.display()
        )
    })?;
    if let Some(offset) = start_offset {
        if offset > observed_source_size {
            bail!(
                "Codex session was truncated below its byte cursor: {} ({} < {offset})",
                source_path.display(),
                observed_source_size
            );
        }
        reader.seek(SeekFrom::Start(offset))?;
        committed_byte_offset = offset;
        line_number = start_line;
    }

    let batch_start = committed_byte_offset;
    let mut lines = Vec::new();
    let mut stopped_at_limit = false;
    loop {
        if lines.len() >= ROLLOUT_BATCH_MAX_LINES
            || committed_byte_offset.saturating_sub(batch_start) >= ROLLOUT_BATCH_MAX_BYTES
        {
            stopped_at_limit = true;
            break;
        }
        let Some(end_offset) = read_complete_line(&mut reader, &mut buffer)? else {
            break;
        };
        line_number += 1;
        let bytes = buffer.strip_suffix(b"\n").unwrap_or(&buffer);
        let decoded = std::str::from_utf8(bytes)?;
        let text = decoded.strip_suffix('\r').unwrap_or(decoded).to_owned();
        committed_byte_offset = end_offset;
        lines.push(RolloutLine {
            number: line_number,
            text,
        });
    }
    let complete = committed_byte_offset == observed_source_size;
    Ok(RolloutBatch {
        session_id,
        excluded_reason,
        lines,
        committed_byte_offset,
        observed_source_size,
        observed_source_mtime,
        complete,
        caught_up: complete || !stopped_at_limit,
    })
}

impl Store {
    fn new(
        root: PathBuf,
        config_path: &Path,
        python_executable: Option<PathBuf>,
        codex_cli: Option<PathBuf>,
    ) -> Result<Self> {
        let config_text = fs::read_to_string(config_path)
            .with_context(|| format!("read config {}", config_path.display()))?;
        let config: Config = serde_yaml::from_str(&config_text).context("parse config")?;
        let root = expand_tilde(&root)?.canonicalize().or_else(|_| {
            fs::create_dir_all(&root)?;
            root.canonicalize()
        })?;
        let store = Self {
            root,
            config_path: config_path.canonicalize()?,
            config,
            message_cache: RefCell::new(None),
            python_executable,
            codex_cli,
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
            "imports/codex/token-usage",
            "dashboard",
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
            "---\nrecord_type: conversation_transcript\nconversation_id: {}\nformat_version: 1\n---\n\n# Conversation {}\n\nThis file contains user messages, user-visible assistant text, and lightweight visible tool activity. The fenced JSON record preserves the exact stored text and source metadata.\n\n",
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

            let max_raw_sequence = self
                .message_cache
                .borrow()
                .as_ref()
                .into_iter()
                .flat_map(|cache| cache.values())
                .filter_map(|record| record.get("sequence").and_then(Value::as_u64))
                .max()
                .unwrap_or(0);
            let sequence = u64_field(&state, "total_messages")?.max(max_raw_sequence) + 1;
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
        self.root
            .join("imports/codex")
            .join(format!("{}.json", Self::safe_session_id(session_id)))
    }

    fn write_coverage_status(&self, paths: &[PathBuf]) -> Result<()> {
        let mut missing_cursor_rollouts = 0u64;
        let mut incomplete_rollouts = 0u64;
        let mut pending_bytes = 0u64;
        let mut observed_bytes = 0u64;
        for path in paths {
            let metadata = fs::metadata(path)?;
            observed_bytes = observed_bytes.saturating_add(metadata.len());
            let cursor = rollout_session_id(path)
                .map(|session_id| self.cursor_path(&session_id))
                .filter(|cursor_path| cursor_path.exists())
                .map(|cursor_path| read_json(&cursor_path))
                .transpose()?;
            let Some(cursor) = cursor else {
                missing_cursor_rollouts += 1;
                pending_bytes = pending_bytes.saturating_add(metadata.len());
                continue;
            };
            let committed = cursor
                .get("committed_byte_offset")
                .or_else(|| cursor.get("source_size"))
                .and_then(Value::as_u64)
                .unwrap_or(0)
                .min(metadata.len());
            if !cursor_covers_source(&cursor, path, &metadata) {
                incomplete_rollouts += 1;
                pending_bytes = pending_bytes.saturating_add(metadata.len() - committed);
            }
        }
        atomic_write_json(
            &self.root.join("imports/codex/coverage-status.json"),
            &json!({
                "format_version": 1,
                "status": if missing_cursor_rollouts == 0 && incomplete_rollouts == 0 {
                    "covered"
                } else {
                    "catching-up"
                },
                "scoped_rollouts": paths.len(),
                "missing_cursor_rollouts": missing_cursor_rollouts,
                "incomplete_rollouts": incomplete_rollouts,
                "pending_bytes": pending_bytes,
                "observed_bytes": observed_bytes,
                "updated_at": now_iso(),
            }),
        )
    }

    fn safe_session_id(session_id: &str) -> String {
        session_id
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || "._-".contains(c) {
                    c
                } else {
                    '_'
                }
            })
            .collect()
    }

    fn update_token_usage(
        &self,
        session_id: &str,
        source_path: &Path,
        lines: &[RolloutLine],
        committed_line: u64,
    ) -> Result<TokenUsageUpdate> {
        let path = self
            .root
            .join("imports/codex/token-usage")
            .join(format!("{}.json", Self::safe_session_id(session_id)));
        let mut ledger = if path.exists() {
            let value = read_json(&path)?;
            if value.get("format_version").and_then(Value::as_u64)
                != Some(TOKEN_USAGE_FORMAT_VERSION)
            {
                bail!("unsupported token usage ledger format: {}", path.display());
            }
            value
        } else {
            json!({
                "format_version": TOKEN_USAGE_FORMAT_VERSION,
                "measurement": "codex-reported-model-usage",
                "conversation_id": format!("codex:{session_id}"),
                "session_id": session_id,
                "source": {
                    "kind": "codex-rollout-token-count",
                    "path": portable_path(source_path),
                },
                "scanned_through_line": 0,
                "first_token_event": null,
                "last_token_event": null,
                "token_event_count": 0,
                "model_request_count": 0,
                "counter_reset_count": 0,
                "closed_segments_usage": usage_value(&empty_usage()),
                "current_segment_usage": usage_value(&empty_usage()),
                "reported_usage": usage_value(&empty_usage()),
                "latest_request_usage": usage_value(&empty_usage()),
                "model_context_window": 0,
                "updated_at": null,
            })
        };
        let ledger_line = ledger
            .get("scanned_through_line")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let existing_events = ledger
            .get("token_event_count")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let mut previous_signature = if existing_events > 0 {
            normalized_usage(ledger.get("current_segment_usage"))
                .map(|value| usage_signature(&value))
        } else {
            None
        };
        let mut changed_events = 0u64;
        for line in lines.iter().filter(|line| line.number > ledger_line) {
            let line_number = line.number;
            if rollout_route(&line.text)?.outer.as_deref() != Some("event_msg") {
                continue;
            }
            let event: Value = serde_json::from_str(&line.text).with_context(|| {
                format!(
                    "invalid Codex JSONL {}:{line_number}",
                    source_path.display()
                )
            })?;
            if event.get("type").and_then(Value::as_str) != Some("event_msg") {
                continue;
            }
            let Some(payload) = event.get("payload") else {
                continue;
            };
            if payload.get("type").and_then(Value::as_str) != Some("token_count") {
                continue;
            }
            let Some(info) = payload.get("info") else {
                continue;
            };
            let Some(cumulative) = normalized_usage(info.get("total_token_usage")) else {
                continue;
            };
            let Some(latest_request) = normalized_usage(info.get("last_token_usage")) else {
                continue;
            };
            let current =
                normalized_usage(ledger.get("current_segment_usage")).unwrap_or_else(empty_usage);
            let mut closed =
                normalized_usage(ledger.get("closed_segments_usage")).unwrap_or_else(empty_usage);
            if ledger
                .get("token_event_count")
                .and_then(Value::as_u64)
                .unwrap_or(0)
                > 0
                && usage_total(&cumulative) < usage_total(&current)
            {
                closed = add_usage(&closed, &current);
                ledger["counter_reset_count"] = json!(
                    ledger
                        .get("counter_reset_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0)
                        + 1
                );
            }
            let signature = usage_signature(&cumulative);
            if previous_signature != Some(signature) {
                ledger["model_request_count"] = json!(
                    ledger
                        .get("model_request_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0)
                        + 1
                );
            }
            previous_signature = Some(signature);
            let marker = json!({
                "line": line_number,
                "timestamp": event.get("timestamp").and_then(Value::as_str).unwrap_or(""),
            });
            if ledger.get("first_token_event").map_or(true, Value::is_null) {
                ledger["first_token_event"] = marker.clone();
            }
            ledger["last_token_event"] = marker;
            ledger["token_event_count"] = json!(
                ledger
                    .get("token_event_count")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    + 1
            );
            ledger["closed_segments_usage"] = usage_value(&closed);
            ledger["current_segment_usage"] = usage_value(&cumulative);
            ledger["reported_usage"] = usage_value(&add_usage(&closed, &cumulative));
            ledger["latest_request_usage"] = usage_value(&latest_request);
            ledger["model_context_window"] = json!(
                info.get("model_context_window")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
            );
            changed_events += 1;
        }
        if changed_events == 0 {
            return Ok(TokenUsageUpdate {
                changed_events: 0,
                reported_total_tokens: normalized_usage(ledger.get("reported_usage"))
                    .map(|value| usage_total(&value))
                    .filter(|_| path.exists()),
                ledger_path: path.exists().then_some(path),
            });
        }
        ledger["source"] = json!({
            "kind": "codex-rollout-token-count",
            "path": portable_path(source_path),
        });
        ledger["scanned_through_line"] = json!(committed_line);
        ledger["updated_at"] = json!(now_iso());
        atomic_write_json(&path, &ledger)?;
        Ok(TokenUsageUpdate {
            changed_events,
            reported_total_tokens: normalized_usage(ledger.get("reported_usage"))
                .map(|value| usage_total(&value)),
            ledger_path: Some(path),
        })
    }

    fn changed_rollouts(&self, paths: Vec<PathBuf>) -> Result<Vec<PathBuf>> {
        let session_pattern =
            Regex::new(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")?;
        let changed = paths
            .into_iter()
            .filter_map(|path| {
                let session_id = path
                    .file_name()
                    .and_then(|value| value.to_str())
                    .and_then(|value| session_pattern.captures(value))
                    .and_then(|captures| captures.get(1))
                    .map(|value| value.as_str());
                let Some(session_id) = session_id else {
                    return Some(Ok(path));
                };
                let cursor_path = self.cursor_path(session_id);
                let cursor = match read_json(&cursor_path) {
                    Ok(value) => value,
                    Err(_) => return Some(Ok(path)),
                };
                let key = portable_path(&path);
                if cursor.get("source_path").and_then(Value::as_str) != Some(key.as_str()) {
                    return Some(Ok(path));
                }
                let metadata = match fs::metadata(&path) {
                    Ok(value) => value,
                    Err(error) => return Some(Err(error.into())),
                };
                if cursor.get("source_size").and_then(Value::as_u64) != Some(metadata.len()) {
                    return Some(Ok(path));
                }
                if cursor.get("excluded_reason").is_some() {
                    return None;
                }
                let cursor_modified = cursor
                    .get("source_mtime")
                    .and_then(Value::as_str)
                    .and_then(|value| DateTime::parse_from_rfc3339(value).ok());
                let current_modified: DateTime<Utc> =
                    metadata.modified().unwrap_or(SystemTime::now()).into();
                match cursor_modified {
                    Some(value)
                        if value.timestamp_nanos_opt()
                            == current_modified.timestamp_nanos_opt() =>
                    {
                        None
                    }
                    _ => Some(Ok(path)),
                }
            })
            .collect::<Result<Vec<_>>>()?;
        eprintln!("memory-wuxian-collector startup: cursor comparison completed");
        Ok(changed)
    }

    fn prepare_sync_file(&self, source_path: &Path) -> Result<PreparedSync> {
        let source_path = source_path.canonicalize()?;
        let hinted_session_id = resolve_rollout_session_id(&source_path)?;
        let hinted_cursor = hinted_session_id
            .as_ref()
            .map(|session_id| self.cursor_path(session_id))
            .filter(|path| path.exists())
            .map(|path| read_json(&path))
            .transpose()?
            .unwrap_or_else(|| json!({}));
        let last_line = hinted_cursor
            .get("last_line")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let backfill_file_changes = hinted_cursor
            .get("file_change_format_version")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            < 1;
        let backfill_token_usage = hinted_cursor
            .get("token_usage_format_version")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            < TOKEN_USAGE_FORMAT_VERSION;
        if last_line > 0 {
            let metadata = fs::metadata(&source_path)?;
            let committed = hinted_cursor
                .get("committed_byte_offset")
                .or_else(|| hinted_cursor.get("source_size"))
                .and_then(Value::as_u64)
                .unwrap_or(0);
            if metadata.len() < committed {
                bail!(
                    "Codex session was truncated below its byte cursor: {} ({} < {committed})",
                    source_path.display(),
                    metadata.len()
                );
            }
            let current_modified: DateTime<Utc> =
                metadata.modified().unwrap_or(SystemTime::now()).into();
            let cursor_modified = hinted_cursor
                .get("source_mtime")
                .and_then(Value::as_str)
                .and_then(|value| DateTime::parse_from_rfc3339(value).ok());
            if metadata.len() == committed
                && cursor_modified.is_some_and(|value| {
                    value.timestamp_nanos_opt() != current_modified.timestamp_nanos_opt()
                })
            {
                bail!(
                    "Codex session identity changed without an append: {}",
                    source_path.display()
                );
            }
        }
        let resume_line = if backfill_file_changes || backfill_token_usage {
            0
        } else {
            last_line
        };
        let resume_offset = if resume_line == last_line && resume_line > 0 {
            hinted_cursor
                .get("committed_byte_offset")
                .and_then(Value::as_u64)
        } else {
            None
        };
        let batch = read_rollout_batch(&source_path, resume_line, resume_offset)?;
        let session_id = batch.session_id.clone();
        if hinted_session_id
            .as_deref()
            .is_some_and(|hint| hint != session_id)
        {
            bail!(
                "rollout filename and session metadata IDs differ: {}",
                source_path.display()
            );
        }
        Ok(PreparedSync {
            source_path,
            hinted_cursor,
            last_line,
            backfill_file_changes,
            backfill_token_usage,
            resume_line,
            batch,
        })
    }

    fn sync_prepared_file(&self, prepared: PreparedSync) -> Result<FileSyncResult> {
        let PreparedSync {
            source_path,
            hinted_cursor,
            last_line,
            backfill_file_changes,
            backfill_token_usage,
            resume_line,
            batch,
        } = prepared;
        let session_id = batch.session_id.clone();
        let excluded_reason = batch.excluded_reason.clone();
        let cursor_path = self.cursor_path(&session_id);
        let current_cursor = if cursor_path.exists() {
            read_json(&cursor_path)?
        } else {
            json!({})
        };
        if current_cursor != hinted_cursor {
            bail!(
                "Codex cursor changed while a source batch was prepared: {}",
                source_path.display()
            );
        }
        let committed_line = batch
            .lines
            .last()
            .map(|line| line.number)
            .unwrap_or(resume_line);
        if committed_line < last_line && !backfill_file_changes && !backfill_token_usage {
            bail!(
                "Codex session was truncated below its cursor: {} ({committed_line} < {last_line})",
                source_path.display()
            );
        }
        let mut result = FileSyncResult {
            session_id: session_id.clone(),
            source_path: source_path.clone(),
            last_line: committed_line,
            committed_byte_offset: batch.committed_byte_offset,
            observed_source_size: batch.observed_source_size,
            complete: batch.complete,
            caught_up: batch.caught_up,
            ..Default::default()
        };
        if let Some(excluded_reason) = excluded_reason {
            let modified: DateTime<Utc> = batch.observed_source_mtime.into();
            atomic_write_json(
                &cursor_path,
                &json!({
                    "format_version": 1,
                    "session_id": session_id,
                    "source_path": portable_path(&source_path),
                    "last_line": committed_line,
                    "file_change_format_version": 1,
                    "token_usage_format_version": TOKEN_USAGE_FORMAT_VERSION,
                    "source_size": batch.committed_byte_offset,
                    "committed_byte_offset": batch.committed_byte_offset,
                    "observed_source_size": batch.observed_source_size,
                    "complete": batch.complete,
                    "source_mtime": modified.to_rfc3339(),
                    "excluded_reason": excluded_reason,
                    "updated_at": now_iso(),
                }),
            )?;
            result.excluded_reason = Some(excluded_reason.to_owned());
            return Ok(result);
        }
        for line in &batch.lines {
            let line_number = line.number;
            let route = rollout_route(&line.text)?;
            if route.outer.as_deref() == Some("response_item")
                && !matches!(
                    route.payload.as_deref(),
                    Some(
                        "custom_tool_call"
                            | "function_call"
                            | "local_shell_call"
                            | "web_search_call"
                    )
                )
            {
                continue;
            }
            if !matches!(route.outer.as_deref(), Some("event_msg" | "response_item")) {
                continue;
            }
            let event: Value = serde_json::from_str(&line.text).with_context(|| {
                format!(
                    "invalid Codex JSONL {}:{line_number}",
                    source_path.display()
                )
            })?;
            let outer_type = event.get("type").and_then(Value::as_str);
            let payload = match event.get("payload") {
                Some(value) => value,
                None => continue,
            };
            let event_type = payload.get("type").and_then(Value::as_str);
            let incoming_phase = payload.get("phase").and_then(Value::as_str);
            let tool_activity = if outer_type == Some("response_item") {
                summarize_tool_activity(payload)
            } else {
                None
            };
            let file_change = if outer_type == Some("event_msg") {
                summarize_file_change(payload)
            } else {
                None
            };
            if line_number <= last_line && file_change.is_none() {
                continue;
            }
            let (speaker, phase, complete_round, message) =
                match (outer_type, event_type, incoming_phase) {
                    (_, _, _) if file_change.is_some() => {
                        ("tool", "file_change", false, file_change.unwrap())
                    }
                    (_, _, _) if tool_activity.is_some() => {
                        ("tool", "tool_activity", false, tool_activity.unwrap())
                    }
                    (Some("event_msg"), Some("user_message"), _) => (
                        "user",
                        "user",
                        false,
                        payload
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_owned(),
                    ),
                    (Some("event_msg"), Some("agent_message"), Some("commentary")) => (
                        "assistant",
                        "commentary",
                        false,
                        payload
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_owned(),
                    ),
                    (Some("event_msg"), Some("agent_message"), Some("final_answer")) => {
                        let text = payload
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_owned();
                        ("assistant", "final_answer", true, text)
                    }
                    _ => continue,
                };
            if message.is_empty() {
                continue;
            }
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
            let suffix = match speaker {
                "user" => "u",
                "assistant" => "a",
                _ => "t",
            };
            let message_id = format!("codex-{session_id}-{line_number:08}-{suffix}");
            let append = self.append_message(
                speaker,
                &message,
                &timestamp,
                &format!("codex:{session_id}"),
                &message_id,
                complete_round,
                json!({
                    "kind": "codex-rollout-jsonl",
                    "session_id": session_id,
                    "path": portable_path(&source_path),
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
        let token_usage =
            self.update_token_usage(&session_id, &source_path, &batch.lines, committed_line)?;
        result.token_usage_changed_events = token_usage.changed_events;
        result.reported_total_tokens = token_usage.reported_total_tokens;
        result.token_usage_ledger = token_usage.ledger_path;
        if committed_line != last_line || backfill_file_changes || backfill_token_usage {
            let modified: DateTime<Utc> = batch.observed_source_mtime.into();
            atomic_write_json(
                &cursor_path,
                &json!({
                    "format_version": 1,
                    "session_id": session_id,
                    "source_path": portable_path(&source_path),
                    "last_line": committed_line,
                    "file_change_format_version": 1,
                    "token_usage_format_version": TOKEN_USAGE_FORMAT_VERSION,
                    "source_size": batch.committed_byte_offset,
                    "committed_byte_offset": batch.committed_byte_offset,
                    "observed_source_size": batch.observed_source_size,
                    "complete": batch.complete,
                    "source_mtime": modified.to_rfc3339(),
                    "updated_at": now_iso(),
                }),
            )?;
        }
        Ok(result)
    }

    fn deterministic_level_one_record(
        &self,
        conversation_id: &str,
        selected_rounds: &[Vec<Value>],
    ) -> Result<Value> {
        let mut records: Vec<Value> = selected_rounds.iter().flatten().cloned().collect();
        records.sort_by_key(|record| record.get("sequence").and_then(Value::as_u64).unwrap_or(0));
        let start_round = records
            .first()
            .and_then(|v| v.get("round_number"))
            .and_then(Value::as_u64)
            .ok_or_else(|| anyhow!("deterministic index start round is missing"))?;
        let end_round = records
            .last()
            .and_then(|v| v.get("round_number"))
            .and_then(Value::as_u64)
            .ok_or_else(|| anyhow!("deterministic index end round is missing"))?;
        let signature = format!("conversation:{conversation_id}:rounds:{start_round}-{end_round}");
        let mut timestamps: Vec<(DateTime<FixedOffset>, String)> = records
            .iter()
            .map(|record| {
                let value = string_field(record, "timestamp")?.to_owned();
                Ok((DateTime::parse_from_rfc3339(&value)?, value))
            })
            .collect::<Result<Vec<_>>>()?;
        timestamps.sort_by_key(|entry| entry.0);
        let mut user_anchors = Vec::new();
        let mut assistant_anchors = Vec::new();
        for record in &records {
            let text = record.get("text").and_then(Value::as_str).unwrap_or("");
            match record.get("speaker").and_then(Value::as_str) {
                Some("user") => push_unique_excerpt(&mut user_anchors, text, 5),
                Some("assistant")
                    if record
                        .get("completes_round")
                        .and_then(Value::as_bool)
                        .unwrap_or(true) =>
                {
                    push_unique_excerpt(&mut assistant_anchors, text, 5)
                }
                _ => {}
            }
        }
        let source_message_ids: Vec<Value> = records
            .iter()
            .map(|record| record["message_id"].clone())
            .collect();
        let visible_characters: usize = records
            .iter()
            .map(|record| {
                record
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .chars()
                    .count()
            })
            .sum();
        Ok(json!({
            "index_id": format!("D1-{}", &sha256_hex(signature.as_bytes())[..16]),
            "level": 1,
            "conversation_id": conversation_id,
            "source_round_start": start_round,
            "source_round_end": end_round,
            "source_start": records.first().unwrap()["message_id"],
            "source_end": records.last().unwrap()["message_id"],
            "source_start_sequence": records.first().unwrap()["sequence"],
            "source_end_sequence": records.last().unwrap()["sequence"],
            "start_time": timestamps.first().unwrap().1,
            "end_time": timestamps.last().unwrap().1,
            "source_message_ids": source_message_ids,
            "source_sha256": raw_source_sha256(&records)?,
            "round_count": selected_rounds.len(),
            "visible_characters": visible_characters,
            "user_anchors": user_anchors,
            "assistant_anchors": assistant_anchors,
        }))
    }

    fn deterministic_parent_record(&self, level: u64, children: &[Value]) -> Result<Value> {
        let child_ids: Vec<String> = children
            .iter()
            .map(|child| string_field(child, "index_id").map(str::to_owned))
            .collect::<Result<Vec<_>>>()?;
        let signature = format!("children:{}", child_ids.join(","));
        let source_digests = Value::Array(
            children
                .iter()
                .map(|child| {
                    json!({
                        "index_id": child["index_id"],
                        "source_sha256": child["source_sha256"],
                    })
                })
                .collect(),
        );
        let mut user_anchors = Vec::new();
        let mut assistant_anchors = Vec::new();
        for child in children {
            for anchor in child
                .get("user_anchors")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                push_unique_value(&mut user_anchors, anchor.as_str().unwrap_or(""), 10);
            }
            for anchor in child
                .get("assistant_anchors")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                push_unique_value(&mut assistant_anchors, anchor.as_str().unwrap_or(""), 10);
            }
        }
        let first = children.first().unwrap();
        let last = children.last().unwrap();
        let round_count: u64 = children
            .iter()
            .filter_map(|v| v.get("round_count").and_then(Value::as_u64))
            .sum();
        let visible_characters: u64 = children
            .iter()
            .filter_map(|v| v.get("visible_characters").and_then(Value::as_u64))
            .sum();
        Ok(json!({
            "index_id": format!("D{level}-{}", &sha256_hex(signature.as_bytes())[..16]),
            "level": level,
            "conversation_id": first["conversation_id"],
            "child_index_ids": child_ids,
            "source_round_start": first["source_round_start"],
            "source_round_end": last["source_round_end"],
            "source_start": first["source_start"],
            "source_end": last["source_end"],
            "source_start_sequence": first["source_start_sequence"],
            "source_end_sequence": last["source_end_sequence"],
            "start_time": first["start_time"],
            "end_time": last["end_time"],
            "source_sha256": canonical_sha256(&source_digests)?,
            "round_count": round_count,
            "visible_characters": visible_characters,
            "user_anchors": user_anchors,
            "assistant_anchors": assistant_anchors,
        }))
    }

    fn refresh_deterministic_indexes(&self) -> Result<Value> {
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
        let mut level_one_by_conversation: BTreeMap<String, Vec<Value>> = BTreeMap::new();
        for (conversation_id, rounds) in grouped {
            let mut completed = Vec::new();
            for (_, mut records) in rounds {
                records.sort_by_key(|record| {
                    record.get("sequence").and_then(Value::as_u64).unwrap_or(0)
                });
                let has_user = records
                    .iter()
                    .any(|record| record.get("speaker").and_then(Value::as_str) == Some("user"));
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
            let mut bucket: Vec<Vec<Value>> = Vec::new();
            let mut bucket_characters = 0usize;
            for records in completed {
                bucket_characters += records
                    .iter()
                    .map(|record| {
                        record
                            .get("text")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .chars()
                            .count()
                    })
                    .sum::<usize>();
                bucket.push(records);
                if bucket.len() >= self.config.summaries.level_1_trigger_rounds as usize
                    || bucket_characters
                        >= self.config.summaries.level_1_trigger_characters as usize
                    || bucket_characters.div_ceil(3)
                        >= self.config.summaries.level_1_trigger_tokens as usize
                {
                    let index = self.deterministic_level_one_record(&conversation_id, &bucket)?;
                    level_one_by_conversation
                        .entry(conversation_id.clone())
                        .or_default()
                        .push(index);
                    bucket.clear();
                    bucket_characters = 0;
                }
            }
        }

        let mut levels: BTreeMap<u64, Vec<Value>> = BTreeMap::new();
        levels.insert(
            1,
            level_one_by_conversation
                .values()
                .flatten()
                .cloned()
                .collect(),
        );
        let mut current = level_one_by_conversation;
        let higher_level_trigger = self.config.summaries.higher_level_trigger_count.max(1);
        for level in 2..=self.config.summaries.maximum_summary_depth {
            let mut next: BTreeMap<String, Vec<Value>> = BTreeMap::new();
            for (conversation_id, children) in &current {
                for group in children.chunks(higher_level_trigger) {
                    if group.len() < higher_level_trigger {
                        continue;
                    }
                    next.entry(conversation_id.clone())
                        .or_default()
                        .push(self.deterministic_parent_record(level, group)?);
                }
            }
            if next.is_empty() {
                break;
            }
            levels.insert(level, next.values().flatten().cloned().collect());
            current = next;
        }

        let directory = self.root.join("indexes/deterministic");
        fs::create_dir_all(&directory)?;
        for entry in fs::read_dir(&directory)? {
            let path = entry?.path();
            if path
                .file_name()
                .and_then(|v| v.to_str())
                .is_some_and(|name| name.starts_with("level-") && name.ends_with(".jsonl"))
            {
                fs::remove_file(path)?;
            }
        }
        let mut timeline = String::from("# Deterministic Index Timeline\n\n");
        for (level, records) in &levels {
            atomic_write_jsonl(&directory.join(format!("level-{level}.jsonl")), records)?;
            for record in records {
                timeline.push_str(&format!(
                    "## {}\n\n- Level: `{level}`\n- Conversation: `{}`\n- Time range: `{}` to `{}`\n- Rounds: `{}` through `{}`\n- Visible characters: `{}`\n- Source: `{}` through `{}`\n\n",
                    string_field(record, "index_id")?, string_field(record, "conversation_id")?,
                    string_field(record, "start_time")?, string_field(record, "end_time")?,
                    record["source_round_start"], record["source_round_end"], record["visible_characters"],
                    string_field(record, "source_start")?, string_field(record, "source_end")?,
                ));
            }
        }
        atomic_write(&directory.join("timeline.md"), timeline.as_bytes())?;

        let by_conversation_root = self.root.join("indexes/by-conversation");
        if by_conversation_root.exists() {
            for entry in fs::read_dir(&by_conversation_root)? {
                let path = entry?.path();
                if !path.is_dir() {
                    continue;
                }
                for child in fs::read_dir(path)? {
                    let child_path = child?.path();
                    if child_path
                        .file_name()
                        .and_then(|v| v.to_str())
                        .is_some_and(|name| {
                            name.starts_with("deterministic-level-") && name.ends_with(".jsonl")
                        })
                    {
                        fs::remove_file(child_path)?;
                    }
                }
            }
        }
        let conversation_ids: BTreeSet<String> = levels
            .values()
            .flatten()
            .filter_map(|record| {
                record
                    .get("conversation_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            })
            .collect();
        for conversation_id in conversation_ids {
            let conversation_directory = self.ensure_conversation_indexes(&conversation_id)?;
            for (level, records) in &levels {
                let selected: Vec<Value> = records
                    .iter()
                    .filter(|record| {
                        record.get("conversation_id").and_then(Value::as_str)
                            == Some(conversation_id.as_str())
                    })
                    .cloned()
                    .collect();
                if !selected.is_empty() {
                    atomic_write_jsonl(
                        &conversation_directory.join(format!("deterministic-level-{level}.jsonl")),
                        &selected,
                    )?;
                }
            }
        }

        let mut counts = Map::new();
        for (level, records) in &levels {
            counts.insert(level.to_string(), json!(records.len()));
        }
        Ok(json!({
            "levels": counts,
            "level_1_round_trigger": self.config.summaries.level_1_trigger_rounds,
            "level_1_character_trigger": self.config.summaries.level_1_trigger_characters,
            "level_1_token_trigger": self.config.summaries.level_1_trigger_tokens,
        }))
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
                let mut selected_rounds = Vec::new();
                let mut selected_characters = 0usize;
                for records in eligible_rounds {
                    selected_characters += records
                        .iter()
                        .map(|record| {
                            record
                                .get("text")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .chars()
                                .count()
                        })
                        .sum::<usize>();
                    selected_rounds.push(records);
                    if selected_rounds.len()
                        >= self.config.summaries.level_1_trigger_rounds as usize
                        || selected_characters
                            >= self.config.summaries.level_1_trigger_characters as usize
                        || selected_characters.div_ceil(3)
                            >= self.config.summaries.level_1_trigger_tokens as usize
                    {
                        break;
                    }
                }
                if selected_rounds.is_empty()
                    || (selected_rounds.len()
                        < self.config.summaries.level_1_trigger_rounds as usize
                        && selected_characters
                            < self.config.summaries.level_1_trigger_characters as usize)
                        && selected_characters.div_ceil(3)
                            < self.config.summaries.level_1_trigger_tokens as usize
                {
                    continue;
                }
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
                "required_result_keys": ["topics", "established_conclusions", "open_questions", "concepts", "policy_events"],
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

    fn record_backup_debt(&self, reason: &str, metadata: Value) -> Result<Option<PathBuf>> {
        if !self.config.backup.enabled {
            return Ok(None);
        }
        if self.config.backup.directory.trim().is_empty() {
            bail!("backup.enabled requires backup.directory");
        }
        let path = self.root.join("pending/backup-debt.json");
        let now = now_iso();
        let existing = if path.exists() {
            read_json(&path)?
        } else {
            json!({})
        };
        let first_recorded_at = existing
            .get("first_recorded_at")
            .and_then(Value::as_str)
            .unwrap_or(&now)
            .to_owned();
        let mutation_count = existing
            .get("mutation_count")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            + 1;
        let mut reason_counts = existing
            .get("reason_counts")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let reason_count = reason_counts
            .get(reason)
            .and_then(Value::as_u64)
            .unwrap_or(0)
            + 1;
        reason_counts.insert(reason.to_owned(), json!(reason_count));
        atomic_write_json(
            &path,
            &json!({
                "format_version": 1,
                "status": "pending",
                "first_recorded_at": first_recorded_at,
                "updated_at": now,
                "mutation_count": mutation_count,
                "reason_counts": reason_counts,
                "latest_reason": reason,
                "latest_metadata": metadata,
            }),
        )?;
        Ok(Some(path))
    }

    fn sync_batch_with_semantic_worker(
        &self,
        paths: Vec<PathBuf>,
        run_semantic_worker: bool,
    ) -> Result<Value> {
        self.repair_native_recovery_debt()?;
        self.write_coverage_status(&paths)?;
        let coverage_paths = paths.clone();
        let completed_before = self.lock("archive.lock", || {
            Ok(completed_round_total(&self.load_state()?))
        })?;
        let mut result = json!({
            "status": "synced",
            "sessions": [],
            "session_count": 0,
            "imported_messages": 0,
            "duplicate_messages": 0,
            "repaired_transcripts": 0,
            "token_usage_changed_events": 0,
            "created_summary_job": null,
            "deterministic_indexes": null,
            "backup": null,
            "backup_debt": null,
        });
        for path in paths {
            let mut accumulated_session: Option<Value> = None;
            loop {
                let prepared = self.prepare_sync_file(&path)?;
                let chunk =
                    self.lock("archive.lock", || self.sync_batch_unlocked(vec![prepared]))?;
                for field in [
                    "imported_messages",
                    "duplicate_messages",
                    "repaired_transcripts",
                    "token_usage_changed_events",
                ] {
                    result[field] = json!(
                        result.get(field).and_then(Value::as_u64).unwrap_or(0)
                            + chunk.get(field).and_then(Value::as_u64).unwrap_or(0)
                    );
                }
                if let Some(session) = chunk
                    .get("sessions")
                    .and_then(Value::as_array)
                    .and_then(|sessions| sessions.first())
                {
                    if let Some(accumulated) = accumulated_session.as_mut() {
                        for field in [
                            "visible_events",
                            "imported_messages",
                            "duplicate_messages",
                            "repaired_transcripts",
                            "token_usage_changed_events",
                        ] {
                            accumulated[field] = json!(
                                accumulated.get(field).and_then(Value::as_u64).unwrap_or(0)
                                    + session.get(field).and_then(Value::as_u64).unwrap_or(0)
                            );
                        }
                        for field in [
                            "last_line",
                            "reported_total_tokens",
                            "token_usage_ledger",
                            "excluded_reason",
                            "committed_byte_offset",
                            "observed_source_size",
                            "complete",
                            "caught_up",
                        ] {
                            accumulated[field] = session[field].clone();
                        }
                    } else {
                        accumulated_session = Some(session.clone());
                    }
                }
                for field in [
                    "created_summary_job",
                    "deterministic_indexes",
                    "backup_debt",
                ] {
                    if chunk.get(field).is_some_and(|value| !value.is_null()) {
                        result[field] = chunk[field].clone();
                    }
                }
                let caught_up = chunk
                    .get("sessions")
                    .and_then(Value::as_array)
                    .and_then(|sessions| sessions.first())
                    .and_then(|session| session.get("caught_up"))
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                if caught_up {
                    break;
                }
            }
            if let Some(session) = accumulated_session {
                result["sessions"]
                    .as_array_mut()
                    .expect("sessions is an array")
                    .push(session);
            }
        }
        result["session_count"] = json!(coverage_paths.len());
        let imported = result
            .get("imported_messages")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let finalized = self.lock("archive.lock", || {
            let deterministic_indexes = if imported > 0 {
                Some(self.refresh_deterministic_indexes()?)
            } else {
                None
            };
            let completed_after = completed_round_total(&self.load_state()?);
            let created_job = if imported > 0
                && completed_after > completed_before
                && self.config.summaries.automatic_semantic_jobs
            {
                self.maybe_create_level_one_job()?
            } else {
                None
            };
            Ok(json!({
                "created_summary_job": created_job.map(|path| path.to_string_lossy().into_owned()),
                "deterministic_indexes": deterministic_indexes,
            }))
        })?;
        for field in ["created_summary_job", "deterministic_indexes"] {
            result[field] = finalized[field].clone();
        }
        self.write_coverage_status(&coverage_paths)?;
        if run_semantic_worker
            && self.config.ai_summary.enabled
            && let Some(job) = result.get("created_summary_job").and_then(Value::as_str)
        {
            result["semantic_worker"] = self.run_one_shot_summary(Path::new(job));
        }
        append_jsonl(
            &self.root.join("dashboard/events.jsonl"),
            &json!({
                "event_id": chrono::Utc::now().timestamp_millis(),
                "created_at": now_iso(),
                "kind": "archive-updated",
                "imported_messages": result.get("imported_messages").cloned().unwrap_or(json!(0)),
                "duplicate_messages": result.get("duplicate_messages").cloned().unwrap_or(json!(0)),
                "token_usage_changed_events": result
                    .get("token_usage_changed_events")
                    .cloned()
                    .unwrap_or(json!(0)),
            }),
        )?;
        Ok(result)
    }

    fn sync_batch(&self, paths: Vec<PathBuf>) -> Result<Value> {
        self.sync_batch_with_semantic_worker(paths, true)
    }

    fn sync_startup_batch(&self, paths: Vec<PathBuf>) -> Result<Value> {
        self.sync_batch_with_semantic_worker(paths, false)
    }

    fn repair_native_recovery_debt(&self) -> Result<()> {
        let marker = self.root.join("pending/native-recovery-debt.json");
        if !marker.exists() {
            return Ok(());
        }
        let configured_memory_cli = self
            .config_path
            .parent()
            .unwrap_or(Path::new("."))
            .join("scripts/memory_cli.py");
        let bundled_memory_cli = std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(Path::to_path_buf))
            .and_then(|path| path.parent().map(Path::to_path_buf))
            .map(|path| path.join("scripts/memory_cli.py"));
        let memory_cli = if configured_memory_cli.is_file() {
            configured_memory_cli
        } else if let Some(path) = bundled_memory_cli.filter(|path| path.is_file()) {
            path
        } else {
            configured_memory_cli
        };
        if !memory_cli.is_file() {
            bail!(
                "native recovery requires the installed memory CLI: {}",
                memory_cli.display()
            );
        }
        let python_path = self
            .python_executable
            .as_ref()
            .map(|path| path.to_string_lossy().into_owned())
            .or_else(|| std::env::var("MEMORY_WUXIAN_PYTHON").ok())
            .unwrap_or_else(|| {
                if cfg!(windows) {
                    self.config
                        .ai_summary
                        .python_path_windows
                        .as_ref()
                        .unwrap_or(&self.config.ai_summary.python_path)
                        .clone()
                } else {
                    self.config.ai_summary.python_path.clone()
                }
            });
        let mut command = Command::new(python_path);
        command
            .arg(memory_cli)
            .arg("--root")
            .arg(&self.root)
            .arg("--config")
            .arg(&self.config_path)
            .arg("heartbeat")
            .arg("--no-create-jobs")
            .arg("--repair");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let output = command
            .output()
            .context("run deterministic native recovery before resuming collection")?;
        if !output.status.success() {
            bail!(
                "native recovery failed before resume: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        let recovery: Value = serde_json::from_slice(&output.stdout)
            .context("parse deterministic native recovery result")?;
        if recovery.get("status").and_then(Value::as_str) != Some("ok") {
            bail!("native recovery did not verify the archive: {recovery}");
        }
        fs::remove_file(&marker)?;
        Ok(())
    }

    fn run_one_shot_summary(&self, job_path: &Path) -> Value {
        let dispatcher_path = PathBuf::from(&self.config.ai_summary.dispatcher_path);
        let dispatcher_path = if dispatcher_path.is_absolute() {
            dispatcher_path
        } else {
            self.config_path
                .parent()
                .unwrap_or(Path::new("."))
                .join(dispatcher_path)
        };
        let python_path = self
            .python_executable
            .as_ref()
            .map(|path| path.to_string_lossy().into_owned())
            .or_else(|| std::env::var("MEMORY_WUXIAN_PYTHON").ok())
            .unwrap_or_else(|| {
                if cfg!(windows) {
                    self.config
                        .ai_summary
                        .python_path_windows
                        .as_ref()
                        .unwrap_or(&self.config.ai_summary.python_path)
                        .clone()
                } else {
                    self.config.ai_summary.python_path.clone()
                }
            });
        let mut command = Command::new(python_path);
        command
            .arg(dispatcher_path)
            .arg("--root")
            .arg(&self.root)
            .arg("--config")
            .arg(&self.config_path)
            .arg("--job")
            .arg(job_path)
            .arg("--no-backup");
        if let Some(codex_cli) = &self.codex_cli {
            command.env("MEMORY_WUXIAN_CODEX", codex_cli);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        match command.output() {
            Ok(output) if output.status.success() => json!({
                "status": "completed",
                "output": String::from_utf8_lossy(&output.stdout).trim(),
            }),
            Ok(output) => json!({
                "status": "failed",
                "exit_code": output.status.code(),
                "error": String::from_utf8_lossy(&output.stderr).trim(),
            }),
            Err(error) => json!({
                "status": "failed",
                "error": error.to_string(),
            }),
        }
    }

    fn sync_batch_unlocked(&self, prepared_files: Vec<PreparedSync>) -> Result<Value> {
        let mut files = Vec::new();
        let mut imported = 0;
        let mut duplicates = 0;
        let mut repaired = 0;
        let mut token_usage_changed_events = 0;
        for prepared in prepared_files {
            let recovery_debt = self.root.join("pending/native-recovery-debt.json");
            let inherited_recovery_debt = recovery_debt.exists();
            if !inherited_recovery_debt {
                atomic_write_json(
                    &recovery_debt,
                    &json!({
                        "format": "memory-wuxian-native-recovery-debt-v1",
                        "source_path": portable_path(&prepared.source_path),
                        "last_committed_line": prepared.last_line,
                        "created_at": now_iso(),
                    }),
                )?;
            }
            let result = self.sync_prepared_file(prepared)?;
            if !inherited_recovery_debt {
                fs::remove_file(&recovery_debt)?;
            }
            imported += result.imported_messages;
            duplicates += result.duplicate_messages;
            repaired += result.repaired_transcripts;
            token_usage_changed_events += result.token_usage_changed_events;
            files.push(json!({
                "session_id": result.session_id,
                "source_path": portable_path(&result.source_path),
                "last_line": result.last_line,
                "visible_events": result.visible_events,
                "imported_messages": result.imported_messages,
                "duplicate_messages": result.duplicate_messages,
                "repaired_transcripts": result.repaired_transcripts,
                "token_usage_changed_events": result.token_usage_changed_events,
                "reported_total_tokens": result.reported_total_tokens,
                "token_usage_ledger": result
                    .token_usage_ledger
                    .as_ref()
                    .map(|path| portable_path(path)),
                "excluded_reason": result.excluded_reason,
                "committed_byte_offset": result.committed_byte_offset,
                "observed_source_size": result.observed_source_size,
                "complete": result.complete,
                "caught_up": result.caught_up,
            }));
        }
        let backup_debt = if imported > 0 || repaired > 0 || token_usage_changed_events > 0 {
            self.record_backup_debt(
                "codex-native-sync",
                json!({
                    "session_count": files.len(),
                    "imported_messages": imported,
                    "duplicate_messages": duplicates,
                    "repaired_transcripts": repaired,
                    "token_usage_changed_events": token_usage_changed_events,
                    "bounded_batch": true,
                }),
            )?
        } else {
            None
        };
        Ok(json!({
            "status": "synced", "sessions": files, "session_count": files.len(),
            "imported_messages": imported, "duplicate_messages": duplicates,
            "repaired_transcripts": repaired,
            "token_usage_changed_events": token_usage_changed_events,
            "created_summary_job": Value::Null,
            "deterministic_indexes": Value::Null,
            "backup": Value::Null,
            "backup_debt": backup_debt.map(|path| path.to_string_lossy().into_owned()),
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

fn completed_round_total(state: &Value) -> u64 {
    state
        .get("completed_rounds")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        + state
            .get("completed_rounds_out_of_order")
            .and_then(Value::as_array)
            .map(|values| values.len() as u64)
            .unwrap_or(0)
}

fn compact_json(value: &Value) -> Result<String> {
    Ok(serde_json::to_string(value)?)
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn deterministic_excerpt(text: &str, limit: usize) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(limit)
        .collect()
}

fn push_unique_value(values: &mut Vec<String>, value: &str, limit: usize) {
    if !value.is_empty() && values.len() < limit && !values.iter().any(|item| item == value) {
        values.push(value.to_owned());
    }
}

fn push_unique_excerpt(values: &mut Vec<String>, text: &str, limit: usize) {
    let excerpt = deterministic_excerpt(text, 240);
    push_unique_value(values, &excerpt, limit);
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

fn atomic_write_jsonl(path: &Path, values: &[Value]) -> Result<()> {
    let mut bytes = Vec::new();
    for value in values {
        bytes.extend(serde_json::to_vec(value)?);
        bytes.push(b'\n');
    }
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

fn rollout_session_id(path: &Path) -> Option<String> {
    let name = path.file_name()?.to_str()?;
    Regex::new(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
        .expect("valid session ID regex")
        .captures(name)
        .and_then(|captures| captures.get(1))
        .map(|value| value.as_str().to_owned())
}

fn resolve_rollout_session_id(path: &Path) -> Result<Option<String>> {
    if let Some(session_id) = rollout_session_id(path) {
        return Ok(Some(session_id));
    }
    let mut reader = BufReader::new(File::open(path)?);
    let mut buffer = Vec::new();
    for _ in 0..32 {
        if read_complete_line(&mut reader, &mut buffer)?.is_none() {
            break;
        }
        let bytes = buffer.strip_suffix(b"\n").unwrap_or(&buffer);
        let decoded = std::str::from_utf8(bytes)?;
        let text = decoded.strip_suffix('\r').unwrap_or(decoded);
        if let Ok(event) = serde_json::from_str::<Value>(text)
            && let Some((session_id, _)) = session_metadata(&event)
        {
            return Ok(Some(session_id));
        }
    }
    Ok(None)
}

fn cursor_covers_source(cursor: &Value, path: &Path, metadata: &fs::Metadata) -> bool {
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

fn recent_rollouts(
    root: &Path,
    since: Option<DateTime<FixedOffset>>,
    archive_root: &Path,
) -> Result<Vec<PathBuf>> {
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
        let path = entry.path().canonicalize()?;
        let metadata = entry.metadata()?;
        if let Some(since) = since {
            let modified: DateTime<Utc> = metadata.modified()?.into();
            if modified.timestamp() < since.timestamp() {
                let covered = rollout_session_id(&path)
                    .map(|session_id| {
                        archive_root
                            .join("imports/codex")
                            .join(format!("{session_id}.json"))
                    })
                    .filter(|cursor_path| cursor_path.exists())
                    .map(|cursor_path| read_json(&cursor_path))
                    .transpose()?
                    .is_some_and(|cursor| cursor_covers_source(&cursor, &path, &metadata));
                if covered {
                    continue;
                }
            }
        }
        paths.push(path);
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
    archive_root: &Path,
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
        paths.extend(recent_rollouts(sessions_root, since, archive_root)?);
    }
    paths.sort();
    paths.dedup();
    Ok(paths)
}

fn emit(value: &Value) -> Result<()> {
    println!("{}", serde_json::to_string(value)?);
    Ok(())
}

fn sync_and_emit(store: &Store, paths: Vec<PathBuf>) -> bool {
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
                    > 0
                || result
                    .get("token_usage_changed_events")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    > 0;
            if changed && let Err(error) = emit(&result) {
                eprintln!("output error: {error:#}");
            }
            true
        }
        Err(error) => {
            eprintln!("sync error: {error:#}");
            false
        }
    }
}

#[cfg(target_os = "macos")]
struct KqueueWatcher {
    queue: File,
    _watched: Vec<File>,
}

#[cfg(target_os = "macos")]
const MAX_KQUEUE_ROLLOUT_WATCHES: usize = 64;

#[cfg(target_os = "macos")]
impl KqueueWatcher {
    fn new(root: &Path, _since: Option<DateTime<FixedOffset>>) -> Result<Self> {
        let queue_fd = unsafe { libc::kqueue() };
        if queue_fd < 0 {
            return Err(std::io::Error::last_os_error()).context("create kqueue");
        }
        let queue = unsafe { File::from_raw_fd(queue_fd) };
        let mut directories = Vec::new();
        let mut rollout_files = Vec::new();
        for entry in WalkDir::new(root).follow_links(false) {
            let entry = entry?;
            if entry.file_type().is_dir() {
                directories.push(entry.path().to_path_buf());
                continue;
            }
            if !entry.file_type().is_file()
                || !entry
                    .file_name()
                    .to_str()
                    .is_some_and(|name| name.starts_with("rollout-") && name.ends_with(".jsonl"))
            {
                continue;
            }
            let modified = entry.metadata()?.modified()?;
            rollout_files.push((modified, entry.path().to_path_buf()));
        }
        rollout_files
            .sort_by(|left, right| right.0.cmp(&left.0).then_with(|| right.1.cmp(&left.1)));
        rollout_files.truncate(MAX_KQUEUE_ROLLOUT_WATCHES);

        let mut watched = Vec::new();
        for watch_path in directories
            .into_iter()
            .chain(rollout_files.into_iter().map(|(_, path)| path))
        {
            let path = CString::new(watch_path.as_os_str().as_bytes())?;
            let fd = unsafe { libc::open(path.as_ptr(), libc::O_EVTONLY | libc::O_CLOEXEC) };
            if fd < 0 {
                return Err(std::io::Error::last_os_error())
                    .with_context(|| format!("watch {}", watch_path.display()));
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
                    .with_context(|| format!("register {}", watch_path.display()));
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

    fn wait(&self, timeout: Duration) -> Result<bool> {
        loop {
            let mut event: libc::kevent = unsafe { std::mem::zeroed() };
            let timeout = libc::timespec {
                tv_sec: timeout.as_secs() as libc::time_t,
                tv_nsec: timeout.subsec_nanos() as libc::c_long,
            };
            let result = unsafe {
                libc::kevent(
                    self.queue.as_raw_fd(),
                    std::ptr::null(),
                    0,
                    &mut event,
                    1,
                    &timeout,
                )
            };
            if result > 0 {
                return Ok(true);
            }
            if result == 0 {
                return Ok(false);
            }
            let error = std::io::Error::last_os_error();
            if error.kind() != std::io::ErrorKind::Interrupted {
                return Err(error).context("wait for kqueue event");
            }
        }
    }
}

fn rollout_stamps(paths: &[PathBuf]) -> Result<HashMap<PathBuf, (u64, SystemTime)>> {
    paths
        .iter()
        .map(|path| {
            let metadata = fs::metadata(path)?;
            Ok((path.clone(), (metadata.len(), metadata.modified()?)))
        })
        .collect()
}

const ACTIVE_FALLBACK: Duration = Duration::from_secs(5);
const IDLE_FALLBACK: Duration = Duration::from_secs(30);
const DEEP_IDLE_FALLBACK: Duration = Duration::from_secs(300);
const IDLE_AFTER: Duration = Duration::from_secs(120);
const DEEP_IDLE_AFTER: Duration = Duration::from_secs(900);

fn adaptive_fallback(idle_for: Duration) -> Duration {
    if idle_for >= DEEP_IDLE_AFTER {
        DEEP_IDLE_FALLBACK
    } else if idle_for >= IDLE_AFTER {
        IDLE_FALLBACK
    } else {
        ACTIVE_FALLBACK
    }
}

struct CollectorTelemetry {
    ready: bool,
    last_mode: &'static str,
    last_file_event: Option<String>,
    last_archive_update: Option<String>,
    source_watermark: Option<String>,
    archive_watermark: Option<String>,
    wakeups: VecDeque<(std::time::Instant, String)>,
}

impl CollectorTelemetry {
    fn new() -> Self {
        Self {
            ready: false,
            last_mode: "active",
            last_file_event: None,
            last_archive_update: None,
            source_watermark: None,
            archive_watermark: None,
            wakeups: VecDeque::new(),
        }
    }

    fn mode(interval: Duration) -> &'static str {
        if interval == DEEP_IDLE_FALLBACK {
            "deep-idle"
        } else if interval == IDLE_FALLBACK {
            "idle"
        } else {
            "active"
        }
    }

    fn record_event(&mut self) {
        let now = Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true);
        self.last_file_event = Some(now.clone());
        self.wakeups.push_back((std::time::Instant::now(), now));
    }

    fn record_source_watermark(&mut self, watermark: Option<String>) {
        self.source_watermark = watermark;
    }

    fn record_archive(&mut self, watermark: Option<String>) {
        self.last_archive_update = Some(Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true));
        self.archive_watermark = watermark;
    }

    fn mark_ready(&mut self) {
        self.ready = true;
    }

    fn write(&mut self, store: &Store, interval: Duration) -> Result<()> {
        while self
            .wakeups
            .front()
            .is_some_and(|(instant, _)| instant.elapsed() > Duration::from_secs(3600))
        {
            self.wakeups.pop_front();
        }
        self.last_mode = Self::mode(interval);
        atomic_write_json(
            &store.root.join("imports/codex/collector-telemetry.json"),
            &json!({
                "format_version": 2,
                "pid": std::process::id(),
                "phase": if self.ready { "ready" } else { "starting" },
                "ready": self.ready,
                "mode": self.last_mode,
                "fallback_interval_seconds": interval.as_secs(),
                "last_file_event": self.last_file_event,
                "last_archive_update": self.last_archive_update,
                "source_watermark": self.source_watermark,
                "archive_watermark": self.archive_watermark,
                "wakeups_last_hour": self.wakeups.len(),
                "recent_wakeups": self.wakeups.iter().map(|(_, timestamp)| timestamp).collect::<Vec<_>>(),
                "updated_at": Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true),
            }),
        )
    }
}

fn newest_source_watermark(stamps: &HashMap<PathBuf, (u64, SystemTime)>) -> Option<String> {
    stamps
        .values()
        .map(|(_, modified)| *modified)
        .max()
        .map(|modified| DateTime::<Utc>::from(modified).to_rfc3339_opts(SecondsFormat::Secs, true))
}

#[cfg(target_os = "macos")]
fn run_event_loop(
    store: &Store,
    sessions_root: &Path,
    since: Option<DateTime<FixedOffset>>,
    debounce_ms: u64,
) -> Result<()> {
    let mut watcher = KqueueWatcher::new(sessions_root, since)?;
    let initial_paths = recent_rollouts(sessions_root, since, &store.root)?;
    let mut known_stamps = rollout_stamps(&initial_paths)?;
    let mut last_activity = std::time::Instant::now();
    let mut telemetry = CollectorTelemetry::new();
    let initial_watermark = newest_source_watermark(&known_stamps);
    telemetry.record_source_watermark(initial_watermark.clone());
    telemetry.record_archive(initial_watermark);
    telemetry.mark_ready();
    telemetry.write(store, ACTIVE_FALLBACK)?;
    eprintln!(
        "memory-wuxian-collector ready (kqueue with adaptive 5s/30s/5m metadata fallback): {}",
        sessions_root.display()
    );
    loop {
        let interval = adaptive_fallback(last_activity.elapsed());
        if CollectorTelemetry::mode(interval) != telemetry.last_mode {
            telemetry.write(store, interval)?;
        }
        let received_event = watcher.wait(interval)?;
        if received_event {
            telemetry.record_event();
            std::thread::sleep(Duration::from_millis(debounce_ms));
        }
        let current_paths = recent_rollouts(sessions_root, since, &store.root)?;
        let current_stamps = rollout_stamps(&current_paths)?;
        let source_watermark = newest_source_watermark(&current_stamps);
        telemetry.record_source_watermark(source_watermark.clone());
        let changed_paths: Vec<PathBuf> = current_paths
            .iter()
            .filter(|path| known_stamps.get(*path) != current_stamps.get(*path))
            .cloned()
            .collect();
        let sync_succeeded = changed_paths.is_empty() || sync_and_emit(store, changed_paths);
        if sync_succeeded && known_stamps != current_stamps {
            telemetry.record_archive(source_watermark);
        }
        if received_event || known_stamps != current_stamps {
            last_activity = std::time::Instant::now();
            telemetry.write(store, ACTIVE_FALLBACK)?;
        }
        if received_event || known_stamps.len() != current_stamps.len() {
            watcher = KqueueWatcher::new(sessions_root, since)?;
        }
        if sync_succeeded {
            known_stamps = current_stamps;
        }
        telemetry.write(store, interval)?;
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
    let initial_paths = recent_rollouts(sessions_root, since, &store.root)?;
    let mut known_stamps = rollout_stamps(&initial_paths)?;
    let mut last_activity = std::time::Instant::now();
    let mut telemetry = CollectorTelemetry::new();
    let initial_watermark = newest_source_watermark(&known_stamps);
    telemetry.record_source_watermark(initial_watermark.clone());
    telemetry.record_archive(initial_watermark);
    telemetry.mark_ready();
    telemetry.write(store, ACTIVE_FALLBACK)?;
    eprintln!(
        "memory-wuxian-collector ready (native watcher with adaptive 5s/30s/5m metadata fallback): {}",
        sessions_root.display()
    );
    loop {
        let interval = adaptive_fallback(last_activity.elapsed());
        if CollectorTelemetry::mode(interval) != telemetry.last_mode {
            telemetry.write(store, interval)?;
        }
        let first = match receiver.recv_timeout(interval) {
            Ok(value) => Some(value),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => bail!("filesystem watcher stopped"),
        };
        let received_event = first.is_some();
        if received_event {
            telemetry.record_event();
        }
        let mut candidates = BTreeSet::new();
        if let Some(first) = first {
            match first {
                Ok(event) => {
                    candidates.extend(event_rollouts(event, sessions_root, since, &store.root)?)
                }
                Err(error) => eprintln!("watch error: {error}"),
            }
            loop {
                match receiver.recv_timeout(Duration::from_millis(debounce_ms)) {
                    Ok(Ok(event)) => {
                        candidates.extend(event_rollouts(event, sessions_root, since, &store.root)?)
                    }
                    Ok(Err(error)) => eprintln!("watch error: {error}"),
                    Err(RecvTimeoutError::Timeout) => break,
                    Err(RecvTimeoutError::Disconnected) => bail!("filesystem watcher stopped"),
                }
            }
        }

        let current_paths = recent_rollouts(sessions_root, since, &store.root)?;
        let current_stamps = rollout_stamps(&current_paths)?;
        let source_watermark = newest_source_watermark(&current_stamps);
        telemetry.record_source_watermark(source_watermark.clone());
        candidates.extend(
            current_paths
                .iter()
                .filter(|path| known_stamps.get(*path) != current_stamps.get(*path))
                .cloned(),
        );
        let sync_succeeded =
            candidates.is_empty() || sync_and_emit(store, candidates.into_iter().collect());
        if sync_succeeded && known_stamps != current_stamps {
            telemetry.record_archive(source_watermark);
        }
        if received_event || known_stamps != current_stamps {
            last_activity = std::time::Instant::now();
            telemetry.write(store, ACTIVE_FALLBACK)?;
        }
        if sync_succeeded {
            known_stamps = current_stamps;
        }
        telemetry.write(store, interval)?;
    }
}

#[cfg(test)]
mod adaptive_fallback_tests {
    use super::*;

    #[test]
    fn backs_off_after_idle_periods() {
        assert_eq!(adaptive_fallback(Duration::from_secs(0)), ACTIVE_FALLBACK);
        assert_eq!(adaptive_fallback(IDLE_AFTER), IDLE_FALLBACK);
        assert_eq!(adaptive_fallback(DEEP_IDLE_AFTER), DEEP_IDLE_FALLBACK);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn kqueue_rollout_watch_count_is_bounded() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let day = temporary.path().join("2026/07/23");
        fs::create_dir_all(&day)?;
        for index in 0..100 {
            fs::write(
                day.join(format!("rollout-{index:03}.jsonl")),
                b"{\"type\":\"session_meta\"}\n",
            )?;
        }

        let watcher = KqueueWatcher::new(temporary.path(), None)?;
        let directory_count = 4;
        assert_eq!(
            watcher._watched.len(),
            directory_count + MAX_KQUEUE_ROLLOUT_WATCHES
        );
        Ok(())
    }
}

#[cfg(test)]
mod rollout_stream_tests {
    use super::*;

    const SESSION_ID: &str = "019fb8f2-9a67-7b03-9474-6f92cd6b21a7";

    fn rollout_path(root: &Path) -> PathBuf {
        root.join(format!("rollout-2026-08-01T01-12-15-{SESSION_ID}.jsonl"))
    }

    fn session_meta(source: Value) -> String {
        serde_json::to_string(&json!({
            "type": "session_meta",
            "payload": {"id": SESSION_ID, "source": source}
        }))
        .unwrap()
    }

    fn user_event(message: &str) -> String {
        serde_json::to_string(&json!({
            "timestamp": "2026-08-01T00:00:00Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": message}
        }))
        .unwrap()
    }

    #[test]
    fn older_than_since_without_cursor_is_discovered() -> Result<()> {
        let sessions = tempfile::tempdir()?;
        let archive = tempfile::tempdir()?;
        let path = rollout_path(sessions.path());
        fs::write(&path, format!("{}\n", session_meta(json!("user"))))?;
        let since = DateTime::parse_from_rfc3339("2999-01-01T00:00:00+00:00")?;

        let discovered = recent_rollouts(sessions.path(), Some(since), archive.path())?;
        assert_eq!(discovered, vec![path.canonicalize()?]);

        let canonical = path.canonicalize()?;
        let metadata = fs::metadata(&canonical)?;
        let size = metadata.len();
        let modified: DateTime<Utc> = metadata.modified()?.into();
        let cursor_dir = archive.path().join("imports/codex");
        fs::create_dir_all(&cursor_dir)?;
        atomic_write_json(
            &cursor_dir.join(format!("{SESSION_ID}.json")),
            &json!({
                "format_version": 1,
                "session_id": SESSION_ID,
                "source_path": portable_path(&canonical),
                "source_size": size,
                "committed_byte_offset": size,
                "observed_source_size": size,
                "complete": true,
                "source_mtime": modified.to_rfc3339(),
            }),
        )?;
        assert!(recent_rollouts(sessions.path(), Some(since), archive.path())?.is_empty());
        atomic_write_json(
            &cursor_dir.join(format!("{SESSION_ID}.json")),
            &json!({
                "format_version": 1,
                "session_id": SESSION_ID,
                "source_path": portable_path(&canonical),
                "source_size": size,
                "source_mtime": modified.to_rfc3339(),
            }),
        )?;
        assert!(recent_rollouts(sessions.path(), Some(since), archive.path())?.is_empty());
        Ok(())
    }

    #[test]
    fn utf8_crosses_reader_buffer_without_text_changes() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let path = rollout_path(temporary.path());
        let message = format!("{}円¥中文🙂終", "あ".repeat(10_000));
        fs::write(
            &path,
            format!(
                "{}\n{}\n",
                session_meta(json!("user")),
                user_event(&message)
            ),
        )?;
        let batch = read_rollout_batch(&path, 0, None)?;
        assert!(batch.complete);
        assert_eq!(batch.lines.len(), 1);
        let event: Value = serde_json::from_str(&batch.lines[0].text)?;
        assert_eq!(
            event.pointer("/payload/message").and_then(Value::as_str),
            Some(message.as_str())
        );
        Ok(())
    }

    #[test]
    fn trailing_partial_line_is_not_committed_and_resumes() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let path = rollout_path(temporary.path());
        let first = user_event("first");
        let second = user_event("second");
        let split = second.len() / 2;
        fs::write(
            &path,
            format!(
                "{}\n{}\n{}",
                session_meta(json!("user")),
                first,
                &second[..split]
            ),
        )?;
        let first_batch = read_rollout_batch(&path, 0, None)?;
        assert!(!first_batch.complete);
        assert!(first_batch.caught_up);
        assert_eq!(first_batch.lines.len(), 1);
        let committed = first_batch.committed_byte_offset;
        let committed_line = first_batch.lines.last().unwrap().number;

        let mut file = OpenOptions::new().append(true).open(&path)?;
        writeln!(file, "{}", &second[split..])?;
        let resumed = read_rollout_batch(&path, committed_line, Some(committed))?;
        assert!(resumed.complete);
        assert_eq!(resumed.lines.len(), 1);
        assert_eq!(resumed.lines[0].number, committed_line + 1);
        assert_eq!(resumed.lines[0].text, second);
        Ok(())
    }

    #[test]
    fn bounded_batch_resumes_at_exact_line_and_offset() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let path = rollout_path(temporary.path());
        let mut content = format!("{}\n", session_meta(json!("user")));
        for index in 0..(ROLLOUT_BATCH_MAX_LINES + 9) {
            content.push_str(&user_event(&format!("message-{index}")));
            content.push('\n');
        }
        fs::write(&path, content)?;
        let first = read_rollout_batch(&path, 0, None)?;
        assert_eq!(first.lines.len(), ROLLOUT_BATCH_MAX_LINES);
        assert!(!first.caught_up);
        let first_line = first.lines.last().unwrap().number;
        let second = read_rollout_batch(&path, first_line, Some(first.committed_byte_offset))?;
        assert_eq!(second.lines.first().unwrap().number, first_line + 1);
        assert_eq!(second.lines.len(), 9);
        assert!(second.complete);
        Ok(())
    }

    #[test]
    fn subagent_and_exec_metadata_remain_excluded() -> Result<()> {
        for (source, expected) in [
            (json!({"subagent": {"id": "worker"}}), "subagent-session"),
            (json!("exec"), "exec-session"),
        ] {
            let temporary = tempfile::tempdir()?;
            let path = rollout_path(temporary.path());
            fs::write(&path, format!("{}\n", session_meta(source)))?;
            let batch = read_rollout_batch(&path, 0, None)?;
            assert_eq!(batch.excluded_reason.as_deref(), Some(expected));
        }
        Ok(())
    }

    #[test]
    fn giant_irrelevant_payload_uses_the_lightweight_envelope() -> Result<()> {
        let text = format!(
            "{{\"timestamp\":\"2026-08-01T00:00:00Z\",\"type\":\"response_item\",\"payload\":{{\"type\":\"custom_tool_call_output\",\"output\":\"{}\"}}}}",
            "x".repeat(5 * 1024 * 1024)
        );
        let route = rollout_route(&text)?;
        assert_eq!(route.outer.as_deref(), Some("response_item"));
        assert_eq!(route.payload.as_deref(), Some("custom_tool_call_output"));
        Ok(())
    }
}

fn run() -> Result<()> {
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
    let store = Store::new(
        archive_root,
        &config_path,
        args.python_executable.clone(),
        args.codex_cli.clone(),
    )?;
    if !args.once {
        let mut startup_telemetry = CollectorTelemetry::new();
        startup_telemetry.write(&store, ACTIVE_FALLBACK)?;
    }
    let initial_paths = if args.session_files.is_empty() {
        recent_rollouts(&sessions_root, since, &store.root)?
    } else {
        args.session_files
            .iter()
            .map(|path| expand_tilde(path).and_then(|p| Ok(p.canonicalize()?)))
            .collect::<Result<Vec<_>>>()?
    };
    eprintln!(
        "memory-wuxian-collector startup: discovered {} rollout files",
        initial_paths.len()
    );
    let initial_paths = if args.once {
        initial_paths
    } else {
        store.changed_rollouts(initial_paths)?
    };
    eprintln!(
        "memory-wuxian-collector startup: {} rollout files require synchronization",
        initial_paths.len()
    );
    eprintln!("memory-wuxian-collector startup: synchronization started");
    // Startup and explicit recovery must never block exact capture on a model.
    // The maintenance supervisor drains any summary job created by this pass.
    let initial = store.sync_startup_batch(initial_paths)?;
    eprintln!("memory-wuxian-collector startup: synchronization completed");
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

fn main() -> Result<()> {
    // Windows starts console programs with a relatively small main-thread stack.
    // A full historical import can traverse deeply nested JSON and archive state,
    // so run the collector on an explicitly sized stack on every platform.
    std::thread::Builder::new()
        .name("memory-wuxian-collector".to_string())
        .stack_size(16 * 1024 * 1024)
        .spawn(run)?
        .join()
        .map_err(|panic| {
            let message = panic
                .downcast_ref::<&str>()
                .copied()
                .or_else(|| panic.downcast_ref::<String>().map(String::as_str))
                .unwrap_or("unknown panic");
            anyhow::anyhow!("collector thread panicked: {message}")
        })?
}
