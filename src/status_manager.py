"""Module for managing the status of a charm based on raised exceptions."""

from typing import List

from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase, WaitingStatus


def get_first_worst_status(statuses: List[StatusBase]) -> StatusBase:
    """Return the first of the worst statuses in the list.

    Raises if len(statuses) == 0.

    Status are ranked, starting with the worst:
        BlockedStatus
        WaitingStatus
        MaintenanceStatus
        ActiveStatus
    """
    if len(statuses) == 0:
        raise ValueError("No statuses provided")

    blocked = None
    waiting = None
    maintenance = None
    active = None

    for status in statuses:
        if isinstance(status, BlockedStatus):
            blocked = status
            # Escape immediately, as this is the worst status
            break
        if isinstance(status, WaitingStatus):
            waiting = waiting or status
        elif isinstance(status, MaintenanceStatus):
            maintenance = maintenance or status
        elif isinstance(status, ActiveStatus):
            active = active or status

    status = blocked or waiting or maintenance or active
    if not status:
        raise ValueError("No valid statuses provided")
    return status
