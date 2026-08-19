import hashlib

from memory_cloud_transport import (
    AuthenticatedOpenResult,
    _AUTHENTICATED_OPEN_AUTHORITY,
)


def _import_with_manifest(manager, bundle, manifest, expected_node_id):
    return manager._import_authenticated_delta(
        bundle,
        expected_node_id=expected_node_id,
        authenticated_open_result=AuthenticatedOpenResult(
            _AUTHENTICATED_OPEN_AUTHORITY,
            {
                "origin_node_id": manifest["origin_node_id"],
                "target_node_id": manager.node()["node_id"],
                "payload_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            },
        ),
    )


def verified_import(manager, bundle, expected_node_id=None):
    manifest = manager.read_bundle_manifest(bundle)
    return _import_with_manifest(manager, bundle, manifest, expected_node_id)


def authenticated_import(manager, bundle):
    manifest = manager.read_bundle_manifest(bundle)
    return _import_with_manifest(
        manager,
        bundle,
        manifest,
        manifest["origin_node_id"],
    )
