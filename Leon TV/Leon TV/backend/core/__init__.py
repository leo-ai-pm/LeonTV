"""Core interfaces, loaded only when requested by the application."""

__all__ = ["WorkflowEngine", "WorkflowStage"]


def __getattr__(name):
    if name in __all__:
        from .orchestrator import WorkflowEngine, WorkflowStage
        globals().update(WorkflowEngine=WorkflowEngine, WorkflowStage=WorkflowStage)
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
