from __future__ import annotations

from decision_agent.connectors.base import Connector
from decision_agent.connectors.google import Gmail, GoogleCalendar
from decision_agent.connectors.imap import ImapMail
from decision_agent.connectors.jira import JiraConnector
from decision_agent.connectors.microsoft import OutlookCalendar, OutlookMail
from decision_agent.connectors.notion import NotionConnector
from decision_agent.connectors.slack import SlackConnector

CONNECTORS: dict[str, type[Connector]] = {
    cls.id: cls
    for cls in (
        GoogleCalendar,
        OutlookCalendar,
        Gmail,
        OutlookMail,
        ImapMail,
        NotionConnector,
        JiraConnector,
        SlackConnector,
    )
}


def describe(cls: type[Connector]) -> dict:
    """Everything the dashboard needs to draw the settings form for one connector."""
    return {
        "id": cls.id,
        "label": cls.label,
        "description": cls.description,
        "reads": cls.reads,
        "guide": list(cls.guide),
        "uses_oauth": cls.uses_oauth,
        "options": [
            {
                "name": o.name, "label": o.label, "kind": o.kind, "default": o.default,
                "help": o.help, "required": o.required,
            }
            for o in cls.options
        ],
        "secrets": [
            {"name": s.name, "label": s.label, "help": s.help, "required": s.required}
            for s in cls.secrets
        ],
    }
