import { useId, useState } from "react";
import type { Page } from "../../api/contracts";
import {
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import {
  CoverageNote,
  EvidenceDetails,
  EvidenceRelations,
} from "./EvidenceDetails";
import { useMemoryCursor } from "./pagination";
import {
  evidenceKinds,
  type EvidenceUnit,
  type MemoryTimeQuery,
} from "./types";
export function MemoryEvidenceSearch({
  scope,
  query,
}: {
  scope: DomainTarget;
  query: MemoryTimeQuery;
}) {
  const modeLabelId = useId();
  const [text, setText] = useState("");
  const [mode, setMode] = useState("list");
  const [kinds, setKinds] = useState(Object.keys(evidenceKinds));
  const [filter, setFilter] = useState({
    query: "",
    coverage_mode: "list",
    evidence_kinds: Object.keys(evidenceKinds),
  });
  const plan = { ...filter, ...query };
  const cursor = useMemoryCursor({ ...scope, plan }, "query");
  const view = useDomainResource<Page<EvidenceUnit>>(
    "memory/query",
    {
      ...scope,
      plan,
      cursor: cursor.cursor,
      limit: filter.coverage_mode === "ranked" ? 32 : 40,
    },
    !!scope.actor_id,
  );
  return (
    <section className="memory-review">
      <h3>完整证据检索</h3>
      <form
        className="memory-edit"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter({
            query: text,
            coverage_mode: mode,
            evidence_kinds: kinds,
          });
        }}
      >
        <label>
          检索内容
          <input
            type="search"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <label>
          <span id={modeLabelId}>读取方式</span>
          <select
            aria-labelledby={modeLabelId}
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value="list">完整筛选列表（有界分页）</option>
            <option value="ranked">相关候选（不表示完整）</option>
          </select>
        </label>
        <fieldset>
          <legend>证据类型</legend>
          {Object.entries(evidenceKinds).map(([kind, label]) => (
            <label className="inline-check" key={kind}>
              <input
                type="checkbox"
                checked={kinds.includes(kind)}
                onChange={(event) =>
                  setKinds((before) =>
                    event.target.checked
                      ? [...before, kind]
                      : before.filter((value) => value !== kind),
                  )
                }
              />
              {label}
            </label>
          ))}
        </fieldset>
        <p className="muted">
          完整列表的空检索读取所选范围；有文字时按字面筛选。相关候选只选择有界结果，不能据此声称列全。
        </p>
        <button className="button" disabled={!kinds.length || view.loading}>
          读取证据
        </button>
      </form>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && <p role="status">正在读取完整证据单元…</p>}
      {view.data && (
        <>
          <CoverageNote coverage={view.data.coverage} />
          <ul>
            {view.data.items.map((item) => (
              <li key={`${item.kind}:${item.id}:${item.revision}`}>
                <h4>
                  {evidenceKinds[item.kind] ?? item.kind} · {item.id}
                </h4>
                <p className="memory-prewrap">{item.content}</p>
                <EvidenceDetails item={item} scope={scope} />
                <EvidenceRelations relations={item.relations} scope={scope} />
                <SourceEvidence sources={item.source_refs} scope={scope} />
                <CoverageNote coverage={item.coverage} />
              </li>
            ))}
          </ul>
          {!view.data.items.length && <p>当前范围没有匹配证据。</p>}
        </>
      )}
      <div className="models-actions">
        {cursor.previous && (
          <button
            className="button"
            disabled={view.loading}
            onClick={cursor.back}
          >
            上一批证据
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            disabled={view.loading}
            onClick={() => cursor.next(view.data!.next_cursor!)}
          >
            下一批证据
          </button>
        )}
      </div>
    </section>
  );
}
