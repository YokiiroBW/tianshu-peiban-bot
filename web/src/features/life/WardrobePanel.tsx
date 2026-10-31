import { useEffect, useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useDomainCommand,
  useDomainResource,
  useDomainTarget,
} from "../../api/useDomainResource";
import { uploadMedia } from "../../api/uploads";
import type { MediaRef, Page } from "../../api/contracts";
import { MediaPreview } from "../../components/MediaPreview";
import { CommandFeedback } from "../../components/CommandFeedback";
import { SourceEvidence } from "../../components/SourceEvidence";
import { DetailCard } from "../../components/DetailCard";
import type { Outfit, LifeState } from "./domainTypes";

export function WardrobePanel({
  canManage,
  state,
  refreshState,
}: {
  canManage: boolean;
  state: LifeState | null;
  refreshState: () => Promise<void>;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const view = useDomainResource<Page<Outfit>>("life/outfits", {
    cursor,
    limit: 40,
  });
  const [editing, setEditing] = useState<Outfit | "new" | null>(null);
  const action = useDomainCommand("outfit-selection");
  useEffect(() => {
    if (view.accessLost) setEditing(null);
  }, [view.accessLost]);
  return (
    <section className="life-card">
      <div className="section-heading">
        <div>
          <h3>衣柜</h3>
          <p className="muted">
            外观与参考原图会进入同一场景快照，供小屋和图片创作使用。
          </p>
        </div>
        {canManage && (
          <button className="button" onClick={() => setEditing("new")}>
            添加服装
          </button>
        )}
      </div>
      {view.error && <p role="alert">{view.error}</p>}
      <div className="domain-grid">
        {view.data?.items.map((item) => (
          <article className="domain-record" key={item.id}>
            {item.reference && (
              <MediaPreview media={item.reference} alt={item.description} />
            )}
            <header>
              <strong>{item.description}</strong>
              {state?.outfit?.id === item.id && (
                <span className="badge">当前穿着</span>
              )}
            </header>
            <p>{item.appearance}</p>
            <small>{item.activity_kinds.join(" · ") || "未限定活动"}</small>
            <SourceEvidence sources={item.source_refs} />
            {canManage && (
              <div className="domain-actions">
                <button
                  className="button"
                  disabled={action.busy || !state}
                  onClick={() => {
                    if (state)
                      void action
                        .run(
                          "life/outfits.select",
                          { id: item.id },
                          state.revision,
                        )
                        .then((result) => {
                          if (result) void refreshState();
                        });
                  }}
                >
                  穿上这件
                </button>
                <button
                  className="text-link"
                  onClick={() => setEditing(structuredClone(item))}
                >
                  编辑服装
                </button>
                <button
                  className="text-link"
                  disabled={action.busy}
                  onClick={() =>
                    void action
                      .run(
                        "life/outfits.delete",
                        { id: item.id },
                        item.revision,
                      )
                      .then((result) => {
                        if (result) {
                          void view.refresh();
                          void refreshState();
                        }
                      })
                  }
                >
                  从衣柜移除
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
      {view.data && !view.data.items.length && (
        <p className="muted">衣柜里还没有保存的服装。</p>
      )}
      <CommandFeedback {...action} />
      {editing && (
        <OutfitEditor
          key={editing === "new" ? "new" : `${editing.id}:${editing.revision}`}
          outfit={editing === "new" ? null : editing}
          close={() => setEditing(null)}
          saved={() => {
            setEditing(null);
            void view.refresh();
            void refreshState();
          }}
        />
      )}
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批
          </button>
        )}
        {view.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(view.data!.next_cursor)}
          >
            继续看衣柜
          </button>
        )}
      </div>
    </section>
  );
}

function OutfitEditor({
  outfit,
  close,
  saved,
}: {
  outfit: Outfit | null;
  close: () => void;
  saved: () => void;
}) {
  const { session } = useAuth();
  const target = useDomainTarget();
  const action = useDomainCommand(`outfit:${outfit?.id ?? "new"}`);
  const [reference, setReference] = useState<MediaRef | null>(
    outfit?.reference ?? null,
  );
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  return (
    <DetailCard title={outfit ? "修改服装" : "添加服装"}>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void action
            .run(
              "life/outfits.save",
              {
                ...(outfit ? { id: outfit.id } : {}),
                description: data.get("description"),
                appearance: data.get("appearance"),
                reference,
                activity_kinds: String(data.get("activity_kinds") ?? "")
                  .split(/[,，]/)
                  .map((value) => value.trim())
                  .filter(Boolean),
              },
              outfit?.revision ?? null,
            )
            .then((result) => {
              if (result) saved();
            });
        }}
      >
        <label>
          名称与描述
          <input
            name="description"
            defaultValue={outfit?.description ?? ""}
            required
            maxLength={1000}
          />
        </label>
        <label>
          完整外观
          <textarea
            name="appearance"
            defaultValue={outfit?.appearance ?? ""}
            required
            maxLength={8000}
            placeholder="颜色、剪裁、配饰以及需要保持的细节"
          />
        </label>
        <label>
          适用活动
          <input
            name="activity_kinds"
            defaultValue={outfit?.activity_kinds.join("，") ?? ""}
            placeholder="用逗号分隔，可留空"
          />
        </label>
        <label>
          参考图片
          <input
            type="file"
            accept="image/*"
            disabled={uploading}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file || !session?.authenticated) return;
              setUploading(true);
              setError("");
              void uploadMedia(
                file,
                { ...target, actor_id: target.actor_id ?? undefined },
                session.csrf,
                new AbortController().signal,
              )
                .then((uploaded) => setReference(uploaded.artifact.media))
                .catch((cause) =>
                  setError(
                    cause instanceof Error ? cause.message : "上传结果待确认",
                  ),
                )
                .finally(() => setUploading(false));
            }}
          />
        </label>
        {reference && (
          <>
            <MediaPreview media={reference} />
            <button
              className="text-link"
              type="button"
              onClick={() => setReference(null)}
            >
              移除此参考图
            </button>
          </>
        )}
        {error && <p role="alert">{error}</p>}
        <div className="domain-actions">
          <button
            className="button primary"
            disabled={uploading || action.busy}
          >
            {uploading ? "正在上传原图…" : "保存服装"}
          </button>
          <button className="button" type="button" onClick={close}>
            收起
          </button>
        </div>
        <CommandFeedback {...action} />
      </form>
    </DetailCard>
  );
}
