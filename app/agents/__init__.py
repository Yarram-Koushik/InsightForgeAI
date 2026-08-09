"""
InsightForgeAI – Multi-Agent Layer (Phase 2.3)

LangGraph-style orchestration without heavy framework lock-in.
Agents:
  - RouterAgent: classifies intent
  - SQLAgent: NL → SQL → execute (wraps Phase 2.2)
  - InsightAgent: business interpretation of results
  - ClarifyAgent: asks for missing context
  - Orchestrator: runs the pipeline with full transparency
"""

from .orchestrator import run_agent, AgentResult

__all__ = ["run_agent", "AgentResult"]
