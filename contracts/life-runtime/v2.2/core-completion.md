# Core completion additive projection

The existing read port adds resource=autonomy. It returns the owner actual goal,
reason, step checkpoint and operation state, with original authorization and
scope filtering. Null fields mean no observed value; planned or unknown work is
never reported as completed.

The existing manage port adds actor-memory.import for explicit bounded history
delivery from activities, writing, images or contacts. A batch selects at most
32 owner records, using the original actor and owner-specific after cursor.
The result adds next_cursor/imported/skipped/complete only for this operation.
Each actual fact reuses the normal source identity/outbox and original outcome;
import does not reread diary prose, synthesize real actions or scan on startup.

This additive candidate retains old user dialogue and original Life operations.
It excludes new Bilibili/Xiaoheihe account adapters. Production and real-platform
acceptance remain separate from isolated contract/schema checks.
