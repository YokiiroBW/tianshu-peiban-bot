import { useState } from "react";
import { Search, Check } from "lucide-react";
import type {
  Book,
  BookMetadata,
  MetadataResult,
  MetadataCandidate,
} from "./types";

const fields: [keyof BookMetadata, string][] = [
  ["title", "书名"],
  ["author", "作者"],
  ["category", "书籍类别"],
  ["language", "语言"],
  ["isbn", "ISBN"],
  ["publisher", "出版社"],
  ["published_at", "出版日期"],
  ["series", "系列"],
  ["series_index", "系列序号"],
  ["tags", "标签"],
  ["cover_url", "封面地址"],
  ["description", "简介"],
];

export function BookMetadataEditor({
  book,
  busy,
  onSearch,
  onApply,
}: {
  book: Book;
  busy: boolean;
  onSearch: (query: string) => Promise<MetadataResult | undefined>;
  onApply: (fields: Partial<BookMetadata>, source: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<BookMetadata>({ ...book.metadata });
  const [selected, setSelected] = useState<Set<keyof BookMetadata>>(new Set());
  const [query, setQuery] = useState(book.metadata.isbn || book.metadata.title);
  const [result, setResult] = useState<MetadataResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [source, setSource] = useState("manual");
  function update(key: keyof BookMetadata, value: string) {
    setDraft((current) => ({
      ...current,
      [key]:
        key === "tags"
          ? value
              .split(/[,，]/)
              .map((tag) => tag.trim())
              .filter(Boolean)
          : value,
    }));
    setSelected((current) => new Set([...current, key]));
  }
  function choose(candidate: MetadataCandidate) {
    const patch = Object.fromEntries(
      Object.entries(candidate.metadata).filter(
        ([, value]) => value && (!Array.isArray(value) || value.length),
      ),
    );
    setDraft((current) => ({ ...current, ...patch }));
    setSelected(new Set(Object.keys(patch) as (keyof BookMetadata)[]));
    setSource(candidate.provider);
  }
  return (
    <div className="book-metadata-editor">
      <form
        className="book-metadata-search"
        onSubmit={(event) => {
          event.preventDefault();
          setSearching(true);
          void onSearch(query)
            .then((value) => {
              if (value) setResult(value);
            })
            .finally(() => setSearching(false));
        }}
      >
        <label className="sr-only" htmlFor="book-metadata-query">
          搜索书库元数据
        </label>
        <input
          id="book-metadata-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="书名、作者或 ISBN"
          maxLength={300}
        />
        <button className="button" disabled={searching || !query.trim()}>
          <Search aria-hidden="true" />
          {searching ? "查询中…" : "搜索书库"}
        </button>
      </form>
      {result && (
        <div className="book-metadata-candidates">
          {result.candidates.map((candidate) => (
            <button
              className="book-metadata-candidate"
              key={candidate.candidate_id}
              onClick={() => choose(candidate)}
            >
              <strong>{candidate.metadata.title}</strong>
              <span>
                {candidate.metadata.author || "作者未提供"} ·{" "}
                {candidate.provider}
              </span>
              {candidate.metadata.isbn && (
                <small>ISBN {candidate.metadata.isbn}</small>
              )}
            </button>
          ))}
          {!result.candidates.length && (
            <p className="muted">
              未找到可靠候选，可以继续手动编辑；下载不受影响。
            </p>
          )}
          {result.providers.some((provider) => provider.code !== "ready") && (
            <p className="media-warning">
              部分书库暂不可用，已保留可取得的候选。
            </p>
          )}
        </div>
      )}
      <form
        className="media-stack"
        onSubmit={(event) => {
          event.preventDefault();
          const patch = Object.fromEntries(
            [...selected].map((key) => [key, draft[key]]),
          );
          void onApply(patch, source).then((saved) => {
            if (saved) setSelected(new Set());
          });
        }}
      >
        <p className="muted">
          只保存勾选的字段。手动输入或选择的资料会保持优先，不被后续自动匹配覆盖。
        </p>
        <div className="book-metadata-fields">
          {fields.map(([key, label]) => {
            const value = draft[key];
            return (
              <div
                key={key}
                className={key === "description" ? "book-metadata-wide" : ""}
              >
                <div className="book-metadata-label">
                  <label htmlFor={`book-field-${key}`}>{label}</label>
                  <label className="book-checkbox">
                    <input
                      type="checkbox"
                      aria-label={`保存${label}`}
                      checked={selected.has(key)}
                      onChange={(event) =>
                        setSelected((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(key);
                          else next.delete(key);
                          return next;
                        })
                      }
                    />
                    保存
                  </label>
                </div>
                {key === "description" ? (
                  <textarea
                    id={`book-field-${key}`}
                    rows={5}
                    value={String(value ?? "")}
                    onChange={(event) => update(key, event.target.value)}
                  />
                ) : (
                  <input
                    id={`book-field-${key}`}
                    value={
                      Array.isArray(value)
                        ? value.join(", ")
                        : String(value ?? "")
                    }
                    onChange={(event) => update(key, event.target.value)}
                  />
                )}
                {book.manual_fields.includes(key) && (
                  <small className="book-manual-note">已人工选择</small>
                )}
              </div>
            );
          })}
        </div>
        <div>
          <button
            className="button primary"
            disabled={busy || !selected.size}
            type="submit"
          >
            <Check aria-hidden="true" />
            保存选定资料
          </button>
        </div>
      </form>
    </div>
  );
}
