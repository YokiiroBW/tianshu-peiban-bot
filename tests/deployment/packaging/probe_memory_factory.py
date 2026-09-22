"""Instrument the real product factory at the Store boundary; never construct a database."""

import sys

from tianshu_memory import app


class ConfigurationObserved(Exception):
    pass


observed = []


def store_boundary(path, **_):
    observed.append(path)
    raise ConfigurationObserved()


app.Store = store_boundary
try:
    # In this baseline this argument is not the source loaded by configured_app. The selected
    # environment points at the real test document; Store interception verifies that document's
    # database marker was parsed without opening it. Packaging binds both paths identically.
    app.runtime_app(sys.argv[1], None)
except ConfigurationObserved:
    raise SystemExit(0 if observed == [sys.argv[2]] else 1)
raise SystemExit(2)
