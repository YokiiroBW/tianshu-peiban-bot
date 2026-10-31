import { BookOpen } from "lucide-react";
import "./bookshelf.css";
export type ShelfEntry = {
  id: string;
  title: string;
  subtitle?: string;
  badge?: string;
  category?: string;
  selected?: boolean;
};
export function BookShelf({
  entries,
  onOpen,
  empty = "书架还是空的",
}: {
  entries: ShelfEntry[];
  onOpen: (id: string) => void;
  empty?: string;
}) {
  const groups = new Map<string, ShelfEntry[]>();
  for (const entry of entries) {
    const category = entry.category ?? "收藏";
    groups.set(category, [...(groups.get(category) ?? []), entry]);
  }
  if (!entries.length)
    return (
      <div className="shelf-empty">
        <BookOpen aria-hidden="true" />
        <p>{empty}</p>
      </div>
    );
  return (
    <div className="bookshelves">
      {[...groups].map(([category, items]) => (
        <section className="bookshelf" key={category}>
          <header>
            <h3>{category}</h3>
            <span>{items.length}</span>
          </header>
          <div className="bookshelf-books">
            {items.map((entry, index) => (
              <button
                type="button"
                className="shelf-book"
                data-color={index % 5}
                data-selected={entry.selected || undefined}
                key={entry.id}
                title={entry.title}
                onClick={() => onOpen(entry.id)}
                aria-label={`打开 ${entry.title}`}
              >
                <span className="book-mark">
                  <BookOpen aria-hidden="true" />
                </span>
                <strong>{entry.title}</strong>
                <span className="book-bottom">
                  {entry.badge ?? entry.subtitle ?? "点击阅读"}
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
