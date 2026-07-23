use std::fs::{self, File};
use std::io::{Cursor, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::str::FromStr;

use age::secrecy::ExposeSecret;
use age::x25519;
use anyhow::{Context, Result, anyhow, ensure};
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use clap::{Parser, Subcommand, ValueEnum};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use rand_core::OsRng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

const ENVELOPE_FORMAT: &str = "memory-wuxian-envelope-v1";
const IDENTITY_FORMAT: &str = "memory-wuxian-envelope-identity-v1";
const INNER_MAGIC: &[u8] = b"memory-wuxian-envelope-v1\0";
const SIGNATURE_LENGTH: usize = 64;
const MAX_METADATA_LENGTH: usize = 64 * 1024;

#[derive(Parser, Debug)]
#[command(version, about = "Encrypt and sign Memory Wuxian federation envelopes")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    InitIdentity {
        #[arg(long)]
        path: PathBuf,
        #[arg(long)]
        node_id: String,
    },
    ShowIdentity {
        #[arg(long)]
        path: PathBuf,
    },
    Seal {
        #[arg(long)]
        identity: PathBuf,
        #[arg(long, required = true, action = clap::ArgAction::Append)]
        recipient: Vec<String>,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        kind: EnvelopeKind,
        #[arg(long)]
        origin_node_id: String,
        #[arg(long)]
        target_node_id: String,
    },
    Open {
        #[arg(long)]
        identity: PathBuf,
        #[arg(long)]
        signing_public_key: String,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        expected_kind: EnvelopeKind,
        #[arg(long)]
        expected_origin_node_id: String,
        #[arg(long)]
        expected_target_node_id: String,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, ValueEnum)]
#[serde(rename_all = "lowercase")]
enum EnvelopeKind {
    Bundle,
    Ack,
}

#[derive(Debug, Deserialize, Serialize)]
struct StoredIdentity {
    format: String,
    node_id: String,
    age_identity: String,
    encryption_public_key: String,
    signing_private_key: String,
    signing_public_key: String,
    fingerprint: String,
}

#[derive(Debug, Deserialize, Eq, PartialEq, Serialize)]
struct PublicIdentity {
    node_id: String,
    encryption_public_key: String,
    signing_public_key: String,
    fingerprint: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct InnerMetadata {
    format: String,
    kind: EnvelopeKind,
    origin_node_id: String,
    target_node_id: String,
    payload_length: u64,
    payload_sha256: String,
    signature_algorithm: String,
}

#[derive(Debug, Serialize)]
struct SealResult {
    format: &'static str,
    kind: EnvelopeKind,
    origin_node_id: String,
    target_node_id: String,
    payload_length: u64,
    payload_sha256: String,
    recipient_count: usize,
    output: String,
}

#[derive(Debug, Serialize)]
struct OpenResult {
    format: &'static str,
    kind: EnvelopeKind,
    origin_node_id: String,
    target_node_id: String,
    payload_length: u64,
    payload_sha256: String,
    output: String,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("memory-wuxian-envelope: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    let output = match cli.command {
        Command::InitIdentity { path, node_id } => {
            serde_json::to_value(init_identity(&path, &node_id)?)?
        }
        Command::ShowIdentity { path } => serde_json::to_value(load_identity(&path)?.public())?,
        Command::Seal {
            identity,
            recipient,
            input,
            output,
            kind,
            origin_node_id,
            target_node_id,
        } => serde_json::to_value(seal_file(
            &identity,
            &recipient,
            &input,
            &output,
            kind,
            &origin_node_id,
            &target_node_id,
        )?)?,
        Command::Open {
            identity,
            signing_public_key,
            input,
            output,
            expected_kind,
            expected_origin_node_id,
            expected_target_node_id,
        } => serde_json::to_value(open_file(
            &identity,
            &signing_public_key,
            &input,
            &output,
            expected_kind,
            &expected_origin_node_id,
            &expected_target_node_id,
        )?)?,
    };
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}

impl StoredIdentity {
    fn public(&self) -> PublicIdentity {
        PublicIdentity {
            node_id: self.node_id.clone(),
            encryption_public_key: self.encryption_public_key.clone(),
            signing_public_key: self.signing_public_key.clone(),
            fingerprint: self.fingerprint.clone(),
        }
    }

