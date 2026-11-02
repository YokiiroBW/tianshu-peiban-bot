import pytest

from tianshu.models.retry import retry_delay_seconds, transient_model_failure


@pytest.mark.parametrize("code", [
    "timeout_unknown", "transport_unknown", "ServerDisconnectedError", "ClientPayloadError",
    "upstream_http_500", "upstream_http_503", "upstream_http_429", "http_502",
    "broker_restarted_after_submit", "queue_wait_expired", "execution_deadline_expired",
    "incomplete_native_stream", "reflection_outcome_unknown",
    "consumer_interrupted", "transport_interrupted", "broker_stopping",
])
def test_computation_transient_failure_can_be_scheduled_again(code):
    assert transient_model_failure(code)
    assert not transient_model_failure(code, cancelled=True)


@pytest.mark.parametrize("code", [
    None, "unclassified_failure", "reflection_output_invalid", "upstream_http_400",
    "upstream_http_401", "upstream_http_404", "model_function_not_configured",
    "source_unavailable", "result_source_unavailable", "source_recheck_timeout",
    "access_changed", "permission_denied", "cancelled_unknown", "cancelled_before_start",
    "business_deadline_expired", "attempt_identity_conflict", "invalid_input",
])
def test_permanent_or_explicitly_cancelled_work_is_not_retried(code):
    assert not transient_model_failure(code)


def test_computation_failure_backoff_grows_and_stays_bounded():
    assert [retry_delay_seconds(number) for number in range(1, 8)] == [30, 60, 120, 240, 480, 900, 900]
    assert retry_delay_seconds(10**9) == 900
    with pytest.raises(ValueError):
        retry_delay_seconds(0)
