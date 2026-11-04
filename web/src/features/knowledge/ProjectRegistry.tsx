import { useState } from "react";
import { useDomainCommand } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { Project, Worktree } from "./types";
import { lines, confirmed } from "./types";

export function ProjectRegistry({
  project,
  roots,
  onSaved,
}: {
  project: Project | null;
  roots: string[];
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState({
    project_id: project?.project_id ?? "",
    label: project?.label ?? "",
    root: project?.root ?? roots[0] ?? "",
    enabled: project?.enabled ?? true,
  });
  const [directories, setDirectories] = useState(
    project?.directories.join("\n") ?? "",
  );
  const [urls, setUrls] = useState(project?.allowed_urls.join("\n") ?? "");
  const [worktrees, setWorktrees] = useState<Worktree[]>(
    project?.worktrees ?? [],
  );
  const command = useDomainCommand<Project>(
    `project-registry:${project?.project_id ?? "new"}`,
    "global",
  );
  function change(index: number, patch: Partial<Worktree>) {
    setWorktrees((values) =>
      values.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }
  return (
    <form
      className="knowledge-form"
      onSubmit={async (event) => {
        event.preventDefault();
        if (
          confirmed(
            await command.run(
              "projects/registry/save",
              {
                ...draft,
                directories: lines(directories),
                allowed_urls: lines(urls),
                worktrees,
              },
              project?.revision ?? 0,
            ),
          )
        )
          await onSaved();
      }}
    >
      <p className="muted">
        登记服务器上实际受管目录。只注册范围，不扫描根目录，不修改外部项目文件。
      </p>
      <label>
        项目标识
        <input
          required
          readOnly={!!project}
          value={draft.project_id}
          onChange={(event) =>
            setDraft({ ...draft, project_id: event.target.value })
          }
        />
      </label>
      <label>
        项目名称
        <input
          required
          value={draft.label}
          onChange={(event) =>
            setDraft({ ...draft, label: event.target.value })
          }
        />
      </label>
      <label>
        实际项目根目录
        <input
          required
          list="managed-project-roots"
          value={draft.root}
          onChange={(event) => setDraft({ ...draft, root: event.target.value })}
        />
        <datalist id="managed-project-roots">
          {roots.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
      </label>
      <p className="muted">
        服务器允许根：
        {roots.join("、") || "尚未配置capabilities.projects.allowed_roots"} ·
        当前授权工作区
      </p>
      <label>
        允许显式扫描的相对目录（每行一个，根目录不自动扫描）
        <textarea
          rows={3}
          value={directories}
          onChange={(event) => setDirectories(event.target.value)}
        />
      </label>
      <label>
        允许导入的完整URL（每行一个）
        <textarea
          rows={3}
          value={urls}
          onChange={(event) => setUrls(event.target.value)}
        />
      </label>
      <fieldset>
        <legend>受管工作目录与续作文件</legend>
        {worktrees.map((row, index) => (
          <div className="knowledge-worktree" key={index}>
            <label>
              工作目录标识
              <input
                required
                value={row.id}
                onChange={(event) => change(index, { id: event.target.value })}
              />
            </label>
            <label>
              服务器实际checkout绝对路径
              <input
                required
                value={row.path}
                onChange={(event) =>
                  change(index, { path: event.target.value })
                }
              />
            </label>
            <label>
              登记分支
              <input
                required
                value={row.branch}
                onChange={(event) =>
                  change(index, { branch: event.target.value })
                }
              />
            </label>
            <label>
              允许读取摘要的文件（最多16个，每行一个）
              <textarea
                rows={3}
                value={row.files.join("\n")}
                onChange={(event) =>
                  change(index, { files: lines(event.target.value) })
                }
              />
            </label>
            <label>
              文件总读取字节上限
              <input
                type="number"
                min={1024}
                max={8388608}
                value={row.max_bytes ?? 1048576}
                onChange={(event) =>
                  change(index, { max_bytes: Number(event.target.value) })
                }
              />
            </label>
            <button
              type="button"
              className="button"
              onClick={() =>
                setWorktrees((values) => values.filter((_, i) => i !== index))
              }
            >
              移除此登记
            </button>
          </div>
        ))}
        <button
          type="button"
          className="button"
          disabled={worktrees.length >= 16}
          onClick={() =>
            setWorktrees((values) => [
              ...values,
              {
                id: "",
                path: "",
                branch: "main",
                files: [],
                max_bytes: 1048576,
              },
            ])
          }
        >
          添加工作目录
        </button>
      </fieldset>
      <label>
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(event) =>
            setDraft({ ...draft, enabled: event.target.checked })
          }
        />
        启用项目
      </label>
      <button
        className="button primary"
        disabled={command.busy || !roots.length}
      >
        保存登记
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
