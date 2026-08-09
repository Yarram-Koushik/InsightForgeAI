"""
InsightForgeAI – Multi-Agent Layer (Phase 2.3 / 2.4)

Agents:
  - RouterAgent, SQLAgent, InsightAgent, ClarifyAgent
  - VizAgent (Phase 2.4) – automatic Plotly charts
  - Orchestrator – runs the pipeline with full transparency
"""

from .orchestrator import run_agent, AgentResult

__all__ = ["run_agent", "AgentResult"]
