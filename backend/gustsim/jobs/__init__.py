"""Job handlers outside the case/mesh/solve pipeline.

Importing this package registers each handler with `worker.JOB_HANDLERS`; the lane a kind
runs on is declared in `db.KIND_LANES`.
"""

from . import amr, optimise, package, report  # noqa: F401,E402
