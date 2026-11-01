"""Explicit CLI. Import/--help never connects to PostgreSQL or a model.

Run this file directly, including when it is mounted into the old production
image. --code-root selects that image/checkout's src before any product import.
"""

import argparse
import asyncio
from pathlib import Path
import subprocess
import sys
import tarfile
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evaluation.memory_closure.dataset import (COST_STORY_IDS, OLD_PRODUCTION_SHA,
        load_json, private_output, read_corpus, write_json)
else:
    from .dataset import (COST_STORY_IDS, OLD_PRODUCTION_SHA, load_json, private_output,
                          read_corpus, write_json)


OLD_PRODUCTION_IMAGE = "sha256:679e08db330d32973e40d32f07007ee5b0b3442a1f6e8fc86af550ed39aaa40c"


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    semantic = commands.add_parser("semantic", help="Serial real Broker run; root executes after integration")
    semantic.add_argument("--config", required=True, type=Path)
    semantic.add_argument("--corpus", required=True, type=Path)
    semantic.add_argument("--output", required=True, type=Path)
    semantic.add_argument("--private-root", required=True, type=Path)
    semantic.add_argument("--code-root", required=True, type=Path)
    semantic.add_argument("--source-sha", required=True)
    semantic.add_argument("--image-digest")
    semantic.add_argument("--variant", choices=("legacy", "conservative", "optimized"), required=True)
    semantic.add_argument("--stories", default="all", help="all, cost, development, or comma-separated IDs")
    semantic.add_argument("--run-id", default=None)
    compare = commands.add_parser("compare", help="Read and pair existing results; never calls a model")
    for name in ("legacy", "conservative", "optimized"):
        compare.add_argument("--" + name, required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    compare.add_argument("--private-root", required=True, type=Path)
    archive = commands.add_parser("prepare-baseline", help="Optional pinned git archive, no program execution")
    archive.add_argument("--repo", required=True, type=Path)
    archive.add_argument("--output", required=True, type=Path)
    archive.add_argument("--private-root", required=True, type=Path)
    return result


def provenance(args):
    if len(args.source_sha) != 40 or any(char not in "0123456789abcdef" for char in args.source_sha):
        raise ValueError("full_source_sha_required")
    if args.variant == "legacy" and args.source_sha != OLD_PRODUCTION_SHA:
        raise ValueError("legacy_must_use_actual_production_sha")
    if args.variant == "legacy" and args.image_digest and args.image_digest != OLD_PRODUCTION_IMAGE:
        raise ValueError("legacy_image_not_pinned_production_image")
    root = args.code_root.resolve()
    if not (root / "src" / "tianshu" / "runtime" / "product.py").is_file():
        raise ValueError("selected_product_source_root_required")
    git_path = root / ".git"
    stamp = root / ".memory-closure-baseline.json"
    if git_path.exists():
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        changes = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain",
                                            "--untracked-files=normal", "--", "src"], text=True)
        if actual != args.source_sha or changes.strip():
            raise ValueError("selected_product_source_must_match_clean_committed_sha")
        authority = "verified_clean_git_source"
    elif stamp.is_file():
        if load_json(stamp)["source_sha"] != args.source_sha:
            raise ValueError("baseline_archive_sha_mismatch")
        authority = "pinned_git_archive"
    elif args.image_digest:
        authority = "operator_supplied_immutable_image_digest_and_source_sha"
    else:
        raise ValueError("source_provenance_required")
    return {"source_sha": args.source_sha, "image_digest": args.image_digest,
            "authority": authority, "code_root": str(root)}


def selected_stories(stories, selection):
    if selection == "all":
        if len(stories) != 24 or sum(story.split == "held_out" for story in stories) != 8:
            raise ValueError("expected_24_stories_including_8_held_out")
        return stories
    if selection == "development":
        return tuple(story for story in stories if story.split == "development")
    ids = COST_STORY_IDS if selection == "cost" else tuple(selection.split(","))
    found = {story.id: story for story in stories}
    if len(set(ids)) != len(ids) or not set(ids) <= found.keys():
        raise ValueError("unknown_or_duplicate_story_selection")
    if selection == "cost" and any(found[key].split != "development" for key in ids):
        raise ValueError("cost_subset_must_be_development_only")
    return tuple(found[key] for key in ids)


