import type { source_ref } from "./runtimeTypes";

const owners = {
  companion: "生活记录",
  memory: "记忆记录",
  platform: "平台回执",
  assetlibrary: "资料原件",
};

/** References identify evidence; they do not imply that its full content is readable. */
export function LifeEvidence({ sources }: { sources: source_ref[] }) {
  return (
    <details className="life-evidence">
      <summary>来源依据（{sources.length}）</summary>
      {sources.length ? (
        <ul>
          {sources.map((source) => (
            <li key={`${source.owner}:${source.object_id}:${source.version}`}>
              {owners[source.owner]} · {source.object_id} · 版本{" "}
              {source.version}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">这条记录尚无来源引用，不能据此认定已经发生。</p>
      )}
    </details>
  );
}