    fn signing_key(&self) -> Result<SigningKey> {
        let bytes = decode_fixed::<32>(&self.signing_private_key, "signing private key")?;
        Ok(SigningKey::from_bytes(&bytes))
    }

    fn age_identity(&self) -> Result<x25519::Identity> {
        x25519::Identity::from_str(&self.age_identity)
            .map_err(|error| anyhow!("invalid age identity: {error}"))
    }
}

fn init_identity(path: &Path, node_id: &str) -> Result<PublicIdentity> {
    validate_node_id(node_id)?;
    ensure!(
        !path.exists(),
        "identity already exists: {}",
        path.display()
    );

    let age_identity = x25519::Identity::generate();
    let encryption_public_key = age_identity.to_public().to_string();
    let signing_key = SigningKey::generate(&mut OsRng);
    let signing_public_key = URL_SAFE_NO_PAD.encode(signing_key.verifying_key().to_bytes());
    let public = PublicIdentity {
        node_id: node_id.to_owned(),
        fingerprint: identity_fingerprint(node_id, &encryption_public_key, &signing_public_key),
        encryption_public_key,
        signing_public_key,
    };
    let stored = StoredIdentity {
        format: IDENTITY_FORMAT.to_owned(),
        node_id: public.node_id.clone(),
        age_identity: age_identity.to_string().expose_secret().to_owned(),
        encryption_public_key: public.encryption_public_key.clone(),
        signing_private_key: URL_SAFE_NO_PAD.encode(signing_key.to_bytes()),
        signing_public_key: public.signing_public_key.clone(),
        fingerprint: public.fingerprint.clone(),
    };
    let bytes = serde_json::to_vec_pretty(&stored)?;
    atomic_write(path, &bytes, true, true)?;
    Ok(public)
}

fn load_identity(path: &Path) -> Result<StoredIdentity> {
    let bytes = fs::read(path).with_context(|| format!("read identity {}", path.display()))?;
    let identity: StoredIdentity = serde_json::from_slice(&bytes).context("parse identity JSON")?;
    ensure!(
        identity.format == IDENTITY_FORMAT,
        "unsupported identity format: {}",
        identity.format
    );
    validate_node_id(&identity.node_id)?;

    let age_identity = identity.age_identity()?;
    ensure!(
        age_identity.to_public().to_string() == identity.encryption_public_key,
        "identity encryption public key mismatch"
    );
    let signing_key = identity.signing_key()?;
    ensure!(
        URL_SAFE_NO_PAD.encode(signing_key.verifying_key().to_bytes())
            == identity.signing_public_key,
        "identity signing public key mismatch"
    );
    ensure!(
        identity.fingerprint
            == identity_fingerprint(
                &identity.node_id,
                &identity.encryption_public_key,
                &identity.signing_public_key,
            ),
        "identity fingerprint mismatch"
    );
    Ok(identity)
}

#[allow(clippy::too_many_arguments)]
fn seal_file(
    identity_path: &Path,
    recipient_strings: &[String],
    input_path: &Path,
    output_path: &Path,
    kind: EnvelopeKind,
    origin_node_id: &str,
    target_node_id: &str,
) -> Result<SealResult> {
    ensure!(
        !recipient_strings.is_empty(),
        "at least one recipient is required"
    );
    validate_node_id(origin_node_id)?;
    validate_node_id(target_node_id)?;
    let identity = load_identity(identity_path)?;
    ensure!(
        identity.node_id == origin_node_id,
        "origin node ID does not match signing identity"
    );

    let payload =
        fs::read(input_path).with_context(|| format!("read input {}", input_path.display()))?;
    let payload_length = u64::try_from(payload.len()).context("payload is too large")?;
    let payload_sha256 = hex_digest(&payload);
    let metadata = InnerMetadata {
        format: ENVELOPE_FORMAT.to_owned(),
        kind,
        origin_node_id: origin_node_id.to_owned(),
        target_node_id: target_node_id.to_owned(),
        payload_length,
        payload_sha256: payload_sha256.clone(),
        signature_algorithm: "ed25519-v1".to_owned(),
    };
    let metadata_bytes = serde_json::to_vec(&metadata)?;
    ensure!(
        metadata_bytes.len() <= MAX_METADATA_LENGTH,
        "metadata is too large"
    );
    let signed_content = encode_signed_content(&metadata_bytes, &payload)?;
    let signature = identity.signing_key()?.sign(&signed_content);

    let mut inner = signed_content;
    inner.extend_from_slice(&signature.to_bytes());

    let recipients = recipient_strings
        .iter()
        .map(|value| {
            x25519::Recipient::from_str(value)
                .map(|recipient| Box::new(recipient) as Box<dyn age::Recipient + Send>)
                .map_err(|error| anyhow!("invalid age recipient: {error}"))
        })
        .collect::<Result<Vec<_>>>()?;
    let encrypted = encrypt(&inner, recipients)?;
    atomic_write(output_path, &encrypted, false, false)?;

    Ok(SealResult {
        format: ENVELOPE_FORMAT,
        kind,
        origin_node_id: origin_node_id.to_owned(),
        target_node_id: target_node_id.to_owned(),
        payload_length,
        payload_sha256,
        recipient_count: recipient_strings.len(),
        output: output_path.display().to_string(),
    })
}

#[allow(clippy::too_many_arguments)]
fn open_file(
    identity_path: &Path,
    signing_public_key: &str,
    input_path: &Path,
    output_path: &Path,
    expected_kind: EnvelopeKind,
    expected_origin_node_id: &str,
    expected_target_node_id: &str,
) -> Result<OpenResult> {
    validate_node_id(expected_origin_node_id)?;
    validate_node_id(expected_target_node_id)?;
    let identity = load_identity(identity_path)?;
    let verifying_key = VerifyingKey::from_bytes(&decode_fixed::<32>(
        signing_public_key,
        "signing public key",
    )?)
    .context("invalid Ed25519 signing public key")?;
    let encrypted =
        fs::read(input_path).with_context(|| format!("read input {}", input_path.display()))?;
    let inner = decrypt(&encrypted, &identity.age_identity()?)?;
    let (metadata, payload, signature, signed_content) = decode_inner(&inner)?;

    ensure!(
        metadata.format == ENVELOPE_FORMAT,
        "unexpected envelope format"
    );
    ensure!(metadata.kind == expected_kind, "unexpected envelope kind");
    ensure!(
        metadata.origin_node_id == expected_origin_node_id,
        "unexpected origin node ID"
    );
    ensure!(
        metadata.target_node_id == expected_target_node_id,
        "unexpected target node ID"
    );
    ensure!(
        metadata.signature_algorithm == "ed25519-v1",
        "unsupported signature algorithm"
    );
    let actual_length = u64::try_from(payload.len()).context("payload is too large")?;
    ensure!(
        metadata.payload_length == actual_length,
        "payload length mismatch"
    );
    ensure!(
        metadata.payload_sha256 == hex_digest(payload),
        "payload SHA-256 mismatch"
    );
    verifying_key
        .verify(signed_content, &signature)
        .context("Ed25519 signature verification failed")?;

    atomic_write(output_path, payload, false, false)?;
    Ok(OpenResult {
        format: ENVELOPE_FORMAT,
        kind: metadata.kind,
        origin_node_id: metadata.origin_node_id,
        target_node_id: metadata.target_node_id,
        payload_length: metadata.payload_length,
        payload_sha256: metadata.payload_sha256,
        output: output_path.display().to_string(),
    })
}

fn encode_signed_content(metadata: &[u8], payload: &[u8]) -> Result<Vec<u8>> {
    let metadata_length = u32::try_from(metadata.len()).context("metadata is too large")?;
    let capacity = INNER_MAGIC
        .len()
        .checked_add(4)
        .and_then(|value| value.checked_add(metadata.len()))
        .and_then(|value| value.checked_add(payload.len()))
        .context("envelope is too large")?;
    let mut output = Vec::with_capacity(capacity);
    output.extend_from_slice(INNER_MAGIC);
    output.extend_from_slice(&metadata_length.to_be_bytes());
    output.extend_from_slice(metadata);
    output.extend_from_slice(payload);
    Ok(output)
}

fn decode_inner(inner: &[u8]) -> Result<(InnerMetadata, &[u8], Signature, &[u8])> {
    let minimum = INNER_MAGIC.len() + 4 + SIGNATURE_LENGTH;
    ensure!(inner.len() >= minimum, "decrypted envelope is truncated");
    ensure!(
        inner.starts_with(INNER_MAGIC),
        "invalid decrypted envelope header"
    );
    let length_start = INNER_MAGIC.len();
    let metadata_length = u32::from_be_bytes(
        inner[length_start..length_start + 4]
            .try_into()
            .expect("fixed metadata length field"),
    ) as usize;
    ensure!(
        metadata_length <= MAX_METADATA_LENGTH,
        "metadata length exceeds limit"
    );
    let metadata_start = length_start + 4;
    let metadata_end = metadata_start
        .checked_add(metadata_length)
        .context("metadata length overflow")?;
    ensure!(
        metadata_end + SIGNATURE_LENGTH <= inner.len(),
        "decrypted envelope is truncated"
    );
    let metadata: InnerMetadata = serde_json::from_slice(&inner[metadata_start..metadata_end])
        .context("parse envelope metadata")?;
    let signature_start = inner.len() - SIGNATURE_LENGTH;
    ensure!(
        metadata_end <= signature_start,
        "decrypted envelope has invalid framing"
    );
    let signature = Signature::from_bytes(
        inner[signature_start..]
            .try_into()
            .expect("fixed signature length"),
    );
    Ok((
        metadata,
        &inner[metadata_end..signature_start],
        signature,
        &inner[..signature_start],
    ))
}

fn encrypt(plaintext: &[u8], recipients: Vec<Box<dyn age::Recipient + Send>>) -> Result<Vec<u8>> {
    let encryptor = age::Encryptor::with_recipients(
        recipients
            .iter()
            .map(|recipient| recipient.as_ref() as &dyn age::Recipient),
    )
    .map_err(|error| anyhow!("create age encryptor: {error}"))?;
    let mut encrypted = Vec::new();
    let mut writer = encryptor
        .wrap_output(&mut encrypted)
        .context("start age encryption")?;
    writer.write_all(plaintext).context("encrypt envelope")?;
    writer.finish().context("finish age encryption")?;
    Ok(encrypted)
}

fn decrypt(ciphertext: &[u8], identity: &x25519::Identity) -> Result<Vec<u8>> {
    let decryptor = age::Decryptor::new_buffered(Cursor::new(ciphertext))
        .context("parse age encrypted envelope")?;
    let mut reader = decryptor
        .decrypt(std::iter::once(identity as &dyn age::Identity))
        .context("decrypt age envelope")?;
    let mut plaintext = Vec::new();
    reader
        .read_to_end(&mut plaintext)
        .context("read decrypted envelope")?;
    Ok(plaintext)
}

fn atomic_write(path: &Path, bytes: &[u8], private: bool, no_clobber: bool) -> Result<()> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)
        .with_context(|| format!("create output directory {}", parent.display()))?;
    let mut temporary = NamedTempFile::new_in(parent)
        .with_context(|| format!("create temporary file in {}", parent.display()))?;
    temporary
        .write_all(bytes)
        .with_context(|| format!("write temporary file for {}", path.display()))?;
    temporary
        .as_file()
        .sync_all()
        .with_context(|| format!("sync temporary file for {}", path.display()))?;
    if private {
        set_private_permissions(temporary.as_file())?;
    }
    if no_clobber {
        temporary
            .persist_noclobber(path)
            .map_err(|error| error.error)
            .with_context(|| format!("persist new file {}", path.display()))?;
    } else {
        temporary
            .persist(path)
            .map_err(|error| error.error)
            .with_context(|| format!("persist file {}", path.display()))?;
    }
    sync_parent(parent)?;
    Ok(())
}

