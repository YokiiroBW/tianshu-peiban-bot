import { useState } from "react";
import { BookShelf } from "../../components/BookShelf";
import { CornerDownRight, File, FileSymlink, Folder } from "lucide-react";
import { kindLabel, modifiedText, sizeText, type Entry } from "./api";

function EntryIcon({ entry }: { entry: Entry }) {
  if (entry.directory) return <Folder aria-hidden="true" />;
  if (entry.kind === "reparse_file") return <FileSymlink aria-hidden="true" />;
  return <File aria-hidden="true" />;
}

/**
 * Shelf and compact list presentations share the existing entry navigation.
 *
 * A directory row is a real control that opens the next level; a file row selects the entry whose
 * read-only detail is shown beside (or below) the list. Long names wrap instead of being clipped
 * away from assistive technology, and the row's accessible name carries the full path.
 */
export function EntryList({
  entries,
  selected,
  busy,
  onOpen,
  onSelect,
}: {
  entries: Entry[];
  selected: string;
  busy: boolean;
  onOpen: (entry: Entry) => void;
  onSelect: (entry: Entry) => void;
}) {
  const [layout, setLayout] = useState<"shelf" | "list">("shelf");
  return (
    <>
      <div className="role-settings-tabs" aria-label="资源显示方式">
        <button
          aria-selected={layout === "shelf"}
          onClick={() => setLayout("shelf")}
        >
          书架
        </button>
        <button
          aria-selected={layout === "list"}
          onClick={() => setLayout("list")}
        >
          列表
        </button>
      </div>
      {layout === "shelf" ? (
        <BookShelf
          entries={entries.map((e) => ({
            id: e.entry_id,
            title: e.name,
            badge: e.directory ? "文件夹" : kindLabel(e.kind, false),
            category: e.directory ? "文件夹" : "资料",
            selected: selected === e.entry_id,
          }))}
          onOpen={(id) => {
            const entry = entries.find((e) => e.entry_id === id);
            if (entry) (entry.directory ? onOpen : onSelect)(entry);
          }}
        />
      ) : (
        <ul className="asset-list" aria-busy={busy || undefined}>
          {entries.map((entry) => (
            <li key={`${entry.library_id}:${entry.entry_id}`}>
              <button
                type="button"
                className="asset-row"
                data-selected={selected === entry.entry_id || undefined}
                aria-current={selected === entry.entry_id ? "true" : undefined}
                onClick={() =>
                  entry.directory ? onOpen(entry) : onSelect(entry)
                }
              >
                <span className="asset-row-icon">
                  <EntryIcon entry={entry} />
                </span>
                <span className="asset-row-name">
                  <span className="asset-row-label">{entry.name}</span>
                  <span className="asset-row-path" title={entry.relative_path}>
                    {entry.relative_path}
                  </span>
                </span>
                <span className="asset-row-kind">
                  {kindLabel(entry.kind, entry.directory)}
                </span>
                <span className="asset-row-size">
                  {entry.directory ? "—" : sizeText(entry)}
                </span>
                <span className="asset-row-time">{modifiedText(entry)}</span>
                <span className="asset-row-hint" aria-hidden="true">
                  {entry.directory ? (
                    <>
                      进入
                      <CornerDownRight />
                    </>
                  ) : (
                    "查看详情"
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
