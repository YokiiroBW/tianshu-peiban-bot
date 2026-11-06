import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  type DomainTarget,
} from "../../api/useDomainResource";
import { SourceEvidence } from "../../components/SourceEvidence";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { SourceRef } from "../../api/contracts";

type TextSource = {
  content: string | null;
  representation: string;
  speaker_id: string | null;
  can_revoke: boolean;
  next_position: number | null;
};
export function SourceForget({
  source,
  scope,
  onChanged,
}: {
  source: SourceRef;
  scope: DomainTarget;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const view = useDomainResource<TextSource>(
    "sources/read",
    {
      ...scope,
      source_ref: source,
      requested: { position, max_characters: 6000 },
    },
    open,
  );
  const command = useDomainCommand<unknown>(
    `source-forget:${source.source_id}:${source.revision}`,
    "actor",
  );
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>原件与遗忘范围</summary>
      <p className="muted">
        遗忘原件会同时撤回依赖它的记忆、素材和未执行结果；其它独立有效来源保留。
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data?.content && (
        <label>
          选择需要忘记的原文片段
          <textarea
            rows={7}
            readOnly
            value={view.data.content}
            onSelect={(event) => {
              const input = event.currentTarget;
              const start =
                position +
                [...input.value.slice(0, input.selectionStart)].length;
              const end =
                position + [...input.value.slice(0, input.selectionEnd)].length;
              setSelected(start < end ? { start, end } : null);
            }}
          />
        </label>
      )}
      {view.data?.next_position != null && (
        <button
          className="button"
          onClick={() => {
            setPosition(view.data!.next_position!);
            setSelected(null);
          }}
        >
          读取下一段原文
        </button>
      )}
      {view.data && (
        <p className="muted">
          原作者：{view.data.speaker_id ? "已关联来源作者" : "未登记"}
          {view.data.can_revoke ? "" : " · 当前身份仅可读取此原件"}
        </p>
      )}
      {view.data?.can_revoke && (
        <div className="models-actions">
          {selected && (
            <button
              className="button"
              disabled={command.busy}
              onClick={async () => {
                if (
                  await command.run(
                    "sources/forget-fragment",
                    { ...scope, source_id: source.source_id, ...selected },
                    source.revision,
                  )
                ) {
                  setSelected(null);
                  await onChanged();
                  await view.refresh();
                }
              }}
            >
              忘记选中文字（{selected.start}–{selected.end}）
            </button>
          )}
          <button
            className="button"
            disabled={command.busy}
            onClick={async () => {
              if (
                await command.run(
                  "sources/forget",
                  { ...scope, source_id: source.source_id },
                  source.revision,
                )
              )
                await onChanged();
            }}
          >
            忘记这份原件及其依赖
          </button>
        </div>
      )}
      {!view.data?.content && (
        <SourceEvidence sources={[source]} scope={scope} />
      )}
      <CommandFeedback {...command} />
    </details>
  );
}
