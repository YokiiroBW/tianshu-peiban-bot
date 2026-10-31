import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useDomainCommand,
  type DomainTarget,
} from "../../api/useDomainResource";
import { uploadMedia } from "../../api/uploads";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { Project, DirectoryPlan } from "./types";
import { confirmed } from "./types";

export function ProjectImport({
  project,
  scope,
  refreshed,
}: {
  project: Project;
  scope: DomainTarget;
  refreshed: () => Promise<void>;
}) {
  const { session } = useAuth();
  const command = useDomainCommand<Record<string, unknown>>(
    `project-import:${project.project_id}`,
    "actor",
  );
  const preview = useDomainCommand<DirectoryPlan>(
    `project-preview:${project.project_id}`,
    "actor",
  );
  const [directory, setDirectory] = useState(project.directories[0] ?? "");
  const [checked, setChecked] = useState<string[]>([]);
  const [removed, setRemoved] = useState<string[]>([]);
  const [kind, setKind] = useState("file");
  const [locator, setLocator] = useState("");
  const [version, setVersion] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState<Awaited<
    ReturnType<typeof uploadMedia>
  > | null>(null);
  const [usedPlan, setUsedPlan] = useState("");
  const activeUpload = useRef<AbortController | null>(null);
  useEffect(() => () => activeUpload.current?.abort(), []);
  const plan =
    preview.result?.receipt?.execution === "succeeded" &&
    preview.result.data.plan_id !== usedPlan
      ? preview.result.data
      : null;
  useEffect(() => {
    setChecked([]);
    setRemoved([]);
  }, [plan?.plan_id]);
  return (
    <div className="knowledge-import">
      <details>
        <summary>预览并选择增量导入</summary>
        <div className="knowledge-actions">
          <select
            value={directory}
            onChange={(event) => setDirectory(event.target.value)}
          >
            <option value="">选择已登记目录</option>
            {project.directories.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
          <button
            className="button"
            disabled={preview.busy || !directory}
            onClick={() =>
              void preview.run("projects/directories/preview", {
                ...scope,
                project_id: project.project_id,
                directory,
              })
            }
          >
            只预览目录
          </button>
        </div>
        <CommandFeedback {...preview} />
        {plan && (
          <>
            <p className="muted">
              目录 {plan.directory} · 本批 {plan.items.length} 项 ·{" "}
              {plan.coverage.complete
                ? "已完成本次范围"
                : "仍有范围缺口：" + plan.coverage.missing.join("、")}{" "}
              · 预览没有导入资料。
            </p>
            <ul className="knowledge-import-list">
              {plan.items.map((row) => (
                <li key={row.locator}>
                  <label>
                    <input
                      type="checkbox"
                      disabled={row.state === "unavailable"}
                      checked={(row.state === "removed"
                        ? removed
                        : checked
                      ).includes(row.locator)}
                      onChange={(event) => {
                        const change =
                          row.state === "removed" ? setRemoved : setChecked;
                        change((values) =>
                          event.target.checked
                            ? [...values, row.locator]
                            : values.filter((value) => value !== row.locator),
                        );
                      }}
                    />
                    {row.locator} · {row.state}
                    {row.reason && ` · ${row.reason}`} · v{row.expected_version}
                  </label>
                </li>
              ))}
            </ul>
            <button
              className="button primary"
              disabled={command.busy || (!checked.length && !removed.length)}
              onClick={async () => {
                if (
                  confirmed(
                    await command.run("projects/directories/import", {
                      ...scope,
                      project_id: project.project_id,
                      plan_id: plan.plan_id,
                      plan_hash: plan.sha256,
                      files: checked,
                      tombstones: removed,
                    }),
                  )
                ) {
                  setChecked([]);
                  setRemoved([]);
                  setUsedPlan(plan.plan_id);
                  await refreshed();
                }
              }}
            >
              导入所选 {checked.length} 项 / 移除缺失索引 {removed.length} 项
            </button>
            {plan.next_position && (
              <button
                className="button"
                disabled={preview.busy}
                onClick={() =>
                  void preview.run("projects/directories/preview", {
                    ...scope,
                    project_id: project.project_id,
                    directory: plan.directory,
                    position: plan.next_position,
                  })
                }
              >
                预览下一批
              </button>
            )}
          </>
        )}
      </details>
      <details>
        <summary>显式导入单个文件、URL或上传原件</summary>
        <form
          className="knowledge-form"
          onSubmit={async (event) => {
            event.preventDefault();
            setError("");
            try {
              let source = uploaded;
              if (kind === "source" && !source) {
                if (!file || !session) throw new Error("请选择原件文件");
                if (file.size > 33554432)
                  throw new Error("单个项目原件最大32MiB");
                setUploading(true);
                const controller = new AbortController();
                activeUpload.current = controller;
                source = await uploadMedia(
                  file,
                  scope,
                  session.csrf,
                  controller.signal,
                );
                if (controller.signal.aborted) return;
                setUploaded(source);
                activeUpload.current = null;
              }
              const answer = await command.run("projects/documents/import", {
                ...scope,
                project_id: project.project_id,
                kind,
                locator:
                  kind === "source" ? source!.source_ref.source_id : locator,
                expected_version: version,
                ...(source && kind === "source"
                  ? {
                      source_ref: source.source_ref,
                      source_refs: [source.source_ref],
                      filename: file?.name,
                    }
                  : {}),
              });
              if (confirmed(answer)) {
                setUploaded(null);
                setFile(null);
                await refreshed();
              }
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : "导入未完成");
            } finally {
              activeUpload.current = null;
              setUploading(false);
            }
          }}
        >
          <fieldset disabled={uploading || command.busy}>
            <label>
              来源类型
              <select
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value);
                  setLocator("");
                  setUploaded(null);
                  setFile(null);
                }}
              >
                <option value="file">已登记项目根内的相对文件</option>
                <option value="url">已登记URL</option>
                <option value="source">上传原件</option>
              </select>
            </label>
            {kind === "url" ? (
              <label>
                登记URL
                <select
                  required
                  value={locator}
                  onChange={(event) => setLocator(event.target.value)}
                >
                  <option value="">请选择</option>
                  {project.allowed_urls.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
            ) : kind === "file" ? (
              <label>
                项目根内相对路径
                <input
                  required
                  value={locator}
                  onChange={(event) => setLocator(event.target.value)}
                  placeholder="docs/README.md"
                />
              </label>
            ) : (
              <label>
                原件文件
                <input
                  type="file"
                  required
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null);
                    setUploaded(null);
                  }}
                />
              </label>
            )}
            <label>
              已知当前版本（新文件为0，更新必须使用目录中的实际版本）
              <input
                type="number"
                min={0}
                value={version}
                onChange={(event) => setVersion(Number(event.target.value))}
              />
            </label>
            <button className="button" disabled={command.busy || uploading}>
              {uploading ? "正在上传原件…" : "导入这一份原件"}
            </button>
          </fieldset>
          {uploading && (
            <button
              type="button"
              className="button"
              onClick={() => activeUpload.current?.abort()}
            >
              取消此次上传
            </button>
          )}
          {error && <p role="alert">{error}</p>}
        </form>
      </details>
      <CommandFeedback {...command} />
    </div>
  );
}
