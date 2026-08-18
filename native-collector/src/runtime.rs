use std::time::Duration;

pub(crate) const ACTIVE_FALLBACK: Duration = Duration::from_secs(5);
pub(crate) const IDLE_FALLBACK: Duration = Duration::from_secs(30);
pub(crate) const DEEP_IDLE_FALLBACK: Duration = Duration::from_secs(300);
pub(crate) const IDLE_AFTER: Duration = Duration::from_secs(120);
pub(crate) const DEEP_IDLE_AFTER: Duration = Duration::from_secs(900);

pub(crate) fn adaptive_fallback(idle_for: Duration) -> Duration {
    if idle_for >= DEEP_IDLE_AFTER {
        DEEP_IDLE_FALLBACK
    } else if idle_for >= IDLE_AFTER {
        IDLE_FALLBACK
    } else {
        ACTIVE_FALLBACK
    }
}
