import { useEffect, useState } from "react";
import { useAuth } from "../../app/Auth";
import { useDomainCommand } from "../../api/useDomainResource";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import type { Coverage } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { CoverageNote } from "./EvidenceDetails";
import { evidenceKinds } from "./types";

type RebuildResult = {
  queued: number;
  kinds: string[];
  next_cursor: string | null;
  coverage: Coverage;
  replayed?: boolean;
};
export function MemoryIndexRebuild() {
  const { actor } = useRoleWorkspace();
  const { session } = useAuth();
  const manageable = session?.principal?.permissions.some(
    (permission) => permission === "*" || permission === "memory.manage",
  );
  const [kinds, setKinds] = useState(Object.keys(evidenceKinds));
  const [page, setPage] = useState<RebuildResult | null>(null);
  useEffect(() => setPage(null), [actor]);
  const command = useDomainCommand<RebuildResult>(
    `memory-index:${actor}`,
    "actor",
  );
  async function rebuild(cursor: string | null) {
    const answer = await command.run("memory/index/rebuild", {
      actor_id: actor,
      kinds: [...kinds].sort(),
      ...(cursor ? { cursor } : {}),
    });
    if (answer) setPage(answer.data);
  }
  return (
    <section className="panel memory-index-rebuild">
      <h3>记忆索引重建</h3>
      <p>
        默认包含事实、可修订认识、事项、经历和反思。每次只排队一批，继续沿服务器游标读取；排队不代表索引已经完成。
      </p>
      <fieldset>
        <legend>重建类型</legend>
        {Object.entries(evidenceKinds).map(([kind, label]) => (
          <label className="inline-check" key={kind}>
            <input
              type="checkbox"
              checked={kinds.includes(kind)}
              disabled={command.busy}
              onChange={(event) => {
                setKinds((before) =>
                  event.target.checked
                    ? [...before, kind]
                    : before.filter((value) => value !== kind),
                );
                setPage(null);
              }}
            />
            {label}
          </label>
        ))}
      </fieldset>
      {manageable && (
        <div className="models-actions">
          <button
            className="button"
            disabled={!actor || !kinds.length || command.busy}
            onClick={() => void rebuild(null)}
          >
            重建所选索引
          </button>
          {page?.next_cursor && (
            <button
              className="button"
              disabled={command.busy}
              onClick={() => void rebuild(page.next_cursor)}
            >
              继续下一批索引
            </button>
          )}
        </div>
      )}
      {!actor && <p>先选择角色。</p>}
      {page && (
        <>
          <p role="status">
            {page.replayed
              ? "这次是原命令回执，未重复派发任务。"
              : `本批已排队 ${page.queued} 项。`}
          </p>
          <CoverageNote coverage={page.coverage} />
          <p className="muted">
            类型：
            {page.kinds.map((kind) => evidenceKinds[kind] ?? kind).join("、")}
          </p>
        </>
      )}
      <CommandFeedback {...command} />
    </section>
  );
}
