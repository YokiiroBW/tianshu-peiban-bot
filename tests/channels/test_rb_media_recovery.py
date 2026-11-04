"""Only recoverable media failures consume another bounded attempt."""
from tianshu.channels.supplements import failure_category, failure_state
from tianshu.runtime.recovery_policy import FailureCategory


def test_media_failure_classification_and_total_attempt_budget():
    assert failure_state("provider_media_mapping_required", 0) == "waiting_config"
    assert failure_state("channel_media_unavailable", 0) == "waiting_retry"
    assert failure_category("supplement_worker_interrupted") == FailureCategory.INFRASTRUCTURE
    assert failure_state("supplement_worker_interrupted", 1) == "waiting_retry"
    assert failure_state("provider_media_path", 0) == "unavailable"
    assert failure_state("unsupported_provider_format", 0) == "unavailable"
    assert failure_state("provider_media_mapping_required", 2) == "unavailable"
    assert failure_state("channel_media_unavailable", 2) == "unavailable"
