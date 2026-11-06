import { DetailCard } from "../../components/DetailCard";
import { errorWords, reasonWords, stateWords } from "./wording";

/** Show an explanation instead of the upstream implementation code. */
export function ReasonNote({ code }: { code: string }) {
  if (!code) return null;
  const description = errorWords[code] ?? reasonWords[code] ?? stateWords[code];
  return (
    <DetailCard
      title={<>{description ?? "查看此阶段的诊断信息"}</>}
      className="media-reason"
    >
      <p>{description ?? "服务尚未提供可读的原因说明，请查看原任务的执行结果。"}</p>
    </DetailCard>
  );
}
