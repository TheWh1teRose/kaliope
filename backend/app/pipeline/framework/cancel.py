"""Cooperative cancellation of a flow.

A stop never aborts a provider call that is already on the wire. The call
returns, its result is discarded, and the runner does not start another node,
beat or chunk. Work already written to the artifact store stays there.
"""

from __future__ import annotations


class RunStopped(Exception):  # noqa: N818 - a stop is not a failure
    """The run was asked to stop. Not a failure: nothing is wrong with the work."""
