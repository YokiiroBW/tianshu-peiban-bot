import { DetailCard } from "../../components/DetailCard";
import { errorWords, reasonWords, stateWords } from "./wording";

/** Keep actionable wording visible; raw upstream codes remain available for diagnosis. */
export function ReasonNote({ code }: { code: string }) {
  if (!code) return null;
  const description = errorWords[code] ?? reasonWords[code] ?? stateWords[code];
  return (
    <DetailCard
      title={<>{description ?? "查看此阶段的诊断信息"}</>}
      className="media-reason"
    >
      <code>原因码：{code}</code>
    </DetailCard>
  );
}
