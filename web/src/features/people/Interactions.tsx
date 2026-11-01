import MemoryReview from "../memory/MemoryReview";
import type { DomainTarget } from "../../api/useDomainResource";
import { MemoryTimeFilter, useMemoryTime } from "../memory/MemoryTimeFilter";
export function Interactions({
  scope,
  personId,
}: {
  scope: DomainTarget;
  personId: string;
}) {
  const [query, setQuery] = useMemoryTime();
  return (
    <>
      <MemoryTimeFilter value={query} onChange={setQuery} />
      <MemoryReview
        key={JSON.stringify(query)}
        scope={scope}
        personId={personId}
        query={query}
      />
    </>
  );
}