async def semantic(args):
    code = provenance(args)
    output = private_output(args.output, args.private_root)
    if output.exists():
        raise ValueError("new_run_output_required; existing_attempts_are_observed_not_replaced")
    config = load_json(args.config)
    if config.get("synthetic_material_only") is not True:
        raise ValueError("explicit_synthetic_material_config_required")
    stories, corpus_hash = read_corpus(args.corpus)
    stories = selected_stories(stories, args.stories)
    # Product imports resolve only from the selected image/checkout, never from
    # the mounted runner's candidate worktree. Each CLI invocation is one process.
    sys.path.insert(0, str(args.code_root.resolve() / "src"))
    if __package__ in {None, ""}:
        from evaluation.memory_closure.runtime import run_story
    else:
        from .runtime import run_story
    import tianshu.runtime.product
    if args.code_root.resolve() not in Path(tianshu.runtime.product.__file__).resolve().parents:
        raise ValueError("product_import_did_not_use_selected_code_root")
    output.mkdir(parents=True)
    run_id = args.run_id or uuid4().hex
    manifest = {"run_id": run_id, "corpus_hash": corpus_hash, "variant": args.variant,
                "code": code, "story_ids": [story.id for story in stories], "reports": [],
                "semantic_result": "pending_human_review", "status": "running"}
    write_json(output / "run.json", manifest)
    for story in stories:
        report = await run_story(story, corpus_hash, config, output / story.id,
                                 run_id + ":" + story.id, args.variant, code)
        manifest["reports"].append({"story_id": story.id, "state": report["state"],
                                    "report": story.id + "/report.json"})
        write_json(output / "run.json", manifest)
    failed = any(item["state"] == "failed_or_unknown" for item in manifest["reports"])
    manifest["status"] = "finished_with_execution_failures" if failed else "completed_for_human_review"
    write_json(output / "run.json", manifest)
    return 2 if failed else 0


def read_results(directory, variant):
    manifest = load_json(directory / "run.json")
    if manifest["variant"] != variant:
        raise ValueError("cost_variant_manifest_mismatch")
    return {item["story_id"]: load_json(directory / item["report"])
            for item in manifest["reports"]}


def compare(args):
    results = {variant: read_results(getattr(args, variant), variant)
               for variant in ("legacy", "conservative", "optimized")}
    output = private_output(args.output, args.private_root)
    pairs = []
    for story_id in COST_STORY_IDS:
        arms = {variant: results[variant].get(story_id) for variant in results}
        if any(value is None for value in arms.values()):
            raise ValueError("missing_cost_story:" + story_id)
        if len({value["comparison_key"] for value in arms.values()}) != 1:
            raise ValueError("cost_inputs_or_arrival_schedule_differ:" + story_id)
        if len({value["execution_config_fingerprint"] for value in arms.values()}) != 1:
            raise ValueError("cost_model_authority_or_common_execution_settings_differ:" + story_id)
        if arms["legacy"]["code"]["source_sha"] != OLD_PRODUCTION_SHA:
            raise ValueError("cost_legacy_not_actual_production")
        if arms["conservative"]["code"]["source_sha"] != arms["optimized"]["code"]["source_sha"]:
            raise ValueError("new_policy_arms_must_use_same_product_code")
        pairs.append({"story_id": story_id, "comparison_key": arms["legacy"]["comparison_key"],
            "arms": {variant: {"usage": report.get("model_usage"), "state": report["state"],
                "elapsed_ms": report.get("elapsed_ms"), "answer_elapsed_ms": report.get("answer_elapsed_ms"),
                "index_timings": report.get("index_timings"), "answer": report.get("answer"),
                "subjects": report.get("subjects"), "episode_coverage": report.get("episode_coverage"),
                "batch_results": report.get("batch_results"),
                "code": report["code"], "run_id": report["run_id"], "reused_existing_result": True}
                for variant, report in arms.items()}, "quality_review": "pending_human_review"})
    write_json(output, {"denominator": list(COST_STORY_IDS), "pairs": pairs,
        "optimized_samples": "references_same_8_from_completed_24; no_extra_calls_or_independent_sample_claim",
        "actual_fee": None, "semantic_verdict": "pending_human_review",
        "note": "Compare per-person coverage/answers as well as tokens. Unknown usage stays unknown; failures included."})


def prepare_baseline(args):
    output = private_output(args.output, args.private_root)
    if output.exists():
        raise ValueError("new_baseline_directory_required")
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_path = output.with_suffix(".tar")
    if archive_path.exists():
        raise ValueError("baseline_archive_path_exists")
    subprocess.run(["git", "-C", str(args.repo), "archive", "--format=tar", "-o", str(archive_path),
                    OLD_PRODUCTION_SHA], check=True)
    try:
        with tarfile.open(archive_path) as archive:
            archive.extractall(output, filter="data")
        write_json(output / ".memory-closure-baseline.json", {"source_sha": OLD_PRODUCTION_SHA,
            "method": "git_archive_actual_complete_production_code", "executed": False})
    finally:
        archive_path.unlink(missing_ok=True)


def main():
    args = parser().parse_args()
    try:
        if args.command == "semantic":
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            return asyncio.run(semantic(args))
        elif args.command == "compare":
            compare(args)
        else:
            prepare_baseline(args)
    except Exception as error:
        # Never print private configuration/URL/DSN values in a traceback.
        print(getattr(error, "code", str(error) if isinstance(error, ValueError)
                      else type(error).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
