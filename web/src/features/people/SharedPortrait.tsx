import MemoryRecords from "../memory/MemoryRecords";
import type { DomainTarget } from "../../api/useDomainResource";
import { MemoryTimeFilter, useMemoryTime } from "../memory/MemoryTimeFilter";
export function SharedPortrait({
  scope,
  personId,
  displayName,
}: {
  scope: DomainTarget;
  personId: string;
  displayName?: string;
}) {
  const [query, setQuery] = useMemoryTime();
  return (
    <section>
      <MemoryTimeFilter compact value={query} onChange={setQuery} />
      <MemoryRecords
        key={JSON.stringify(query)}
        scope={scope}
        subjectId={personId}
        query={query}
        identities={displayName ? { [personId]: displayName } : {}}
      />
    </section>
  );
}
