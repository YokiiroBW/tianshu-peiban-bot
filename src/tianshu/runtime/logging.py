"""Retain ordinary runtime diagnostics without serializing queued user arguments."""

import logging
import traceback


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


def configure():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(TaskMetadata())
