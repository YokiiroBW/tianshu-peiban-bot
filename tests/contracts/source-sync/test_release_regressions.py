"""Independent negative expectations for the coordinator's confirmed counterexamples."""
import copy
import unittest

from multi_actor_model import verify_access, verify_actor_event, verify_first_mapping, validate
from relations import Violation, digest
from test_multi_actor import FIXTURE, NOW, current_access, sample


class SharedRuleRegressions(unittest.TestCase):
    def event_case(self, audience):
        snapshot = sample(audience)["facts"]
        owner = copy.deepcopy(FIXTURE["scenarios"][audience]["owner_turns"][0])
        return copy.deepcopy(owner["committed_event"]), owner, snapshot

    def test_matching_owner_payload_cannot_override_physical_reality(self):
        for audience in FIXTURE["scenarios"]:
            for reality in ("fictional", "mixed"):
                with self.subTest(audience=audience, reality=reality):
                    event, owner, snapshot = self.event_case(audience)
                    event["reality"] = owner["committed_event"]["reality"] = reality
                    with self.assertRaisesRegex(Violation, "reality"):
                        verify_actor_event(event, owner, snapshot)

    def test_current_aggregate_and_duplicate_sources_are_shared_constraints(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience, failure="aggregate"):
                event, owner, snapshot = self.event_case(audience)
                owner["aggregate_version"] = event["aggregate_version"] - 1
                with self.assertRaisesRegex(Violation, "owner_event"):
                    verify_actor_event(event, owner, snapshot)
            with self.subTest(audience=audience, failure="duplicate"):
                event, owner, snapshot = self.event_case(audience)
                event["sources"].append(copy.deepcopy(event["sources"][0]))
                owner["committed_event"] = copy.deepcopy(event)
                owner["input_sources"] = copy.deepcopy(event["sources"])
                with self.assertRaisesRegex(Violation, "duplicate_sources"):
                    verify_actor_event(event, owner, snapshot)

    def test_stale_physical_revision_receipt_and_retraction_are_rejected(self):
        for audience in FIXTURE["scenarios"]:
            for change in ("revision", "receipt", "withdrawn"):
                with self.subTest(audience=audience, change=change):
                    event, owner, snapshot = self.event_case(audience)
                    physical = snapshot["physicals"][0]
                    if change == "revision":
                        physical["revision"] += 1
                    elif change == "receipt":
                        physical["physical_receipt_id"] = "physical:other"
                    else:
                        physical.update(state="withdrawn", kind="retract")
                    validate("facts_response", snapshot)
                    with self.assertRaisesRegex(Violation, "stale_source"):
                        verify_actor_event(event, owner, snapshot)

    def test_legitimate_mixed_event_requires_distinct_classified_physicals(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                event, owner, snapshot = self.event_case(audience)
                physical = copy.deepcopy(snapshot["physicals"][0])
                physical["key"]["message_id"] = "message:fictional"
                physical["physical_receipt_id"] = "physical:fictional"
                physical["classification"]["value"] = "fictional"
                admission = copy.deepcopy(snapshot["admissions"][0])
                admission["selector"]["key"] = copy.deepcopy(physical["key"])
                admission["source"]["message_key"]["message_id"] = "message:fictional"
                admission["source"]["receipt_id"] = "receipt:fictional-actor-a"
                admission["physical_receipt_id"] = physical["physical_receipt_id"]
                snapshot["physicals"].append(physical)
                snapshot["admissions"].append(admission)
                event["sources"].append(copy.deepcopy(admission["source"]))
                event["reality"] = "mixed"
                owner["input_sources"] = copy.deepcopy(event["sources"])
                owner["committed_event"] = copy.deepcopy(event)
                validate("facts_response", snapshot)
                verify_actor_event(event, owner, snapshot)
                for classification in ("mixed", "unclassified"):
                    physical["classification"] = dict(value=classification, basis="unclassified" if classification == "unclassified" else "registered_input_mode", policy_ref=None if classification == "unclassified" else "mode:mixed", policy_version=None if classification == "unclassified" else 1)
                    with self.assertRaisesRegex(Violation, "reality"):
                        verify_actor_event(event, owner, snapshot)

    def test_viewer_issuer_must_be_platform(self):
        for audience in FIXTURE["scenarios"]:
            with self.subTest(audience=audience):
                data = sample(audience)
                request, response = current_access(data["facts"])
                scope = data["facts"]["admissions"][0]["scope"]
                request["viewer"] = dict(origin=dict(assertion_ref="viewer:a"), scope=scope)
                response["request_digest"] = digest(request)
                context = copy.deepcopy(data["authority"]["actor_contexts"][0])
                context.update(authenticated_service="companion", audience_service="memory", allowed_scope=scope, assertion_ref="viewer:a")
                response["viewer_context"] = context
                verify_access(request, response, data["facts"], NOW)
                context["issuer"] = "nonebot"
                with self.assertRaisesRegex(Violation, "viewer_actor"):
                    verify_access(request, response, data["facts"], NOW)

    def test_fanout_actor_set_and_receipt_admission_links(self):
        for audience in FIXTURE["scenarios"]:
            for change, expected in (("unrequested", "actor_coverage"), ("effective", "actor_coverage"), ("duplicate", "actor_coverage"), ("missing", "actor_coverage"), ("swapped_receipts", "actor_admission"), ("missing_grant", "actor_authorization")):
                with self.subTest(audience=audience, change=change):
                    data = sample(audience)
                    result = data["response"]
                    if change in {"unrequested", "effective"}:
                        result["outcomes"][1]["actor_id"] = "actor:unrequested"
                        if change == "effective":
                            result["effective_actor_ids"][1] = "actor:unrequested"
                    elif change == "duplicate":
                        result["outcomes"][1]["actor_id"] = result["outcomes"][0]["actor_id"]
                    elif change == "missing":
                        result["outcomes"].pop()
                    elif change == "swapped_receipts":
                        result["outcomes"][0]["receipt"], result["outcomes"][1]["receipt"] = result["outcomes"][1]["receipt"], result["outcomes"][0]["receipt"]
                    else:
                        data["authority"]["actor_contexts"].pop()
                    with self.assertRaisesRegex(Violation, expected):
                        verify_first_mapping(data["request"], result, FIXTURE["identity"], data["authority"], data["facts"]["admissions"], NOW)
