import { useState } from "react";
import {
  GLOBAL_SCOPE,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { ImageWorkflow, WorkflowBinding, WorkflowGraph } from "./types";

type Candidate = WorkflowBinding & { value: string | number };
type Inspection = {
  candidates: Record<string, Candidate[]>;
  outputs: { node: string; title: string }[];
  graph_version: string;
};
const labels: Record<string, string> = {
  positive: "画面提示",
  negative: "排除内容",
  character: "角色",
  outfit: "穿搭",
  pose: "姿势",
  background: "背景",
  camera: "镜头",
  reference: "参考图1",
  reference2: "参考图2",
  reference3: "参考图3",
  seed: "随机种子",
  steps: "步数",
  width: "宽度",
  height: "高度",
};
const target = (value: WorkflowBinding) =>
  JSON.stringify([value.node, value.class_type, value.input]);
export function WorkflowEditor({
  workflow,
  refresh,
}: {
  workflow?: ImageWorkflow;
  refresh: () => Promise<void>;
}) {
  const [graph, setGraph] = useState<WorkflowGraph | null>(
    workflow?.graph ?? null,
  );
  const [bindings, setBindings] = useState<Record<string, WorkflowBinding>>(
    workflow?.bindings ?? {},
  );
  const [outputs, setOutputs] = useState<string[]>(workflow?.outputs ?? []);
  const [error, setError] = useState("");
  const inspection = useDomainResource<Inspection>(
    "images/workflows.inspect",
    { ...GLOBAL_SCOPE, graph },
    !!graph,
  );
  const command = useDomainCommand(
    "workflow:" + (workflow?.id ?? "new"),
    "global",
  );
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!graph) return;
        const data = new FormData(event.currentTarget);
        void command
          .run(
            "images/workflows.import",
            {
              id: workflow?.id ?? null,
              name: String(data.get("name")),
              graph,
              bindings,
              outputs,
            },
            workflow?.revision ?? 0,
          )
          .then((answer) => {
            if (answer) void refresh();
          });
      }}
    >
      <label>
        工作流名称
        <input name="name" defaultValue={workflow?.name ?? ""} required />
      </label>
      <label>
        ComfyUI API JSON
        <input
          type="file"
          accept=".json,application/json"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setError("");
            void file
              .text()
              .then((raw) => {
                const parsed = JSON.parse(raw) as WorkflowGraph;
                if (
                  !parsed ||
                  typeof parsed !== "object" ||
                  Array.isArray(parsed)
                )
                  throw new Error("请选择ComfyUI API导出对象。");
                for (const node of Object.values(parsed)) {
                  for (const value of Object.values(node.inputs ?? {})) {
                    if (
                      typeof value === "number" &&
                      Number.isInteger(value) &&
                      !Number.isSafeInteger(value)
                    )
                      throw new Error(
                        "工作流包含浏览器不能精确表示的超大整数，请将该种子改为安全整数后重新导出。",
                      );
                  }
                }
                setGraph(parsed);
                setBindings({});
                setOutputs([]);
              })
              .catch((cause) =>
                setError(
                  cause instanceof Error ? cause.message : "工作流无法读取。",
                ),
              );
          }}
        />
      </label>
      {inspection.loading && <p role="status">正在读取后端语义绑定目录…</p>}
      {inspection.error && <p role="alert">{inspection.error}</p>}
      {inspection.data && (
        <>
          <p>
            图版本 {inspection.data.graph_version}
            。节点候选由服务端唯一工作流规则产生，固定模型、艺术风格与其他节点保持原图。
          </p>
          <div className="domain-grid">
            {Object.entries(inspection.data.candidates).map(
              ([semantic, candidates]) => {
                const selected = bindings[semantic];
                return (
                  <div className="domain-record" key={semantic}>
                    <label>
                      {labels[semantic] ?? semantic}
                      <select
                        value={selected ? target(selected) : ""}
                        onChange={(event) => {
                          const candidate = candidates.find(
                            (value) => target(value) === event.target.value,
                          );
                          setBindings((current) => {
                            const next = { ...current };
                            if (candidate) {
                              const { value, ...binding } = candidate;
                              next[semantic] = {
                                ...binding,
                                ...(typeof value === "number"
                                  ? {
                                      min: candidate.min ?? 0,
                                      max: Math.min(
                                        candidate.max ??
                                          Number.MAX_SAFE_INTEGER,
                                        Number.MAX_SAFE_INTEGER,
                                      ),
                                    }
                                  : {
                                      mode:
                                        semantic === "positive"
                                          ? "append"
                                          : "replace",
                                    }),
                              };
                            } else delete next[semantic];
                            return next;
                          });
                        }}
                      >
                        <option value="">未绑定</option>
                        {candidates.map((candidate) => (
                          <option
                            key={target(candidate)}
                            value={target(candidate)}
                          >
                            {candidate.node} · {candidate.class_type} /{" "}
                            {candidate.input}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selected &&
                    (selected.min != null || selected.max != null) ? (
                      <>
                        <label>
                          下界
                          <input
                            type="number"
                            step="1"
                            min="0"
                            max={Number.MAX_SAFE_INTEGER}
                            value={selected.min ?? 0}
                            onChange={(event) =>
                              setBindings((current) => ({
                                ...current,
                                [semantic]: {
                                  ...selected,
                                  min: Number(event.target.value),
                                },
                              }))
                            }
                          />
                        </label>
                        <label>
                          上界
                          <input
                            type="number"
                            step="1"
                            min={selected.min ?? 0}
                            max={Number.MAX_SAFE_INTEGER}
                            value={selected.max ?? Number.MAX_SAFE_INTEGER}
                            onChange={(event) =>
                              setBindings((current) => ({
                                ...current,
                                [semantic]: {
                                  ...selected,
                                  max: Number(event.target.value),
                                },
                              }))
                            }
                          />
                        </label>
                      </>
                    ) : selected && !semantic.startsWith("reference") ? (
                      <label>
                        写入方式
                        <select
                          value={
                            selected.mode ??
                            (semantic === "positive" ? "append" : "replace")
                          }
                          onChange={(event) =>
                            setBindings((current) => ({
                              ...current,
                              [semantic]: {
                                ...selected,
                                mode: event.target.value as
                                  "append" | "replace",
                              },
                            }))
                          }
                        >
                          <option value="append">保留原文并追加</option>
                          <option value="replace">替换此绑定字段</option>
                        </select>
                      </label>
                    ) : null}
                  </div>
                );
              },
            )}
          </div>
          <fieldset>
            <legend>明确产物输出节点</legend>
            {inspection.data.outputs.map((output) => (
              <label key={output.node}>
                <input
                  type="checkbox"
                  checked={outputs.includes(output.node)}
                  onChange={(event) =>
                    setOutputs((current) =>
                      event.target.checked
                        ? [...current, output.node]
                        : current.filter((id) => id !== output.node),
                    )
                  }
                />
                {output.title} · {output.node}
              </label>
            ))}
          </fieldset>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      <button
        className="button"
        disabled={command.busy || !graph || !inspection.data || !outputs.length}
      >
        保存工作流与语义绑定
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
