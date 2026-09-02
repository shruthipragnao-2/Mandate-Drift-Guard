"""Framework-agnostic pipeline logic (evidence engine, semantic risk client, policy gate,
orchestrator). No DB session, no FastAPI, no SQLAlchemy dependency at this layer -- this is
what `eval/` (a later milestone) imports directly, in-process, per
docs/IMPLEMENTATION-PLAN.md §A/§D.
"""
