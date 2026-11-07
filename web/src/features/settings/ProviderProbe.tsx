import { useDomainCommand } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { Drawer } from "../../components/Drawer";
import { humanizeCode } from "../../design/humanize";
import type { ProbeResult } from "./providerApi";
import { capabilityNames } from "./providerPresentation";
import type { ModelCatalog, ModelRoute, Provider } from "./providerTypes";

const reasons: Record<string, string> = {
  tool_call_mismatch: "模型没有按测试要求调用工具。",
  probe_output_mismatch: "回答未通过样本核对，尚未登记此能力。",
  probe_output_incomplete: "模型输出被截断或未完整结束，未登记此能力。",
  model_not_succeeded: "模型请求尚未成功完成，未登记此能力。",
  stream_output_not_observed: "尚未取得实际流式文字，未登记流式能力。",
};
const upstreamNames: Record<string, string> = {
  rate_or_quota: "供应商限速或额度不足",
  rate_limit: "供应商请求频率受限",
  quota_or_balance: "供应商额度或余额不足",
  authentication: "供应商密钥验证失败",
  payment: "供应商付款或余额问题",
  forbidden: "供应商拒绝访问",
  invalid_request: "供应商拒绝了请求参数",
  context_length: "超过供应商上下文限制",
  model_or_endpoint: "模型名称或接口地址不可用",
  request_size: "请求体过大",
};

export function ProviderProbe({
  route,
  provider,
  catalog,
  reload,
  close,
}: {
  route: ModelRoute;
  provider: Provider;
  catalog: ModelCatalog;
  reload: () => Promise<void>;
  close: () => void;
}) {
  const probe = useDomainCommand<ProbeResult>(
    `model-probe:${route.route_id}`,
    "global",
  );
  const available = catalog.probe_capabilities?.[provider.protocol] ?? [
    "text",
    "stream",
  ];
  const result = probe.result?.data;
  const upstream = result?.receipt?.diagnostic?.upstream;
  return (
    <Drawer title="测试与登记模型能力" open variant="card" onClose={close}>
      <p>
        {provider.name} / {route.model}
      </p>
      <p>
        逐项实际测试，通过后自动登记。图片、音频和视频使用内置小样本；工具测试包含调用和结果回传。
      </p>
      <div className="models-actions">
        {available.map((capability) => (
          <button
            className="button"
            key={capability}
            disabled={probe.busy}
            onClick={async () => {
              const answer = await probe.run(
                `models/routes/${encodeURIComponent(route.route_id)}/probe`,
                { capability },
                route.revision,
              );
              if (answer) await reload();
            }}
          >
            测试{capabilityNames[capability] ?? capability}
            {route.verified_capabilities.includes(capability)
              ? "（已登记）"
              : ""}
          </button>
        ))}
      </div>
      <CommandFeedback {...probe} />
      {probe.busy && <p role="status">正在完成实际测试，请稍候。</p>}
      {result && (
        <div role="status">
          <strong>
            {capabilityNames[result.capability ?? ""] ?? "模型能力"}：
            {result.verification === "provider_test"
              ? "测试通过，已登记"
              : "尚未通过测试"}
          </strong>
          {result.reason && (
            <p>
              {reasons[result.reason] ??
                humanizeCode(result.reason, "请查看测试请求结果")}
            </p>
          )}
          {upstream && (
            <p>
              {upstreamNames[upstream.category] ?? "供应商请求失败"}
              {upstream.param && `，参数：${upstream.param}`}。
              {upstream.code && `供应商错误码：${upstream.code}`}
            </p>
          )}
          {result.receipt?.error_code && (
            <p>
              {humanizeCode(
                result.receipt.error_code,
                "请求未完成，请查看原任务",
              )}
            </p>
          )}
          {result.receipts && (
            <details>
              <summary>测试请求记录（{result.receipts.length} 次）</summary>
              <ul>
                {result.receipts.map((receipt) => (
                  <li key={receipt.attempt_id}>
                    {humanizeCode(receipt.status, "状态尚未确认")} ·{" "}
                    {receipt.attempt_id}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Drawer>
  );
}
