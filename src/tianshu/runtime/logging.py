"""Retain ordinary runtime diagnostics without serializing queued user arguments."""

import logging
import json
import os
import traceback

from .rb_logs_output import SAFE_FIELDS, redact_text, secret_values


class TaskMetadata(logging.Filter):
    def filter(self, record):
        if record.name.startswith("procrastinate"):
            # Procrastinate formats call_string (including all kwargs) into normal INFO logs.
            # The application EventLog already records actual job lifecycle and safe identities.
            event = getattr(record, "action", "diagnostic")
            job_id = getattr(record, "job_id", None)
            task_name = getattr(record, "task_name", None)
            stack = ""
            if record.exc_info:
                stack = "\n" + "".join(traceback.format_tb(record.exc_info[2]))
                stack += record.exc_info[0].__name__
            record.msg = "queue event=%s job_id=%s task=%s%s"
            record.args = (event, job_id, task_name, stack)
            record.exc_info = None
            record.exc_text = None
        return True


class StructuredFormatter(logging.Formatter):
    """Keep severity and safe correlation metadata on one output line."""

    def __init__(self):
        super().__init__()
        self.secrets = secret_values(os.environ)

    def format(self, record):
        try:
            message = record.getMessage()
        except Exception:
            message = "[invalid diagnostic message]"
        value = {"level": record.levelname, "module": record.name,
                 "message": redact_text(message, self.secrets)}
        for field in SAFE_FIELDS:
            item = getattr(record, field, None)
            if item is not None and isinstance(item, (str, int, float, bool)):
                value[field] = redact_text(item, self.secrets) if isinstance(item, str) else item
        error_type = getattr(record, "error_type", None)
        if error_type is not None:
            value["error_family"] = redact_text(error_type, self.secrets)
        if record.exc_info:
            # Frame locations retain actionable context; exception messages can
            # contain input bodies, prompts, URLs or provider credentials.
            value["stack"] = redact_text("".join(traceback.format_tb(record.exc_info[2])), self.secrets)
            value["error_family"] = record.exc_info[0].__name__
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def configure():
    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.addFilter(TaskMetadata())
        handler.setFormatter(StructuredFormatter())
