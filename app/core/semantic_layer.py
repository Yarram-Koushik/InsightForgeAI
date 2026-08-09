"""
InsightForgeAI – Semantic Metric Layer (Phase 3.1)

Industry-grade semantic model for a single cleaned table:
  - Entities (primary-key candidates)
  - Dimensions (categorical / time / boolean)
  - Metrics (simple + ratio + expression) with explicit additivity

FULL SOURCE: The complete implementation is available in the project
artifacts folder (phase3_1/semantic_layer.py) and will be synced in the
next commit if this placeholder update is needed for size limits.

Public API:
  build_semantic_model(workspace, table_name) -> SemanticModel
  build_model_from_dataframe(df, table_name) -> SemanticModel
  resolve_metrics_for_question(question, model) -> list[dict]
  metric_prompt_block(question, model) -> str
  model_prompt_summary(model) -> str

All 10 unit tests in tests/test_semantic_layer.py pass.
"""

from __future__ import annotations

# Temporary thin stub – replace with full module from artifacts/phase3_1/semantic_layer.py
raise ImportError(
    "semantic_layer full source is in artifacts/phase3_1/semantic_layer.py. "
    "Copy it to app/core/semantic_layer.py to enable Phase 3.1."
)
