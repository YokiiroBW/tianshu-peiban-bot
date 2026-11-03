"""Per-preparation counts and elapsed phases, stored with the existing batch."""

from contextlib import contextmanager
from copy import deepcopy
from time import perf_counter


CANDIDATE_GAP_CODES = 8
CANDIDATE_GAP_CODE_CHARACTERS = 96


def candidate_coverage_view(value):
    """Bound optional diagnostic prose; the manifest retains all original codes."""
    result=deepcopy(value)
    if "record_candidates_missing" in result:
        supplied=result["record_candidates_missing"]
        visible=[]
        omitted=0
        for code in supplied:
            if (isinstance(code,str) and 0<len(code)<=CANDIDATE_GAP_CODE_CHARACTERS
                    and all(character.isascii() and (character.isalnum() or character in "_.-") for character in code)
                    and len(visible)<CANDIDATE_GAP_CODES):
                visible.append(code)
            else:
                omitted+=1
        result["record_candidates_missing"]=visible or ["candidate_context_incomplete"]
        if omitted:
            result["record_candidate_gap_details_omitted"]=omitted
    if isinstance(result.get("record_omitted"),list):
        result["record_omitted"]=len(result["record_omitted"])
    return result


def candidate_coverage_reserve(max_records):
    """Finite shape for the diagnostic fields that a candidate read can add."""
    return {"record_candidates_missing":["x"*CANDIDATE_GAP_CODE_CHARACTERS]*CANDIDATE_GAP_CODES,
        "record_candidate_gap_details_omitted":2**63-1,
        "record_content_omitted":["r"+str(index) for index in range(max_records)],
        "record_omitted":max_records,"record_relations_omitted":2**63-1}


class PreparationMetrics:
    def __init__(self):
        self.started = perf_counter()
        self.values = {name: 0 for name in (
            "assembly_attempts", "trial_count", "source_reads", "conversation_reads", "identity_reads",
            "remote_calls", "remote_failures", "remote_degradations", "source_ms", "conversation_ms",
            "identity_ms", "assembly_ms", "local_records_ms", "remote_ms", "fit_ms", "route_ms",
        )}

    def count(self, name):
        self.values[name] += 1

    @contextmanager
    def phase(self, name):
        started = perf_counter()
        try:
            yield
        finally:
            self.values[name] += (perf_counter() - started) * 1000

    def snapshot(self):
        return {**{name: round(value, 3) if name.endswith("_ms") else value
            for name, value in self.values.items()}, "elapsed_ms": round((perf_counter()-self.started)*1000, 3)}
