from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from project_dm.models import ServiceControl


SERVICE_NAMES: tuple[str, ...] = ("scraper", "avatar", "nlp")
DEFAULT_DESIRED_STATE = "paused"
DEFAULT_CURRENT_STATE = "stopped"

DESIRED_STATE_ACTIONS = {
    "start": "running",
    "pause": "paused",
    "stop": "stopped",
}


def ensure_service_controls(session: Session) -> list[ServiceControl]:
    controls: list[ServiceControl] = []
    for service_name in SERVICE_NAMES:
        control = session.get(ServiceControl, service_name)
        if control is None:
            control = ServiceControl(
                service_name=service_name,
                desired_state=DEFAULT_DESIRED_STATE,
                current_state=DEFAULT_CURRENT_STATE,
            )
            session.add(control)
            session.flush()
        controls.append(control)
    return controls


def list_service_controls(session: Session) -> list[ServiceControl]:
    statement = select(ServiceControl).order_by(ServiceControl.service_name)
    return list(session.scalars(statement))


def set_service_control_desired_state(
    session: Session,
    service_name: str,
    desired_state: str,
) -> ServiceControl | None:
    control = session.get(ServiceControl, service_name)
    if control is None:
        control = ServiceControl(
            service_name=service_name,
            desired_state=desired_state,
            current_state=DEFAULT_CURRENT_STATE,
        )
        session.add(control)
    else:
        control.desired_state = desired_state
    session.flush()
    return control


def update_service_control_state(
    session: Session,
    service_name: str,
    *,
    current_state: str | None = None,
    current_job_id: int | None = None,
    message: str | None = None,
) -> ServiceControl | None:
    control = session.get(ServiceControl, service_name)
    if control is None:
        return None

    if current_state is not None:
        control.current_state = current_state
    control.current_job_id = current_job_id
    control.last_heartbeat_at = datetime.now(UTC)
    control.message = message
    session.flush()
    return control
