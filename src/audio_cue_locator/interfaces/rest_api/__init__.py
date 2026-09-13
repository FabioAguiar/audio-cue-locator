"""REST API: the `/api/v1` FastAPI application and its public v1 transport
schemas (M5-01). No other module in this package defines a second FastAPI
application or a competing Asset/Analysis transport representation.
"""

from .app import API_V1_PREFIX, API_VERSION, V1_STATUS_CATALOG, app, create_app
from .schemas import (
    AnalysisCreateRequest,
    AnalysisCueReference,
    AnalysisFailureCategory,
    AnalysisLifecycleTimestamps,
    AnalysisPublic,
    AnalysisResultEnvelope,
    AnalysisStatus,
    AnalysisStructuredError,
    AssetCreateRequest,
    AssetLogicalType,
    AssetPublic,
    analysis_record_to_public,
    asset_to_public,
)

__all__ = [
    "API_V1_PREFIX",
    "API_VERSION",
    "V1_STATUS_CATALOG",
    "AnalysisCreateRequest",
    "AnalysisCueReference",
    "AnalysisFailureCategory",
    "AnalysisLifecycleTimestamps",
    "AnalysisPublic",
    "AnalysisResultEnvelope",
    "AnalysisStatus",
    "AnalysisStructuredError",
    "AssetCreateRequest",
    "AssetLogicalType",
    "AssetPublic",
    "analysis_record_to_public",
    "app",
    "asset_to_public",
    "create_app",
]
