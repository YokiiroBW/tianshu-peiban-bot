"""Domain schema inputs for isolated fixtures and offline fresh-baseline assembly.

The running product installs runtime/baseline.sql and does not traverse these
historical definition fragments. Subsequent production upgrades append named
migrations after the frozen baseline.
"""
from importlib.resources import files
from tianshu.runtime.migrations import Migration, foundation_migrations, execution_migrations


def assembled_domain_migrations(jobs):
    """The sole installed product baseline; collecting it does not construct/start services."""
    resources = (
        ("0010_identity", "tianshu.identity", ("schema.sql",)),
        ("0011_sources", "tianshu.sources", ("schema.sql", "functions.sql")),
        ("0012_sources_rb", "tianshu.sources", ("0012_sources_rb.sql",)),
        ("0020_memory", "tianshu.memory", ("schema.sql",)),
        ("0021_memory_consolidation", "tianshu.memory", ("0021_memory_consolidation.sql",)),
        ("0022_memory_runtime", "tianshu.memory", ("0022_memory_runtime.sql",)),
        ("0023_memory_index_retry", "tianshu.memory", ("0023_memory_index_retry.sql",)),
        ("0024_memory_consolidation_retry", "tianshu.memory", ("0024_memory_consolidation_retry.sql",)),
        ("0025_memory_write", "tianshu.memory", ("0025_memory_write.sql",)),
        ("0026_memory_retrieval", "tianshu.memory", ("rb_retrieval.sql",)),
        ("0030_companion", "tianshu.companion", ("schema.sql",)),
        ("0031_life", "tianshu.life", ("schema.sql",)),
        ("0032_conversation", "tianshu.conversation", ("schema.sql",)),
        ("0033_life_contact_expiry", "tianshu.life", ("contact_expiry.sql",)),
        ("0034_companion_growth_batches", "tianshu.companion", ("growth_batches.sql",)),
        ("0035_life_planning_recovery", "tianshu.life", ("planning_recovery.sql",)),
        ("0036_conversation_expression_queue_timing", "tianshu.conversation", ("expression_queue_timing.sql",)),
        ("0037_companion_growth_retry", "tianshu.companion", ("growth_retry.sql",)),
        ("0038_life_computation_retry", "tianshu.life", ("0038_life_computation_retry.sql",)),
        ("0039_companion_learning", "tianshu.companion", ("habit_revisions.sql",)),
        ("0040_companion_remove_legacy_growth", "tianshu.companion", ("growth_remove_legacy.sql",)),
        ("0039_conversation_context_selection", "tianshu.conversation", ("context_selection.sql",)),
        ("0040_conversation_input_supplements", "tianshu.conversation", ("input_supplements.sql",)),
        ("0040_life_execution", "tianshu.life", ("rb_life_execution.sql",)),
        ("0041_life_experience", "tianshu.life", ("rb_experience.sql",)),
        ("capabilities-0001", "tianshu.capabilities", ("schema.sql",)),
        ("capabilities-0002-runtime-observations", "tianshu.capabilities", ("runtime_observations.sql",)),
        ("capabilities-projects-0001", "tianshu.capabilities.projects", ("schema.sql",)),
        ("capabilities-projects-0002-independent", "tianshu.capabilities.projects", ("independent.sql",)),
        ("channels-0001", "tianshu.channels", ("schema.sql",)),
        ("channels-0002-media-supplements", "tianshu.channels", ("media_supplements.sql",)),
        ("operations-0001", "tianshu.operations", ("schema.sql",)),
    )
    return (
        *foundation_migrations(),
        *execution_migrations(jobs),
        *(
            Migration(name, "\n".join(files(package).joinpath(part).read_text() for part in parts))
            for name, package, parts in resources
        ),
    )
