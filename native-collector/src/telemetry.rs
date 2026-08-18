use std::collections::VecDeque;
use std::time::{Duration, Instant};

use chrono::{SecondsFormat, Utc};
use serde_json::{Value, json};

use crate::runtime::{DEEP_IDLE_FALLBACK, IDLE_FALLBACK};

pub(crate) struct CollectorTelemetry {
    ready: bool,
    watcher_ready: bool,
    pub(crate) last_mode: &'static str,
    last_file_event: Option<String>,
    last_archive_update: Option<String>,
    source_watermark: Option<String>,
    archive_watermark: Option<String>,
    wakeups: VecDeque<(Instant, String)>,
}

impl CollectorTelemetry {
    pub(crate) fn new() -> Self {
        Self {
            ready: false,
            watcher_ready: false,
            last_mode: "active",
            last_file_event: None,
            last_archive_update: None,
            source_watermark: None,
            archive_watermark: None,
            wakeups: VecDeque::new(),
        }
    }

    pub(crate) fn mode(interval: Duration) -> &'static str {
        if interval == DEEP_IDLE_FALLBACK {
            "deep-idle"
        } else if interval == IDLE_FALLBACK {
            "idle"
        } else {
            "active"
        }
    }

    pub(crate) fn record_event(&mut self) {
        let now = Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true);
        self.last_file_event = Some(now.clone());
        self.wakeups.push_back((Instant::now(), now));
    }

    pub(crate) fn record_source_watermark(&mut self, watermark: Option<String>) {
        self.source_watermark = watermark;
    }

    pub(crate) fn record_archive(&mut self, watermark: Option<String>) {
        self.last_archive_update = Some(Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true));
        self.archive_watermark = watermark;
    }

    pub(crate) fn mark_ready(&mut self) {
        self.ready = true;
    }

    pub(crate) fn mark_watcher_ready(&mut self) {
        self.watcher_ready = true;
    }

    pub(crate) fn document(&mut self, interval: Duration) -> Value {
        while self
            .wakeups
            .front()
            .is_some_and(|(instant, _)| instant.elapsed() > Duration::from_secs(3600))
        {
            self.wakeups.pop_front();
        }
        self.last_mode = Self::mode(interval);
        json!({
            "format_version": 2,
            "pid": std::process::id(),
            "phase": if self.ready { "ready" } else if self.watcher_ready { "watching" } else { "starting" },
            "ready": self.ready,
            "watcher_ready": self.watcher_ready,
            "mode": self.last_mode,
            "fallback_interval_seconds": interval.as_secs(),
            "last_file_event": self.last_file_event,
            "last_archive_update": self.last_archive_update,
            "source_watermark": self.source_watermark,
            "archive_watermark": self.archive_watermark,
            "wakeups_last_hour": self.wakeups.len(),
            "recent_wakeups": self.wakeups.iter().map(|(_, timestamp)| timestamp).collect::<Vec<_>>(),
            "updated_at": Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true),
        })
    }
}
