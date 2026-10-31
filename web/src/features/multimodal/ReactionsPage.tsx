import { useEffect, useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  useDomainTarget,
} from "../../api/useDomainResource";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { uploadMedia } from "../../api/uploads";
import { CommandFeedback } from "../../components/CommandFeedback";
import { ArtifactList, MediaPreview } from "../../components/MediaPreview";
import { DetailCard } from "../../components/DetailCard";
import type { ArtifactRef, SourceRef } from "../../api/contracts";
import { SourceEvidence } from "../../components/SourceEvidence";
import type { ReactionsView, ReactionAsset, ReactionPack } from "./types";
import "./multimodal.css";

export default function ReactionsPage() {
  const scope = useDomainTarget();
  return <ReactionLibrary key={JSON.stringify(scope)} />;
}
function ReactionLibrary() {
  const { session } = useAuth();
  const scope = useDomainTarget();
  const { actor } = useRoleWorkspace();
  const [pack, setPack] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [assets, setAssets] = useState<ReactionAsset[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const view = useDomainResource<ReactionsView>("reactions/view", {
    cursor,
    limit: 48,
  });
  const action = useDomainCommand<{ artifact?: ArtifactRef }>(
    "reaction-library",
  );
  const index = useDomainCommand("reaction-index");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (view.accessLost) {
      setAssets([]);
      setSelected([]);
    } else if (view.data)
      setAssets((current) =>
        cursor
          ? [
              ...current.filter(
                (x) => !view.data!.assets.some((y) => y.id === x.id),
              ),
              ...view.data!.assets,
            ]
          : view.data!.assets,
      );
  }, [view.data, view.accessLost]);
  function refresh() {
    setSelected([]);
    setCursor(null);
    return view.refresh();
  }
  const visible = assets.filter((x) => !pack || x.pack_id === pack);
  const chosen = assets.filter((x) => selected.includes(x.id));
  const currentPack = view.data?.packs.find((x) => x.id === pack);
  return (
    <div className="domain-workspace">
      <header className="domain-toolbar">
        <div>
          <p className="eyebrow">表情与图包</p>
          <h2>按交流用途找到合适的表情</h2>
        </div>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void refresh()}
        >
          读取图库
        </button>
      </header>
      <p>
        图包和原始媒体保留当前真实范围。普通照片与聊天截图不会自动进入共享图包。
      </p>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          <section className="panel">
            <div className="domain-grid">
              <label>
                素材包
                <select
                  value={pack}
                  onChange={(event) => {
                    setPack(event.target.value);
                    setSelected([]);
                  }}
                >
                  <option value="">全部可用图包</option>
                  {view.data.packs.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name} · {x.asset_count ?? "未知"}张 ·{" "}
                      {x.enabled ? "启用" : "停用"}
                    </option>
                  ))}
                </select>
              </label>
              <p>
                索引配置 {view.data.index.profile ?? "尚未配置"} · 等待{" "}
                {view.data.index.pending ?? "未知"} · 失败{" "}
                {view.data.index.failed ?? "未知"}
              </p>
            </div>
            <div className="domain-actions">
              <button
                className="button"
                disabled={index.busy}
                onClick={() =>
                  void index
                    .run(
                      "reactions/index",
                      {
                        ...(chosen.length
                          ? { asset_ids: chosen.map((x) => x.id) }
                          : {}),
                        limit: 20,
                      },
                      null,
                    )
                    .then(() => void refresh())
                }
              >
                索引{chosen.length ? "选中" : "下一批未完成"}素材
              </button>
              <button
                className="button"
                disabled={action.busy || !pack}
                onClick={() =>
                  void action.run(
                    "reactions/packs.export",
                    { pack_id: pack },
                    currentPack?.revision ?? null,
                  )
                }
              >
                导出当前图包
              </button>
            </div>
            <CommandFeedback {...index} />
            <CommandFeedback {...action} />
            {action.result?.data?.artifact && (
              <ArtifactList artifacts={[action.result.data.artifact]} />
            )}
            {currentPack && (
              <PackSettings
                key={currentPack.id + currentPack.revision}
                pack={currentPack}
                refresh={refresh}
              />
            )}
          </section>
          <DetailCard title="导入图片、动图或ZIP图包">
            <form
              className="domain-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (!session) return;
                const form = event.currentTarget;
                const data = new FormData(form);
                const files = data
                  .getAll("files")
                  .filter(
                    (file): file is File =>
                      file instanceof File && file.size > 0,
                  );
                if (!files.length) return;
                setError("");
                setUploading(true);
                void (async () => {
                  const images = [];
                  let archive: ArtifactRef | undefined;
                  for (const file of files) {
                    const uploaded = await uploadMedia(
                      file,
                      scope,
                      session.csrf,
                      new AbortController().signal,
                    );
                    if (file.name.toLowerCase().endsWith(".zip")) {
                      if (archive)
                        throw new Error(
                          "一次导入仅支持一个ZIP图包，可同时添加图片。",
                        );
                      archive = uploaded.artifact;
                    } else
                      images.push({
                        filename: file.name,
                        media: uploaded.artifact.media,
                      });
                  }
                  const result = await action.run(
                    "reactions/packs.import",
                    {
                      name: data.get("name"),
                      ...(data.get("role_only") === "on" && actor
                        ? { actor_ids: [actor] }
                        : {}),
                      files: images,
                      ...(archive ? { archive_media: archive.media } : {}),
                    },
                    0,
                  );
                  if (result) {
                    form.reset();
                    void refresh();
                  }
                })()
                  .catch((cause) =>
                    setError(
                      cause instanceof Error
                        ? cause.message
                        : "上传未确认，请核对原操作。",
                    ),
                  )
                  .finally(() => setUploading(false));
              }}
            >
              <label>
                图包名称
                <input name="name" required />
              </label>
              <label>
                原始图片、GIF或ZIP
                <input
                  name="files"
                  type="file"
                  accept="image/*,.zip"
                  multiple
                  required
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  name="role_only"
                  defaultChecked
                  disabled={!actor}
                />
                仅当前角色使用
              </label>
              <p className="muted">
                使用当前授权会话范围，文件保持原始字节；GIF不会只保留首帧。
              </p>
              <button
                className="button primary"
                disabled={uploading || action.busy}
              >
                上传并导入图包
              </button>
              {error && <p role="alert">{error}</p>}
            </form>
          </DetailCard>
          <ReactionSearch pack={pack} />
          <section className="panel">
            <h3>素材预览、批量设置与纠错</h3>
            <BatchSettings assets={chosen} refresh={refresh} />
            <div className="domain-grid">
              {visible.map((asset) => (
                <div key={asset.id + asset.revision}>
                  <label>
                    <input
                      type="checkbox"
                      checked={selected.includes(asset.id)}
                      onChange={(event) =>
                        setSelected((current) =>
                          event.target.checked
                            ? [...current, asset.id]
                            : current.filter((id) => id !== asset.id),
                        )
                      }
                    />
                    选择此素材
                  </label>
                  <ReactionCard asset={asset} refresh={refresh} />
                </div>
              ))}
            </div>
            {!visible.length && (
              <p className="muted">
                当前已读取页面没有此图包素材，可继续加载；这不表示后续页面为空。
              </p>
            )}
            {view.data.next_cursor && (
              <button
                className="button"
                disabled={view.loading}
                onClick={() => setCursor(view.data!.next_cursor)}
              >
                继续加载素材
              </button>
            )}
          </section>
        </>
      )}
    </div>
  );
}
function PackSettings({
  pack,
  refresh,
}: {
  pack: ReactionPack;
  refresh: () => Promise<void>;
}) {
  const command = useDomainCommand("reaction-pack:" + pack.id);
  return (
    <DetailCard title="图包名称与启停">
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void command
            .run(
              "reactions/packs.update",
              {
                id: pack.id,
                name: String(data.get("name")),
                enabled: data.get("enabled") === "on",
              },
              pack.revision,
            )
            .then((answer) => {
              if (answer) void refresh();
            });
        }}
      >
        <label>
          名称
          <input name="name" defaultValue={pack.name} required />
        </label>
        <label>
          <input name="enabled" type="checkbox" defaultChecked={pack.enabled} />
          允许角色从此图包选图
        </label>
        <button className="button" disabled={command.busy}>
          保存图包
        </button>
        <CommandFeedback {...command} />
      </form>
    </DetailCard>
  );
}
function ReactionSearch({ pack }: { pack: string }) {
  const query = useDomainCommand<{
    selected: ReactionAsset | null;
    reason: string | null;
    candidates: string[];
    source_refs?: SourceRef[];
  }>("reaction-purpose:" + pack);
  return (
    <section className="panel">
      <h3>交流用途选图</h3>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void query.run(
            "reactions/search",
            {
              meaning: String(data.get("meaning")),
              purpose: String(data.get("purpose")),
              attitude: String(data.get("attitude")),
              intensity: Number(data.get("intensity")),
              ironic: data.get("ironic") === "on",
              recent_asset_ids: [],
              requested_pack_ids: pack ? [pack] : [],
            },
            null,
          );
        }}
      >
        <label>
          回应要表达的意思
          <textarea name="meaning" required />
        </label>
        <div className="domain-grid">
          <label>
            交流用途
            <input name="purpose" required placeholder="安慰、期待、问候…" />
          </label>
          <label>
            态度
            <input name="attitude" required placeholder="温柔、俏皮、郑重…" />
          </label>
          <label>
            表达强度
            <input
              type="number"
              name="intensity"
              min="0"
              max="1"
              step="0.1"
              defaultValue="0.5"
              required
            />
          </label>
        </div>
        <label>
          <input name="ironic" type="checkbox" />
          反讽语境
        </label>
        <button className="button" disabled={query.busy}>
          按用途实际选图
        </button>
        <CommandFeedback {...query} />
      </form>
      {query.result?.data && (
        <>
          {query.result.data.selected ? (
            <article className="domain-record">
              <MediaPreview media={query.result.data.selected.media} />
              <p>{query.result.data.selected.description}</p>
            </article>
          ) : (
            <p>{query.result.data.reason ?? "没有合适素材"}</p>
          )}
          <p>
            候选 {query.result.data.candidates.length}{" "}
            项，选图结果不会在此直接发送。
          </p>
          <SourceEvidence sources={query.result.data.source_refs ?? []} />
        </>
      )}
    </section>
  );
}
function BatchSettings({
  assets,
  refresh,
}: {
  assets: ReactionAsset[];
  refresh: () => Promise<void>;
}) {
  const command = useDomainCommand(
    "reaction-batch:" + assets.map((x) => x.id + ":" + x.revision).join(","),
  );
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void command
          .run(
            "reactions/assets.batch",
            {
              items: assets.map((asset) => ({
                id: asset.id,
                expected_revision: asset.revision,
                enabled: data.get("enabled") === "on",
                ...(String(data.get("tags")).trim()
                  ? {
                      tags: String(data.get("tags"))
                        .split(/[,，]/)
                        .map((x) => x.trim())
                        .filter(Boolean),
                    }
                  : {}),
              })),
            },
            null,
          )
          .then((answer) => {
            if (answer) void refresh();
          });
      }}
    >
      <p>已选择 {assets.length} 张素材；每张按自身修订保存。</p>
      <label>
        <input type="checkbox" name="enabled" defaultChecked />
        允许选用
      </label>
      <label>
        统一标签（留空保留每张原标签）
        <input name="tags" />
      </label>
      <button
        className="button"
        disabled={command.busy || !assets.length || assets.length > 200}
      >
        批量保存选中素材
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
function ReactionCard({
  asset,
  refresh,
}: {
  asset: ReactionAsset;
  refresh: () => Promise<void>;
}) {
  const action = useDomainCommand("reaction-asset:" + asset.id);
  return (
    <article className="domain-record reaction-card">
      <MediaPreview media={asset.media} alt={asset.description || "表情素材"} />
      <p>{asset.description || "尚未标注"}</p>
      <small>
        {asset.animated ? "动图" : "静态图"} · {asset.index_state} ·{" "}
        {asset.enabled ? "启用" : "停用"}
      </small>
      <DetailCard title="描述、用途与启用设置">
        <form
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void action
              .run(
                "reactions/assets.update",
                {
                  id: asset.id,
                  description: data.get("description"),
                  ocr: data.get("ocr"),
                  actions: String(data.get("actions"))
                    .split(/[,，]/)
                    .map((x) => x.trim())
                    .filter(Boolean),
                  purposes: String(data.get("purposes"))
                    .split(/[,，]/)
                    .map((x) => x.trim())
                    .filter(Boolean),
                  tags: String(data.get("tags"))
                    .split(/[,，]/)
                    .map((x) => x.trim())
                    .filter(Boolean),
                  enabled: data.get("enabled") === "on",
                },
                asset.revision,
              )
              .then((answer) => {
                if (answer) void refresh();
              });
          }}
        >
          <label>
            画面描述
            <textarea name="description" defaultValue={asset.description} />
          </label>
          <label>
            图中文字
            <input name="ocr" defaultValue={asset.ocr} />
          </label>
          <label>
            动作标签
            <input name="actions" defaultValue={asset.actions.join("，")} />
          </label>
          <label>
            交流用途
            <input name="purposes" defaultValue={asset.purposes.join("，")} />
          </label>
          <label>
            自定义标签
            <input name="tags" defaultValue={asset.tags?.join("，") ?? ""} />
          </label>
          <label>
            <input
              name="enabled"
              type="checkbox"
              defaultChecked={asset.enabled}
            />
            允许选用
          </label>
          <button className="button" disabled={action.busy}>
            保存纠正
          </button>
        </form>
        <div className="domain-actions">
          <button
            className="button"
            disabled={action.busy}
            onClick={() =>
              void action
                .run("reactions/annotate", { id: asset.id }, asset.revision)
                .then(() => void refresh())
            }
          >
            实际理解并标注此素材
          </button>
          <button
            className="button"
            disabled={action.busy}
            onClick={() =>
              void action
                .run(
                  "reactions/assets.delete",
                  { id: asset.id },
                  asset.revision,
                )
                .then((answer) => {
                  if (answer) void refresh();
                })
            }
          >
            删除素材
          </button>
        </div>
        <CommandFeedback {...action} />
      </DetailCard>
    </article>
  );
}
