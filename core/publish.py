"""Build a signed file package and apply that same artifact through one receiver."""
from pathlib import Path
from storage import atomic_replace_bytes, bytes_sha256, safe_target
from install import ChangedFileInstaller


class PackageService:
    def __init__(self, exchange):
        self.exchange = exchange

    def build(self, source, relative_paths, output):
        # Developer-side affected tests are run before this call, not on recipients.
        if not relative_paths or len(relative_paths) != len(set(relative_paths)):
            raise ValueError('an explicit unique package file list is required')
        files = {name: safe_target(source, name).read_bytes() for name in relative_paths}
        artifact = self.exchange.export_files(files)
        atomic_replace_bytes(Path(output), artifact)
        return {'path': str(output), 'files': list(files), 'sha256': bytes_sha256(artifact)}

    def install(self, package, target, expected_origin, load_check):
        if not callable(load_check):
            raise ValueError('one explicit entry-load check is required')
        verified = self.exchange.receive(package, expected_origin=expected_origin, expected_kind='environment')
        if verified['status'] != 'verified-files':
            raise ValueError('expected a signed file package')
        return ChangedFileInstaller(target).apply(verified['files'], load_check=load_check)