#[cfg(unix)]
fn set_private_permissions(file: &File) -> Result<()> {
    file.set_permissions(fs::Permissions::from_mode(0o600))
        .context("set identity permissions to 0600")
}

#[cfg(not(unix))]
fn set_private_permissions(_file: &File) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn sync_parent(parent: &Path) -> Result<()> {
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .with_context(|| format!("sync output directory {}", parent.display()))
}

#[cfg(not(unix))]
fn sync_parent(_parent: &Path) -> Result<()> {
    Ok(())
}

fn identity_fingerprint(node_id: &str, encryption_key: &str, signing_key: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"memory-wuxian-envelope-identity-fingerprint-v1\0");
    hasher.update(node_id.as_bytes());
    hasher.update([0]);
    hasher.update(encryption_key.as_bytes());
    hasher.update([0]);
    hasher.update(signing_key.as_bytes());
    hex::encode(hasher.finalize())
}

fn hex_digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn decode_fixed<const N: usize>(value: &str, label: &str) -> Result<[u8; N]> {
    let bytes = URL_SAFE_NO_PAD
        .decode(value)
        .with_context(|| format!("decode {label} as base64url"))?;
    bytes
        .try_into()
        .map_err(|_| anyhow!("{label} must decode to {N} bytes"))
}

