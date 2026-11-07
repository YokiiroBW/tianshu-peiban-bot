"""One model function selection rule, with explicit background defaults and real quotas."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from tianshu.runtime.errors import AppError
from .types import ModelRequest
from .timing import validate_timing, waiting_budget
from tianshu.runtime.commands import body_hash
from .route_requirements import compatible_request, no_compatible_route, rejection

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
    "ops.diagnose",
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
    primary_route_id: str | None = None
    selection_reason: str = "selected_binding"
    primary_protocol: str | None = None
    provider_revision: int | None = None


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
    cloud_concurrency: int | None = None

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
        if self.cloud_concurrency is not None and (type(self.cloud_concurrency) is not int or self.cloud_concurrency < 1):
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

    async def bound_binding(self, connection, request):
        # An independently bound check wins; otherwise retain the author's
        # existing vision selection, including its actor/global precedence.
        inherited = "vision" if request.function == "image.consistency" else request.function
        binding = await (await connection.execute(
            "SELECT route_id,fallback_routes FROM models.bindings WHERE service_class=%s "
            "AND function IN (%s,%s,'*') AND actor_id IN (%s,'') "
            "ORDER BY (function=%s) DESC,(function=%s) DESC,(actor_id=%s) DESC LIMIT 1",
            (request.service_class.value, request.function, inherited, request.context.actor_id or "",
             request.function, inherited, request.context.actor_id or ""))).fetchone()
        if not binding:
            raise AppError("model_function_not_configured", status=503,
                details={"function": request.function, "service_class": request.service_class.value})
        return binding

    async def bound_route(self, connection, request):
        return (await self.bound_binding(connection, request))["route_id"]

    async def resolve(self, request: ModelRequest, *, frozen_attempt=None) -> RoutePlan:
        if request.function not in FUNCTIONS:
            raise AppError("unknown_model_function")
        if (
            type(request.max_output_tokens) is not int
            or request.max_output_tokens < (0 if request.function in {"embed", "rerank"} else 1)
            or request.max_output_tokens > 2**31 - 1
        ):
            raise AppError("invalid_model_budget")
        validate_timing(request)
        probing = request.capability_probe and bool(
            {"*", "models.probe", "models.manage"} & request.context.permissions
        )
        async with self.database.transaction(waiting_budget(request), read_only=True) as conn:
            binding = None
            if frozen_attempt is not None:
                # A persisted queued attempt owns its original selected route.
                # It must not silently change when bindings or cooldowns change.
                route_id = frozen_attempt["route_id"]
            elif request.route_id:
                if request.native_payload is None and request.function != "ops.diagnose":
                    try:
                        binding = await self.bound_binding(conn, request)
                    except AppError:
                        binding = None
                if not ({"models.route", "*"} & request.context.permissions) and not (
                    request.capability_probe and "models.manage" in request.context.permissions
                ):
                    # A domain may freeze its own already selected binding. This
                    # does not grant arbitrary route selection, including after
                    # an author changes the binding while assembly is in flight.
                    if request.route_revision is None:
                        raise AppError("model_route_forbidden", status=403)
                    embedding_route = None
                    if request.function == "embed":
                        embedding = await (await conn.execute(
                            "SELECT route_id FROM models.embedding_profile WHERE singleton")).fetchone()
                        embedding_route = embedding["route_id"] if embedding else None
                    bound_route = binding and request.route_id in (binding["route_id"], *binding["fallback_routes"])
                    if request.route_id != embedding_route and not bound_route:
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
                try:
                    route_id = await self.bound_route(conn, request)
                except AppError as error:
                    if error.code != "model_function_not_configured":
                        raise
                    raise no_compatible_route([rejection(None, error)]) from error
            cursor = await conn.execute(
                "SELECT r.*,p.base_url,p.credential_ref,p.protocol,p.enabled AS provider_enabled,"
                "p.revision AS provider_revision "
                "FROM models.routes r JOIN models.providers p USING(provider_id) "
                "WHERE r.route_id=%s",
                (route_id,),
            )
            row = await cursor.fetchone()
            primary = row
            selection_reason = "selected_binding"
            primary_route_id = route_id
            # An authorized probe tests its explicit target, rather than moving
            # a business request from the bound primary to a configured fallback.
            if (binding and not probing and request.route_id in binding["fallback_routes"]
                    and request.route_id != binding["route_id"]):
                primary_route_id = binding["route_id"]
                selection_reason = "configured_fallback_selected"
            if request.function == "embed":
                profile = await (
                    await conn.execute("SELECT * FROM models.embedding_profile WHERE singleton")
                ).fetchone()
                if not profile:
                    raise AppError("embedding_profile_not_configured", status=503)
                if row and profile["route_fingerprint"] != embedding_fingerprint(row):
                    raise AppError("embedding_profile_rebuild_required", status=409)
            if frozen_attempt is not None and (not primary or any(
                    primary[key] != frozen_attempt[key] for key in ("route_id", "provider_id", "model", "protocol", "quota_group"))
                    or primary["revision"] != frozen_attempt["route_revision"]
                    or (frozen_attempt.get("provider_revision") is not None and
                        primary["provider_revision"] != frozen_attempt["provider_revision"])):
                raise AppError("model_route_changed_before_execution", status=409)
            if frozen_attempt is None and request.route_revision is not None and primary and request.route_revision != primary["revision"]:
                raise AppError("model_route_revision_conflict", status=409)
            cooldown = None
            route_cooldown = None
            if primary:
                cooldown = await (await conn.execute(
                    "SELECT blocked_until FROM models.quota_cooldowns WHERE quota_group=%s AND blocked_until>now()",
                    (primary["quota_group"],))).fetchone()
                route_cooldown = await (await conn.execute(
                    "SELECT blocked_until FROM models.route_cooldowns WHERE route_id=%s AND blocked_until>now()",
                    (primary_route_id,))).fetchone()
            # Native proxy aliases and explicitly privileged route choices must
            # stay exact. Backups are only those configured for this binding.
            pinned = request.route_id is not None and request.route_revision is not None
            may_fallback = (frozen_attempt is None and not pinned and request.function != "ops.diagnose" and request.native_payload is None
                            and (not request.route_id or request.allow_configured_fallback))
            source_protocol = request.protocol or (primary["protocol"] if primary else None)
            primary_error = None
            try:
                compatible_request(request, primary, source_protocol=source_protocol, probing=probing,
                    allow_adaptation=may_fallback or selection_reason == "configured_fallback_selected"
                    or frozen_attempt is not None and (not request.route_id or request.allow_configured_fallback))
            except AppError as error:
                primary_error = error
            rejections = [rejection(route_id, primary_error)] if primary_error else []
            if may_fallback and (primary_error or cooldown or route_cooldown):
                try:
                    binding = await self.bound_binding(conn, request)
                except AppError:
                    binding = None
                if binding and binding["route_id"] == primary_route_id:
                    seen = {route_id}
                    for candidate_id in binding["fallback_routes"]:
                        if candidate_id in seen:
                            continue
                        seen.add(candidate_id)
                        candidate = await (await conn.execute(
                            "SELECT r.*,p.base_url,p.credential_ref,p.protocol,p.enabled AS provider_enabled,"
                            "p.revision AS provider_revision "
                            "FROM models.routes r JOIN models.providers p USING(provider_id) WHERE r.route_id=%s",
                            (candidate_id,))).fetchone()
                        if not candidate or not candidate["enabled"] or not candidate["provider_enabled"]:
                            rejections.append({"route_id": candidate_id, "reason": "model_route_unavailable"})
                            continue
                        # A second key in the same quota domain is not an independent fallback.
                        if cooldown and candidate["quota_group"] == primary["quota_group"]:
                            rejections.append({"route_id": candidate_id, "reason": "primary_quota_cooldown"})
                            continue
                        blocked = await (await conn.execute(
                            "SELECT 1 FROM models.quota_cooldowns WHERE quota_group=%s AND blocked_until>now()",
                            (candidate["quota_group"],))).fetchone()
                        route_blocked = await (await conn.execute(
                            "SELECT 1 FROM models.route_cooldowns WHERE route_id=%s AND blocked_until>now()",
                            (candidate_id,))).fetchone()
                        if blocked or route_blocked:
                            rejections.append({"route_id": candidate_id, "reason": "model_route_cooldown"})
                            continue
                        if request.function == "embed":
                            if embedding_fingerprint(candidate) != profile["route_fingerprint"]:
                                rejections.append({"route_id": candidate_id, "reason": "embedding_profile_rebuild_required"})
                                continue
                        try:
                            compatible_request(request, candidate, source_protocol=source_protocol,
                                probing=probing, allow_adaptation=True)
                        except AppError as error:
                            rejections.append(rejection(candidate_id, error))
                            continue
                        row = candidate
                        if primary_error:
                            selection_reason = ("primary_unavailable" if primary_error.code == "model_route_unavailable"
                                                else "primary_" + primary_error.code.removeprefix("model_"))
                        else:
                            selection_reason = "primary_quota_cooldown" if cooldown else "primary_route_cooldown"
                        break
            if row is primary and primary_error:
                if may_fallback:
                    raise no_compatible_route(rejections) from primary_error
                raise primary_error
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
            primary_route_id,
            selection_reason,
            source_protocol or row["protocol"],
            row["provider_revision"],
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
