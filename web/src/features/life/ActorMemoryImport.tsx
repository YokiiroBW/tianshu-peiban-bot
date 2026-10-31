import { useState } from "react";
import { useLifeAction } from "./useLifeRuntime";
import { RuntimeFeedback } from "./RuntimeFeedback";
import type { LifeAccess } from "./runtimeApi";

export function ActorMemoryImport({
  access,
  updated,
}: {
  access: LifeAccess;
  updated: () => void;
}) {
  const [owner, setOwner] = useState("activities");
  const [after, setAfter] = useState<string | null>(null);
  const [result, setResult] = useState<{
    imported: number;
    skipped: number;
    complete: boolean;
  } | null>(null);
  const action = useLifeAction(access, updated);
  async function run() {
    const receipt = await action.run(
      "actor-memory.import",
      { owner, after, limit: 32 },
      0,
    );
    if (receipt && receipt.result.state !== "unknown") {
      const next = receipt.result;
      setAfter(next.next_cursor ?? null);
      setResult({
        imported: next.imported ?? 0,
        skipped: next.skipped ?? 0,
        complete: next.complete === true,
      });
    }
  }
  return (
    <details className="life-evidence">
      <summary>导入已核验的历史经历</summary>
      <div className="life-form">
        <p>
          按原记录所属模块分批处理，每批最多 32 项；不会扫描或改写用户记忆。
        </p>
        <label>
          历史记录模块
          <select
            value={owner}
            disabled={action.busy}
            onChange={(event) => {
              setOwner(event.target.value);
              setAfter(null);
              setResult(null);
            }}
          >
            <option value="activities">历史活动</option>
            <option value="writing">作品</option>
            <option value="images">图片</option>
            <option value="contacts">交流记录</option>
          </select>
        </label>
        <button
          className="button"
          disabled={action.busy || result?.complete === true}
          onClick={() => void run()}
        >
          {after ? "继续导入下一批" : "导入一批历史记录"}
        </button>
        {result && (
          <p>
            本批已导入 {result.imported} 项，跳过 {result.skipped} 项 ·{" "}
            {result.complete ? "本模块已处理完毕" : "仍有下一批"}
          </p>
        )}
        <RuntimeFeedback {...action} />
      </div>
    </details>
  );
}
