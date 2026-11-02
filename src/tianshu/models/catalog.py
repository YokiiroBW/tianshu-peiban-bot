"""One model function selection rule, with explicit background defaults and real quotas."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from tianshu.runtime.errors import AppError
from .types import ModelRequest
from .timing import validate_timing, waiting_budget
from tianshu.runtime.commands import body_hash

FUNCTIONS = (
    "chat",
    "tools",
    "code",
    "search",
    "writing",
    "participation",
    "life.plan",
    "memory.extract",
    "reflect",
    "embed",
    "rerank",
    "vision",
    "asr",
    "tts",
    "reaction.annotate",
    "reaction.search",
    "image.plan",
    "image.consistency",
    "video.observe",
)
NATIVE_PROTOCOLS = frozenset(
    {
        "openai.chat",
        "openai.responses",
        "anthropic.messages",
        "gemini.generate",
        "openai.embedding",
        "cohere.rerank",
    }
)


def embedding_fingerprint(row):
    return body_hash(
        {key: row[key] for key in ("provider_id", "model", "protocol", "base_url", "parameters")}
    )


@dataclass(frozen=True, slots=True)
class RoutePlan:
    route_id: str
    revision: int
    provider_id: str
    model: str
    protocol: str
    base_url: str
    credential_ref: str
    quota_group: str
    verified_capabilities: frozenset[str]
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    quota_group: str
    concurrency: int
    interactive_reserve: int
    background_max: int
    rpm: int | None = None
    tpm: int | None = None
    max_queue: int = 128
    max_wait_seconds: float | None = None
    unknown_hold_seconds: float = 300
    unit_limits: Mapping[str, float] = field(default_factory=dict)
    interactive_rpm_reserve: int = 0
    background_rpm_max: int | None = None
    interactive_unit_reserve: Mapping[str, float] = field(default_factory=dict)
    background_unit_max: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self):
        for value in (
            self.concurrency,
            self.interactive_reserve,
            self.background_max,
            self.max_queue,
            self.interactive_rpm_reserve,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("invalid_quota_limit")
        for value in (self.rpm, self.tpm, self.background_rpm_max):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("invalid_quota_limit")
        waits = [self.unknown_hold_seconds]
        if self.max_wait_seconds is not None:
            waits.append(self.max_wait_seconds)
        for value in waits:
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid_quota_wait")
        for mapping in (self.unit_limits, self.interactive_unit_reserve, self.background_unit_max):
            if any(
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
                for value in mapping.values()
            ):
                raise ValueError("invalid_quota_units")
        if self.concurrency < 1 or not 0 <= self.interactive_reserve <= self.concurrency:
            raise ValueError("invalid_quota_concurrency")
        if not 0 <= self.background_max <= self.concurrency:
            raise ValueError("invalid_background_concurrency")
        if self.rpm is not None and not 0 <= self.interactive_rpm_reserve <= self.rpm:
            raise ValueError("invalid_rpm_reserve")
        limits = {**self.unit_limits}
        if self.tpm is not None:
            limits["tokens"] = self.tpm
        if any(
            value < 0 or value > limits.get(unit, 0)
            for unit, value in self.interactive_unit_reserve.items()
        ):
            raise ValueError("invalid_unit_reserve")


class ModelCatalog:
    def __init__(self, database):
        self.database = database

    async def bound_route(self, connection, request):
        # An independently bound check wins; otherwise retain the author's
        # existing vision selection, including its actor/global precedence.
        inherited = "vision" if request.function == "image.consistency" else request.function
        binding = await (await connection.execute(
            "SELECT route_id FROM models.bindings WHERE service_class=%s "
            "AND function IN (%s,%s,'*') AND actor_id IN (%s,'') "
            "ORDER BY (function=%s) DESC,(function=%s) DESC,(actor_id=%s) DESC LIMIT 1",
            (request.service_class.value, request.function, inherited, request.context.actor_id or "",
             request.function, inherited, request.context.actor_id or ""))).fetchone()
        if not binding:
            raise AppError("model_function_not_configured", status=503,
                details={"function": request.function, "service_class": request.service_class.value})
        return binding["route_id"]

    async def resolve(self, request: ModelRequest) -> RoutePlan:
        if request.function not in FUNCTIONS:
            raise AppError("unknown_model_function")
        if (
            type(request.max_output_tokens) is not int
            or request.max_output_tokens < (0 if request.function in {"embed", "rerank"} else 1)
            or request.max_output_tokens > 2**31 - 1
        ):
            raise AppError("invalid_model_budget")
        validate_timing(request)
        async with self.database.transaction(waiting_budget(request), read_only=True) as conn:
            if request.route_id:
                if not ({"models.route", "*"} & request.context.permissions) and not (
                    request.capability_probe and "models.manage" in request.context.permissions
                ):
                    # A domain may freeze its own already selected binding. This
                    # does not grant arbitrary route selection, including after
                    # an author changes the binding while assembly is in flight.
                    if request.route_revision is None:
                        raise AppError("model_route_forbidden", status=403)
                    if await self.bound_route(conn, request) != request.route_id:
                        raise AppError("model_route_binding_changed", status=409)
                route_id = request.route_id
            elif request.function == "embed":
                profile = await (
                    await conn.execute("SELECT * FROM models.embedding_profile WHERE singleton")
                ).fetchone()
                if not profile:
                    raise AppError("embedding_profile_not_configured", status=503)
                route_id = profile["route_id"]
            else:
                route_id = await self.bound_route(conn, request)
            cursor = await conn.execute(
                "SELECT r.*,p.base_url,p.credential_ref,p.protocol,p.enabled AS provider_enabled "
                "FROM models.routes r JOIN models.providers p USING(provider_id) "
                "WHERE r.route_id=%s",
                (route_id,),
            )
            row = await cursor.fetchone()
            if request.function == "embed":
                profile = await (
                    await conn.execute("SELECT * FROM models.embedding_profile WHERE singleton")
                ).fetchone()
                if not profile or profile["route_id"] != route_id:
                    raise AppError("embedding_profile_not_configured", status=503)
                if row and profile["route_fingerprint"] != embedding_fingerprint(row):
                    raise AppError("embedding_profile_rebuild_required", status=409)
        if not row or not row["enabled"] or not row["provider_enabled"]:
            raise AppError("model_route_unavailable", status=503)
        if request.route_revision is not None and request.route_revision != row["revision"]:
            raise AppError("model_route_revision_conflict", status=409)
        if request.protocol and request.protocol != row["protocol"]:
            raise AppError("native_protocol_mismatch", status=422)
        probing = request.capability_probe and bool(
            {"*", "models.probe", "models.manage"} & request.context.permissions
        )
        if request.required_capabilities - set(row["verified_capabilities"]) and not probing:
            raise AppError(
                "model_capability_unverified",
                status=422,
                details={
                    "missing": sorted(
                        request.required_capabilities - set(row["verified_capabilities"])
                    )
                },
            )
        return RoutePlan(
            row["route_id"],
            row["revision"],
            row["provider_id"],
            row["model"],
            row["protocol"],
            row["base_url"],
            row["credential_ref"],
            row["quota_group"],
            frozenset(row["verified_capabilities"]),
            row["parameters"],
        )

    async def policies(self) -> dict[str, QuotaPolicy]:
        async with self.database.transaction(read_only=True) as conn:
            cursor = await conn.execute("SELECT * FROM models.quota_groups")
            rows = await cursor.fetchall()
        return {
            row["quota_group"]: QuotaPolicy(
                **{key: value for key, value in row.items() if key != "revision"}
            )
            for row in rows
        }
