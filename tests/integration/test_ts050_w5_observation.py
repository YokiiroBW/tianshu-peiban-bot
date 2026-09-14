"""One passive W5 observation in an isolated run directory; does not supersede failure evidence."""

import test_ts050_source_scheduling as scheduling


class ObservedW5(scheduling.DebouncedSourceChain):
    pass
