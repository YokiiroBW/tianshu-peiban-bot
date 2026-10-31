import { useEffect, useState } from "react";
import type { Kind, Media, Mutate } from "./types";
import { kinds } from "./types";

type Folder = {
  relative_path: string;
  entries: { name: string; relative_path: string; kind: string }[];
  image_count: number;
  next_offset: number | null;
};
type Inspection = {
  relative_path: string;
  title: string;
  input_digest: string;
  chapters: { title: string; input_kind: string; page_count: number | null }[];
};

export function ComicImports({
  media,
  mutate,
}: {
  media: Media;
  mutate: Mutate;
}) {
  const [folder, setFolder] = useState<Folder>();
  const [inspection, setInspection] = useState<Inspection>();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<Kind>("general");
  const { request } = media;
  const browse = async (path: string, offset = 0) => {
    const r = await request<Folder>(
      "comic-import-browse",
      "comics/imports/browse",
      { relative_path: path, offset },
    );
    if (r) {
      setFolder((old) =>
        offset && old ? { ...r, entries: [...old.entries, ...r.entries] } : r,
      );
      setInspection(undefined);
    }
  };
  useEffect(() => {
    void request<Folder>("comic-import-browse", "comics/imports/browse", {
      relative_path: "",
    }).then((r) => {
      if (r) setFolder(r);
    });
  }, [request]);
  const inspect = async (path: string) => {
    const r = await request<Inspection>(
      "comic-import-inspect",
      "comics/imports/inspect",
      { relative_path: path },
    );
    if (r) {
      setInspection(r);
      setTitle(r.title);
      setKind(path.startsWith("本子/") ? "private" : "general");
    }
  };
  return (
    <div className="comic-stack">
      <div className="media-card">
        <h3>将 NAS 图片目录整理为漫画</h3>
        <p>
          选择一部作品的文件夹，支持“一组有序图片”或“每章一个图片文件夹 /
          CBZ”。文件按自然顺序排列，原文件保留，生成新 CBZ 后归档。
        </p>
        <p className="muted">
          不同作品请分别导入；图片与章节子目录混放时，会提示先选择准确的章节目录。
        </p>
      </div>
      <div className="comic-heading">
        <strong>
          {folder?.relative_path === "."
            ? "漫画输入目录"
            : folder?.relative_path || "正在读取目录"}
        </strong>
        <div className="comic-actions">
          <button className="button" onClick={() => void browse("")}>
            根目录
          </button>
          {folder && folder.relative_path !== "." && (
            <>
              <button
                className="button"
                onClick={() =>
                  void browse(
                    folder.relative_path.split("/").slice(0, -1).join("/"),
                  )
                }
              >
                上级目录
              </button>
              <button
                className="button primary"
                onClick={() => void inspect(folder.relative_path)}
              >
                将当前目录作为一部作品
              </button>
            </>
          )}
        </div>
      </div>
      {folder && (
        <p className="muted">当前目录有 {folder.image_count} 张图片</p>
      )}
      <div className="comic-stack">
        {folder?.entries.map((entry) => (
          <div className="media-card comic-heading" key={entry.relative_path}>
            <button
              className="comic-text-button"
              onClick={() =>
                void (entry.kind === "directory"
                  ? browse(entry.relative_path)
                  : inspect(entry.relative_path))
              }
            >
              {entry.name}
              {entry.kind === "directory" ? " /" : ""}
            </button>
            <button
              className="button"
              onClick={() => void inspect(entry.relative_path)}
            >
              检查导入
            </button>
          </div>
        ))}
      </div>
      {folder?.next_offset != null && (
        <button
          className="button"
          onClick={() => void browse(folder.relative_path, folder.next_offset!)}
        >
          加载更多目录
        </button>
      )}
      {inspection && (
        <form
          className="media-card comic-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void mutate(
              "imports/enqueue",
              {
                relative_path: inspection.relative_path,
                input_digest: inspection.input_digest,
                title,
                library_kind: kind,
              },
              "导入任务已加入队列，原图片和漫画包保持原样。",
            ).then((ok) => {
              if (ok) setInspection(undefined);
            });
          }}
        >
          <h3>导入预览 · {inspection.chapters.length} 章</h3>
          <div className="comic-field-grid">
            <label>
              作品名
              <input
                required
                value={title}
                maxLength={500}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              归档分类
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as Kind)}
              >
                {kinds.map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <ol className="comic-import-order">
            {inspection.chapters.map((c, index) => (
              <li key={index}>
                {c.title} ·{" "}
                {c.page_count
                  ? `${c.page_count} 张图片`
                  : "漫画包，入队后逐页校验"}
              </li>
            ))}
          </ol>
          <button className="button primary" disabled={!!media.busy}>
            无损转换并归档
          </button>
        </form>
      )}
    </div>
  );
}
