import MemoryReview from "../memory/MemoryReview";
import type { DomainTarget } from "../../api/useDomainResource";
export function Interactions({
  scope,
  personId,
}: {
  scope: DomainTarget;
  personId: string;
}) {
  return <MemoryReview scope={scope} personId={personId} />;
}
