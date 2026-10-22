"""Four native Knowledge requests in one container/app/namespace, with serial controls.

All application requests use their normal authorization and capture/read/serve
path. Only the scheduling and clocks live here. Process CPU is a batch clock;
per-worker thread CPU overlaps that clock and must never be added to it.
"""

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common import arguments, emit, measure
from tianshu_memory.contracts import Contracts
from tianshu_memory.domain import canonical

CONCURRENCY = 4


class BatchFailure(RuntimeError):
    """Retain all four slot timings/errors without exporting request/result bodies."""

    def __init__(self, slots, measured, warmed):
        self.slots = slots
        super().__init__(
            canonical(
                {
                    "failed_concurrent_batch": slots,
                    "completed_measured_batches": measured,
                    "completed_warmup_batches": warmed,
                }
            )
        )


def measure_four(name, operation, count, *, warmup=5, check, cpu_clocks=False):
    measured, warmed = [], []

    def worker(slot, gate):
        started, thread = None, None
        result, error = None, None
        try:
            gate.wait(timeout=30)
            thread = time.thread_time_ns() if cpu_clocks else None
            started = time.perf_counter_ns()
            result = operation()
        except Exception as failure:
            error = {
                "type": type(failure).__name__,
                "code": getattr(failure, "code", None),
                "status": getattr(failure, "status", None),
                "phase": "native_operation" if started is not None else "barrier",
            }
        finally:
            wall = (
                (time.perf_counter_ns() - started) / 1e6
                if started is not None
                else None
            )
            thread_ms = (
                (time.thread_time_ns() - thread) / 1e6 if thread is not None else None
            )
        return {
            "slot": slot,
            "wall_ms": wall,
            "thread_cpu_ms": thread_ms,
            "error": error,
        }, result

    def completed(batch):
        index, outcomes = batch
        slots = []
        for timing, value in outcomes:
            timing = dict(timing)
            if timing["error"] is None:
                try:
                    check(value)
                except Exception as failure:
                    timing["error"] = {
                        "type": type(failure).__name__,
                        "code": getattr(failure, "code", None),
                        "status": getattr(failure, "status", None),
                        "phase": "untimed_result_check",
                    }
            slots.append(timing)
        (warmed if index < 0 else measured).append(slots)
        if any(slot["error"] is not None for slot in slots):
            raise BatchFailure(slots, measured, warmed)

    with ThreadPoolExecutor(
        max_workers=CONCURRENCY, thread_name_prefix="knowledge-ref"
    ) as executor:

        def batch(index):
            gate = threading.Barrier(CONCURRENCY)
            futures = [
                executor.submit(worker, slot, gate) for slot in range(CONCURRENCY)
            ]
            # Every slot finishes before checking any result or starting a new
            # batch. The operation and its body never run under a profiler.
            return index, [future.result() for future in futures]

        result = measure(
            name,
            batch,
            count,
            warmup=warmup,
            check=completed,
            cpu_clocks=cpu_clocks,
            operation_unit="four_request_batch",
            request_concurrency=CONCURRENCY,
            logical_request_count=count * CONCURRENCY,
            warmup_logical_request_count=warmup * CONCURRENCY,
            worker_clock_protocol={
                "wall": "perf_counter_ns: native request after all four slots meet the barrier",
                "thread_cpu": "thread_time_ns: executing worker only",
                "process_cpu": "batch process_time_ns only; never sampled or summed per worker",
                "batch_thread_cpu": "thread_time_ns: coordinator only, excludes executing workers",
                "batch_wall": "includes submission, barrier wait, native work and joining all slots",
            },
        )
    result["worker_request_timings"] = measured
    result["warmup_worker_request_timings"] = warmed
    latencies = [slot["wall_ms"] for batch in measured for slot in batch]
    result["worker_request_p50_ms"] = statistics.median(latencies)
    result["sample_dependence"] = (
        "Four correlated requests per batch; analyze by batch/block, not as independent process CPU samples"
    )
    return result


def main():
    from knowledge_reference import run_study

    args, private, _ = arguments()
    directory = (
        args.config.parent
        / "runs"
        / f"knowledge-reference-concurrent-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts/text-dialogue/v1")
    serial, details = run_study(
        directory,
        contracts,
        private,
        args.backend,
        "u" + args.namespace,
        count=3 if args.smoke else 20,
        cpu_clocks=args.cpu_clocks,
        concurrency=CONCURRENCY,
    )
    concurrent = details.pop("concurrent_results")
    emit(
        args,
        "knowledge_reference_concurrent",
        concurrent,
        serial_control_results=serial,
        denominators="results are concurrent batches; serial_control_results are individual native operations; keep them separate",
        **details,
    )


if __name__ == "__main__":
    main()
