"""Optional pytest instrumentation, preserving existing test bodies/order."""
import functools
import itertools
import os
from pathlib import Path

from legacy_reserve_trace import ReserveTrace

_original = None
_counter = itertools.count()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RESERVE_DIAGNOSTIC_MODE") != "prefix":
        return
    target = "tests/test_media_runtime.py::test_unknown_never_resubmits_and_survives_replacement_grant_and_restart"
    stop = next(i for i, item in enumerate(items) if item.nodeid == target) + 1
    config.hook.pytest_deselected(items=items[stop:])
    del items[stop:]


def pytest_runtest_setup(item):
    global _original
    if _original is not None:
        return
    from core.execution.media_runtime import MediaExecution

    _original = MediaExecution.reserve_submit

    @functools.wraps(_original)
    def observed(self, *args, **kwargs):
        output = Path(os.environ["RESERVE_DIAGNOSTIC_OUTPUT"]) / ("reserve-%04d" % next(_counter))
        trace = ReserveTrace(output)
        with trace:
            return _original(self, *args, **kwargs)

    MediaExecution.reserve_submit = observed


def pytest_unconfigure(config):
    if _original is not None:
        from core.execution.media_runtime import MediaExecution

        MediaExecution.reserve_submit = _original