fn validate_node_id(value: &str) -> Result<()> {
    ensure!(!value.is_empty(), "node ID cannot be empty");
    ensure!(value.len() <= 255, "node ID is too long");
    ensure!(
        value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.')),
        "node ID contains unsupported characters"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::panic;
    use tempfile::TempDir;

    struct Fixture {
        directory: TempDir,
        sender_identity: PathBuf,
        recipient_identity: PathBuf,
        other_identity: PathBuf,
        payload: PathBuf,
        envelope: PathBuf,
    }

    impl Fixture {
        fn new() -> Result<Self> {
            let directory = tempfile::tempdir()?;
            let sender_identity = directory.path().join("sender.json");
            let recipient_identity = directory.path().join("recipient.json");
            let other_identity = directory.path().join("other.json");
            init_identity(&sender_identity, "sender")?;
            init_identity(&recipient_identity, "recipient")?;
            init_identity(&other_identity, "other")?;
            let payload = directory.path().join("input.mwxb");
            fs::write(&payload, b"memory-wuxian test payload")?;
            let envelope = directory.path().join("payload.mwxe");
            Ok(Self {
                directory,
                sender_identity,
                recipient_identity,
                other_identity,
                payload,
                envelope,
            })
        }

        fn seal_for_both(&self) -> Result<SealResult> {
            let sender = load_identity(&self.sender_identity)?;
            let recipient = load_identity(&self.recipient_identity)?;
            seal_file(
                &self.sender_identity,
                &[
                    sender.encryption_public_key,
                    recipient.encryption_public_key,
                ],
                &self.payload,
                &self.envelope,
                EnvelopeKind::Bundle,
                "sender",
                "recipient",
            )
        }

        fn open_with(&self, identity: &Path, output: &Path) -> Result<OpenResult> {
            let sender = load_identity(&self.sender_identity)?;
            open_file(
                identity,
                &sender.signing_public_key,
                &self.envelope,
                output,
                EnvelopeKind::Bundle,
                "sender",
                "recipient",
            )
        }
    }

    #[test]
    fn round_trip_for_target_recipient() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.seal_for_both()?;
        let output = fixture.directory.path().join("opened.mwxb");
        fixture.open_with(&fixture.recipient_identity, &output)?;
        assert_eq!(fs::read(output)?, fs::read(fixture.payload)?);
        Ok(())
    }

    #[test]
    fn both_recipients_can_decrypt_the_same_envelope() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.seal_for_both()?;
        let encrypted = fs::read(&fixture.envelope)?;
        let sender = load_identity(&fixture.sender_identity)?;
        let recipient = load_identity(&fixture.recipient_identity)?;
        let sender_output = fixture.directory.path().join("sender-opened.mwxb");
        let recipient_output = fixture.directory.path().join("recipient-opened.mwxb");
        fixture.open_with(&fixture.sender_identity, &sender_output)?;
        fixture.open_with(&fixture.recipient_identity, &recipient_output)?;
        assert_eq!(fs::read(sender_output)?, fs::read(&fixture.payload)?);
        assert_eq!(fs::read(recipient_output)?, fs::read(&fixture.payload)?);
        assert!(!decrypt(&encrypted, &sender.age_identity()?)?.is_empty());
        assert!(!decrypt(&encrypted, &recipient.age_identity()?)?.is_empty());
        Ok(())
    }

    #[test]
    fn rejects_wrong_recipient_without_output() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.seal_for_both()?;
        let output = fixture.directory.path().join("wrong.mwxb");
        let sender = load_identity(&fixture.sender_identity)?;
        let result = open_file(
            &fixture.other_identity,
            &sender.signing_public_key,
            &fixture.envelope,
            &output,
            EnvelopeKind::Bundle,
            "sender",
            "other",
        );
        assert!(result.is_err());
        assert!(!output.exists());
        Ok(())
    }

    #[test]
    fn rejects_ciphertext_tampering_without_output() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.seal_for_both()?;
        let mut bytes = fs::read(&fixture.envelope)?;
        let index = bytes.len() / 2;
        bytes[index] ^= 0x80;
        fs::write(&fixture.envelope, bytes)?;
        let output = fixture.directory.path().join("tampered.mwxb");
        assert!(
            fixture
                .open_with(&fixture.recipient_identity, &output)
                .is_err()
        );
        assert!(!output.exists());
        Ok(())
    }

    #[test]
    fn rejects_wrong_signing_key_without_output() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.seal_for_both()?;
        let other = load_identity(&fixture.other_identity)?;
        let output = fixture.directory.path().join("wrong-signature.mwxb");
        let result = open_file(
            &fixture.recipient_identity,
            &other.signing_public_key,
            &fixture.envelope,
            &output,
            EnvelopeKind::Bundle,
            "sender",
            "recipient",
        );
        assert!(result.is_err());
        assert!(!output.exists());
        Ok(())
    }

    #[test]
    fn rejects_wrong_target_without_output() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.seal_for_both()?;
        let sender = load_identity(&fixture.sender_identity)?;
        let output = fixture.directory.path().join("wrong-target.mwxb");
        let result = open_file(
            &fixture.recipient_identity,
            &sender.signing_public_key,
            &fixture.envelope,
            &output,
            EnvelopeKind::Bundle,
            "sender",
            "different-target",
        );
        assert!(result.is_err());
        assert!(!output.exists());
        Ok(())
    }

    #[test]
    fn public_stdout_values_do_not_contain_private_keys() -> Result<()> {
        let fixture = Fixture::new()?;
        let identity = load_identity(&fixture.sender_identity)?;
        let public_json = serde_json::to_string(&identity.public())?;
        assert!(!public_json.contains(&identity.age_identity));
        assert!(!public_json.contains(&identity.signing_private_key));
        assert!(!public_json.contains("age_identity"));
        assert!(!public_json.contains("signing_private_key"));
        Ok(())
    }

    #[test]
    fn init_identity_does_not_overwrite_existing_file() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("identity.json");
        fs::write(&path, b"existing")?;
        assert!(init_identity(&path, "node").is_err());
        assert_eq!(fs::read(path)?, b"existing");
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn identity_permissions_are_private() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("identity.json");
        init_identity(&path, "node")?;
        assert_eq!(fs::metadata(path)?.permissions().mode() & 0o777, 0o600);
        Ok(())
    }

    #[test]
    fn failures_do_not_panic_on_short_inner_data() {
        let result = panic::catch_unwind(|| decode_inner(b"short"));
        assert!(result.is_ok());
        assert!(result.unwrap().is_err());
    }
}
