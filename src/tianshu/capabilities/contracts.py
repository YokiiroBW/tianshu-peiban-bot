"""Validation shared by capability-owned external protocols."""
import hashlib
import json
import math
from datetime import datetime, timezone
from tianshu.runtime.errors import AppError

class Fault(AppError):
    def __init__(self, code="invalid_input", status=400):
        super().__init__(code, status=status)

def require(condition, code="forbidden", status=403):
    if not condition:
        raise Fault(code,status)

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def loads(raw):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value: raise ValueError("duplicate_key")
            value[key]=item
        return value
    def finite(value):
        number=float(value)
        if not math.isfinite(number): raise ValueError("non_finite")
        return number
    try:
        return json.loads(raw,object_pairs_hook=pairs,parse_constant=finite,parse_float=finite)
    except (ValueError,UnicodeError,RecursionError):
        raise Fault() from None

def epoch(value):
    return datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()

def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace("+00:00","Z")
