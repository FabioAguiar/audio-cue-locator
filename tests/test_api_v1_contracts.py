"""Milestone-level generated OpenAPI and REST dependency contract checks.

The generated document is the sole OpenAPI source.  These assertions are
intentionally broader than the endpoint-focused M5-02 through M5-05 suites:
they describe the complete public v1 surface as one contract and inspect the
whole REST package rather than a single route module.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


app_module = importlib.import_module("audio_cue_locator.interfaces.rest_api.app")
REST_PACKAGE_ROOT = Path(app_module.__file__).parent

ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorPublic"}

# Each operation's documented success response and externally observable
# failure responses.  The comparison is against create_app().openapi(), never
# against a separately maintained OpenAPI fixture.
OPERATION_MATRIX = {
    ("/api/v1/health", "get"): {
        "success": ("200", None),
        "errors": (),
    },
    ("/api/v1/assets/source-media", "post"): {
        "success": ("201", "AssetPublic"),
        "errors": ("413", "415", "422", "500"),
    },
    ("/api/v1/assets/cue", "post"): {
        "success": ("201", "AssetPublic"),
        "errors": ("413", "415", "422", "500"),
    },
    ("/api/v1/analyses", "post"): {
        "success": ("202", "AnalysisPublic"),
        "errors": ("400", "404", "413", "415", "422", "500"),
    },
    ("/api/v1/analyses/{analysis_id}", "get"): {
        "success": ("200", "AnalysisPublic"),
        "errors": ("404", "500"),
    },
    ("/api/v1/analyses/{analysis_id}/result", "get"): {
        "success": ("200", "AnalysisResultEnvelope"),
        "errors": ("404", "409", "500"),
    },
}

PUBLIC_SCHEMA_REQUIRED_FIELDS = {
    "AssetPublic": {
        "identifier",
        "logical_type",
        "sanitized_name",
        "media_type",
        "size_bytes",
        "checksum",
        "checksum_algorithm",
    },
    "AnalysisCreateRequest": {"source_asset_id", "cues"},
    "AnalysisPublic": {
        "analysis_id",
        "status",
        "source_asset_id",
        "cues",
        "lifecycle_timestamps",
    },
    "AnalysisResultEnvelope": {
        "api_version",
        "result_schema_version",
        "result",
    },
    "ErrorPublic": {"error_code", "message", "correlation_id"},
}


def _response_schema(operation: dict, status_code: str) -> dict:
    return operation["responses"][status_code]["content"]["application/json"][
        "schema"
    ]


def test_generated_openapi_contains_the_complete_v1_operation_matrix():
    document = app_module.create_app().openapi()

    documented_operations = set(OPERATION_MATRIX)
    generated_operations = {
        (path, method)
        for path, path_item in document["paths"].items()
        if path.startswith("/api/v1/")
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }

    assert generated_operations == documented_operations

    for (path, method), contract in OPERATION_MATRIX.items():
        operation = document["paths"][path][method]
        success_status, success_model = contract["success"]
        assert success_status in operation["responses"]
        success_schema = _response_schema(operation, success_status)
        if success_model is not None:
            assert success_schema == {
                "$ref": f"#/components/schemas/{success_model}"
            }

        for error_status in contract["errors"]:
            assert error_status in operation["responses"], (
                f"{method.upper()} {path} does not publish its documented "
                f"{error_status} response"
            )
            assert _response_schema(operation, error_status) == ERROR_SCHEMA


def test_generated_components_preserve_public_schema_fields_and_enums():
    components = app_module.create_app().openapi()["components"]["schemas"]

    for schema_name, required_fields in PUBLIC_SCHEMA_REQUIRED_FIELDS.items():
        assert schema_name in components
        assert set(components[schema_name]["required"]) == required_fields

    assert set(components["AssetLogicalType"]["enum"]) == {
        "source_media",
        "cue",
        "derived_artifact",
        "analysis_result",
    }
    assert set(components["AnalysisStatus"]["enum"]) == {
        "queued",
        "running",
        "succeeded",
        "failed",
    }
    assert set(components["ErrorCode"]["enum"]) == {
        "validation_error",
        "unsupported_media",
        "resource_limit_exceeded",
        "resource_not_found",
        "lifecycle_conflict",
        "result_not_ready",
        "analysis_failed",
        "internal_error",
    }

    asset = components["AssetPublic"]["properties"]
    assert asset["size_bytes"]["minimum"] == 0
    assert asset["checksum"]["minLength"] == 64
    assert asset["checksum"]["maxLength"] == 64
    assert asset["checksum_algorithm"]["const"] == "sha256"


def test_generated_cue_reference_schema_exposes_the_three_additive_s0003_fields():
    """S0003 adds `label`, `trim_start_seconds`, and `trim_end_seconds` to
    `AnalysisCueReference`, all optional/nullable, alongside the two
    already-required fields; the numeric bounds are non-negative, matching
    the obvious request-shape violations Pydantic itself rejects (a
    cross-field/duration-aware bound stays Application-owned and is not
    representable in JSON Schema)."""

    components = app_module.create_app().openapi()["components"]["schemas"]
    cue_reference = components["AnalysisCueReference"]

    assert set(cue_reference["required"]) == {"cue_id", "asset_id"}
    properties = cue_reference["properties"]
    for field_name in ("label", "trim_start_seconds", "trim_end_seconds"):
        assert field_name in properties

    for field_name in ("trim_start_seconds", "trim_end_seconds"):
        # A nullable numeric field is generated as `anyOf: [{type, ...},
        # {type: "null"}]` rather than a bare `type`.
        numeric_branch = next(
            branch
            for branch in properties[field_name]["anyOf"]
            if branch.get("type") == "number"
        )
        assert numeric_branch["minimum"] == 0
        null_branch = next(
            branch for branch in properties[field_name]["anyOf"] if branch.get("type") == "null"
        )
        assert null_branch == {"type": "null"}

    label_branch_types = {
        branch.get("type") for branch in properties["label"]["anyOf"]
    }
    assert label_branch_types == {"string", "null"}


def test_generated_openapi_error_and_route_surface_is_unchanged_by_s0003():
    """S0003 adds no new `ErrorCode`, route, or status: the closed v1
    operation matrix and error-code catalog stay exactly as documented."""

    document = app_module.create_app().openapi()
    components = document["components"]["schemas"]

    assert set(components["ErrorCode"]["enum"]) == {
        "validation_error",
        "unsupported_media",
        "resource_limit_exceeded",
        "resource_not_found",
        "lifecycle_conflict",
        "result_not_ready",
        "analysis_failed",
        "internal_error",
    }
    assert set(document["paths"]) == {
        path for path, _ in OPERATION_MATRIX
    }


def test_generated_result_envelope_keeps_api_and_result_versions_independent():
    envelope = app_module.create_app().openapi()["components"]["schemas"][
        "AnalysisResultEnvelope"
    ]["properties"]

    api_version = envelope["api_version"].get(
        "const", envelope["api_version"].get("default")
    )
    result_version = envelope["result_schema_version"]["default"]

    assert api_version == "v1"
    assert result_version.startswith("analysis_result.")
    assert result_version != api_version


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    modules |= {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    return modules


def test_rest_package_keeps_adapters_inside_the_documented_composition_root():
    modules_by_file = {
        path.name: _imported_modules(path)
        for path in sorted(REST_PACKAGE_ROOT.glob("*.py"))
    }

    for filename, modules in modules_by_file.items():
        assert not any("acoustic_matching" in module for module in modules), filename

        if filename == "app.py":
            continue
        assert "sqlite3" not in modules, filename
        assert "pathlib" not in modules, filename
        assert not any(
            module.startswith("audio_cue_locator.infrastructure")
            for module in modules
        ), filename

    composition_imports = {
        module
        for module in modules_by_file["app.py"]
        if module.startswith("audio_cue_locator.infrastructure")
    }
    assert composition_imports == {
        "audio_cue_locator.infrastructure.analysis_repository.sqlite_repository",
        "audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage",
        "audio_cue_locator.infrastructure.execution.local_analysis_executor",
    }


@pytest.mark.parametrize(
    ("path", "method", "success_status"),
    [
        (path, method, contract["success"][0])
        for (path, method), contract in OPERATION_MATRIX.items()
    ],
)
def test_generated_operation_success_statuses_are_not_implicit_defaults(
    path: str, method: str, success_status: str
):
    responses = app_module.create_app().openapi()["paths"][path][method]["responses"]

    assert success_status in responses
