import { DetailCard } from "../../../components/DetailCard";
import type { Book } from "./types";
import { bookCode } from "./presentation";

export function BookSourceStatus({ book }: { book: Book }) {
  const selection = book.source_selection;
  if (!selection) return null;
  return (
    <DetailCard
      title={
        <>
          自动优选：{selection.preferred_source_name}
          {selection.repaired_chapters > 0
            ? ` · 已从其他来源补齐 ${selection.repaired_chapters} 章`
            : " · 查看来源对应情况"}
        </>
      }
      className="book-source-status"
    >
      <p className="book-fineprint muted">{bookCode(selection.code)}</p>
      {selection.last_repair && (
        <p className="book-fineprint">
          {selection.last_repair.chapter_title}：
          {selection.last_repair.from_source}
          读取失败，已由{selection.last_repair.source_name}补齐。
        </p>
      )}
      <ul>
        {selection.candidates.map((candidate) => (
          <li key={candidate.book_id}>
            <strong>{candidate.source_name}</strong>
            {candidate.latest_number
              ? ` · 第 ${candidate.latest_number} 章`
              : ""}
            {" · "}
            {candidate.readable ? "正文已验证" : bookCode(candidate.code)}
          </li>
        ))}
      </ul>
      <p className="book-fineprint muted">
        已存正文直接复用。只有同书和章节对应通过核对的来源才会参与补章。
      </p>
    </DetailCard>
  );
}
