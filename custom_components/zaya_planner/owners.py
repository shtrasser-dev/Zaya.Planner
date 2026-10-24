"""Who makes a plan when the owner is not given: the automation or script that started the run.

Home Assistant fires `automation_triggered` and `script_started` with the
context of the run; the actions of that run (and of the scripts it calls)
share the context. The first of them for a context is its owner: an automation
that calls a planning module owns the plan, a script run by itself owns its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN

OWNERS = f"{DOMAIN}_owners"
# Runs are remembered this long
RUN_LIFETIME = timedelta(hours=1)
STARTED_EVENTS = ("automation_triggered", "script_started")


@callback
def async_track_owners(hass: HomeAssistant) -> None:
    """Remember which automation or script started each run."""
    runs: dict[str, tuple[str, datetime]] = hass.data.setdefault(OWNERS, {})

    @callback
    def _async_started(event: Event) -> None:
        now = dt_util.utcnow()
        for context_id in [key for key, (_, at) in runs.items() if at < now - RUN_LIFETIME]:
            del runs[context_id]
        if (entity_id := event.data.get("entity_id")) and event.context.id not in runs:
            runs[event.context.id] = (entity_id, now)

    for event_type in STARTED_EVENTS:
        hass.bus.async_listen(event_type, _async_started)


def owner_of(hass: HomeAssistant, context: Context) -> str | None:
    """Return the automation or script that started the run of this context."""
    run = hass.data.get(OWNERS, {}).get(context.id)
    return run[0] if run else None
