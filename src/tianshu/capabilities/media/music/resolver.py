"""Automatic, per-song entitlement routing across connected music accounts."""

from ...contracts import Fault, require
from collections import Counter

from .catalog import recording_mismatch, search_title
from .quality import LEVELS, aggregate, requested_levels
from .request_control import Deferred
from .comparison import defer_channel


class Resolver:
    def __init__(self, music):
        self.music, self.repo = music, music.media.repo

    def requests(self, job):
        # One consistent acquisition view, rather than a new connection/advisory lock per
        # request and per playlist. The worker still rechecks its version before completion.
        with self.repo.transaction() as db:
            current = self.repo._jobs(db, [job["job_id"]]).get(job["job_id"])
            requests = self.repo._documents(db, "music_request", current.get("request_ids", []))
            playlists = self.repo._documents(
                db, "music_playlist", [row["playlist_id"] for row in requests.values()]
            )
            members = {key: set(row["entries"]) for key, row in playlists.items()}
            policies = [
                playlists[row["playlist_id"]].get("quality", row["quality"])
                for row in requests.values()
                if row["source_id"] in members.get(row["playlist_id"], set())
            ]
            return aggregate(policies or [job["quality"]]), current.get("request_version", 0)

    def bind(self, provider, track, recording_id):
        catalog = self.music.library.catalog
        with self.repo.transaction() as db:
            binding = catalog.register(provider, track, db)
            # Existing explicit source assignments take precedence over search guesses.
            require(
                catalog.resolve(binding["recording_id"], db) == catalog.resolve(recording_id, db),
                "music_match_ambiguous",
                409,
            )
        return binding

    async def matching_tracks(self, account, cookie, original, recording_id, checked):
        """Share recording matching with lyrics, without requesting audio entitlements."""
        provider = account["provider"]
        if provider == original.get("provider"):
            return [original]
        local, catalog = self.music.media.local, self.music.library.catalog
        candidates = await local(catalog.sources, recording_id, provider)
        if not candidates:
            query = search_title(original) + " " + " ".join(original.get("artists", []))
            found = await self.music.client(provider).search(query, cookie)
            reasons = [recording_mismatch(original, t) for t in found]
            checked["search_results"] = len(found)
            checked["rejected_matches"] = dict(Counter(reason for reason in reasons if reason))
            candidates = list(
                {t["source_id"]: t for t, reason in zip(found, reasons) if reason is None}.values()
            )
            require(len(candidates) <= 1, "music_match_ambiguous", 409)
        for track in candidates:
            await local(self.bind, provider, track, recording_id)
        return candidates

    async def offers(self, job, *, report=None):
        local, library = self.music.media.local, self.music.library
        accounts = [
            a for a in await local(self.repo.list, "music_account") if a["state"] == "ready"
        ]
        report = report if report is not None else {}
        report.update(
            policy="highest_available", providers=[], candidates=[], deferred=[], complete=True
        )
        require(accounts, "music_auth_required", 409)
        offers, failures = [], []
        original = {**job["track"], "provider": job.get("provider", "netease")}
        stored = await local(library.catalog.source, job["source_key"])
        if stored and stored["metadata"].get("metadata_enriched"):
            original = {**stored["metadata"], "provider": original["provider"]}
        for account in accounts:
            if account["provider"] != original["provider"]:
                continue
            client = self.music.client(account["provider"])
            if hasattr(client, "enrich"):
                try:
                    _, cookie = await local(library.account, account["account_id"])
                    original = await client.enrich(original, cookie)
                    report["metadata_enriched"] = bool(original.get("metadata_enriched"))
                except Fault as error:
                    if isinstance(error, Deferred):
                        defer_channel(report, account, error, "metadata")
                    report["metadata_code"] = error.code
            break
        # Refresh the existing source's lookup key before binding another provider.
        await local(self.bind, original["provider"], original, job["recording_id"])
        for account in accounts:
            provider = account["provider"]
            client = self.music.client(provider)
            checked = {"provider": provider, "matches": 0, "available_levels": [], "failures": []}
            report["providers"].append(checked)
            blocked = next(
                (row for row in report["deferred"] if row["account_id"] == account["account_id"]),
                None,
            )
            if blocked:
                checked.update(code=blocked["code"], retry_at=blocked["retry_at"])
                continue
            try:
                account, cookie = await local(library.account, account["account_id"])
                candidates = await self.matching_tracks(
                    account, cookie, original, job["recording_id"], checked
                )
                checked["matches"] = len(candidates)
                for track in candidates:
                    for level in requested_levels(job["quality"]):
                        try:
                            offered = (
                                await client.download_track(track, level, cookie)
                                if hasattr(client, "download_track")
                                else await client.download(track["source_id"], level, cookie)
                            )
                            offers.append(
                                {
                                    "provider": provider,
                                    "track": {**track, "provider": provider},
                                    "account": account,
                                    "cookie": cookie,
                                    "offered": offered,
                                }
                            )
                            checked["available_levels"].append(offered["level"])
                            # One provider's best available offer is enough; actual audio is checked below.
                            break
                        except Fault as error:
                            if isinstance(error, Deferred):
                                raise
                            failures.append(error.code)
                            checked["failures"].append(error.code)
                            if error.code not in {
                                "music_download_forbidden",
                                "music_preview_rejected",
                                "music_quality_unavailable",
                            }:
                                break
            except (Fault, TimeoutError) as error:
                if isinstance(error, Deferred):
                    defer_channel(report, account, error, "entitlement")
                    checked.update(code=error.code, retry_at=error.until)
                    continue
                code = error.code if isinstance(error, Fault) else "music_upstream_unavailable"
                failures.append(code)
                checked["failures"].append(code)
        offers.sort(key=lambda item: LEVELS.get(item["offered"]["level"], -1), reverse=True)
        if any(
            code
            in {
                "music_upstream_unavailable",
                "music_download_unavailable",
                "music_upstream_rejected",
            }
            for code in failures
        ):
            report["complete"] = False
        if not offers:
            if report["deferred"]:
                # The caller may still reuse a local archive. With no archive it
                # waits for the earliest channel, without consuming a retry.
                return []
            if failures and all(
                code in {"music_auth_required", "music_account_unavailable"} for code in failures
            ):
                raise Fault("music_auth_required", 409)
            for code in (
                "music_upstream_unavailable",
                "music_download_unavailable",
                "music_upstream_rejected",
            ):
                if code in failures:
                    # One rejected entitlement must not hide another channel's failed query.
                    raise Fault(code, 502)
            if "music_download_forbidden" in failures or "music_preview_rejected" in failures:
                raise Fault("music_download_forbidden", 409)
            if failures:
                raise Fault(failures[0], 502)
            raise Fault("music_match_unavailable", 409)
        return offers
