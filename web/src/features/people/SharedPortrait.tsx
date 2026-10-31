import MemoryRecords from "../memory/MemoryRecords";
import type { DomainTarget } from "../../api/useDomainResource";
export function SharedPortrait({
  scope,
  personId,
}: {
  scope: DomainTarget;
  personId: string;
}) {
  return (
    <section>
      <h3>人物认识与原文依据</h3>
      <p className="muted">
        这里显示当前明确授权范围内的记忆。人物本人自述、他人转述与角色推测分别保留。
      </p>
      <MemoryRecords scope={scope} subjectId={personId} />
    </section>
  );
}
