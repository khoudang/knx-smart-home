from __future__ import annotations

import json
import logging
from typing import Any


LOGGER = logging.getLogger("aicontrol.intent_pipeline")
_CONFIGURED = False
_HANDLER_ATTR = "_aicontrol_intent_pipeline"


def configure_pipeline_logging(
    *,
    stream=None,
    level: int = logging.INFO,
    enabled: bool = True,
) -> None:
    global _CONFIGURED
    LOGGER.setLevel(level)
    LOGGER.propagate = False

    for handler in list(LOGGER.handlers):
        if getattr(handler, _HANDLER_ATTR, False):
            LOGGER.removeHandler(handler)

    if enabled:
        handler = logging.StreamHandler(stream)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(handler, _HANDLER_ATTR, True)
        LOGGER.addHandler(handler)
    _CONFIGURED = True


def configure_intent_pipeline_logging(**kwargs) -> None:
    configure_pipeline_logging(**kwargs)


def log_pipeline_event(**fields: Any) -> None:
    safe_fields = {key: value for key, value in fields.items() if value is not None}
    safe_fields.setdefault("component", "intent_pipeline")
    LOGGER.info(json.dumps(safe_fields, ensure_ascii=False, sort_keys=True))
