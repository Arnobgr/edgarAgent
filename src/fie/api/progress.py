"""Read a running job's progress out of its LangGraph checkpoint.

The checkpoint is the only place a run's mid-flight state exists — the job
row in `api.db` is written when the graph returns, which for a 75-second run
is a long time to show the caller nothing. This reads the same file
`answer_question` writes, through a second connection held open for the
lifetime of the app.
"""

import logging
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fie.api.models import Progress, RunPhase
from fie.state import RunBudget

logger = logging.getLogger(__name__)


def _phase(channels: dict[str, Any], *, awaiting_review: bool) -> RunPhase:
    """Which stage of the run the state has reached.

    Order matters: a verify→research loop-back leaves `draft_answer` and
    `verdicts` set while research runs again, so the later stages are tested
    first and a loop-back reads as `verifying` rather than reverting to
    `researching`. That is the honest summary — the run is in its
    verification cycle."""
    if awaiting_review:
        return "reviewing"
    if channels.get("claims") and channels.get("verdicts"):
        return "verifying"
    if channels.get("draft_answer"):
        return "synthesizing"
    if channels.get("sub_questions"):
        return "researching"
    return "starting"


async def read_progress(
    saver: AsyncSqliteSaver, thread_id: str, *, awaiting_review: bool = False
) -> Progress | None:
    """Progress for one run, or None when it has not checkpointed yet."""
    try:
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.warning("could not read checkpoint for %s", thread_id, exc_info=True)
        return None
    if checkpoint is None:
        return None

    channels: dict[str, Any] = checkpoint.checkpoint.get("channel_values", {})
    budget = channels.get("run_budget")
    if not isinstance(budget, RunBudget):
        budget = RunBudget()
    metadata = checkpoint.metadata or {}
    step = metadata.get("step")
    return Progress(
        step=step if isinstance(step, int) else None,
        phase=_phase(channels, awaiting_review=awaiting_review),
        verify_iteration=int(channels.get("verify_iteration") or 0),
        critic_iteration=int(channels.get("critic_iteration") or 0),
        cost_usd=budget.cost_usd_used,
        tokens=budget.tokens_used,
    )
