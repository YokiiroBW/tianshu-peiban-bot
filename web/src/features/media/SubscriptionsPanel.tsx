import { useRef, useState } from "react";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import { SubscriptionEditor } from "./SubscriptionEditor";
import { ReasonNote } from "./ReasonNote";
import { destinationLabel } from "./destination";
import type { Subscription } from "./types";
import type { MediaController } from "./useMediaController";
import {
  destinationModeWords,
  sourceWords,
  timestamp,
  tone,
  word,
} from "./wording";

export function SubscriptionsPanel({ media }: { media: MediaController }) {
  const [editing, setEditing] = useState<Subscription | "new" | null>(null);
  const [limit, setLimit] = useState(10);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const trigger = useRef<HTMLElement | null>(null);
  const subscriptions = media.view?.subscriptions ?? [];
  const search = query.trim().toLocaleLowerCase();
  const accounts = new Map(
    media.view?.accounts.map((item) => [item.account_id, item.label]),
  );
  const targets = new Map(
    media.view?.targets.map((item) => [item.target_id, item]),
  );
  const subscriptionDirectory = (item: Subscription) => {
    const target = targets.get(item.target_id);
    return target?.kind === "assetlibrary"
      ? "由资产库管理"
      : destinationLabel(
          item.destination_directory ?? target?.directory,
          item.destination_directory ? "" : item.destination_subdirectory,
        );
  };
  const attention = (item: Subscription) =>
    item.state === "auth_required" || item.state === "rule_error";
  const matches = subscriptions.filter((item) => {
    const statusMatch =
      filter === "all" ||
      (filter === "attention"
        ? attention(item)
        : filter === "uninitialized"
          ? !item.baseline_ready
          : item.state === filter);
    const searchable = [
      item.label,
      item.source.id,
      sourceWords[item.source.kind],
      accounts.get(item.account_id ?? "") ?? "公开访问",
      targets.get(item.target_id)?.label ?? item.target_id,
      subscriptionDirectory(item),
      destinationModeWords[item.destination_mode ?? "custom"],
    ];
    return (
      statusMatch &&
      (!search ||
        searchable.some((value) => value.toLocaleLowerCase().includes(search)))
    );
  });
  const filtered = !!search || filter !== "all";
  const clearFilters = () => {
    setQuery("");
    setFilter("all");
    setLimit(10);
  };
  const edit = (item: Subscription | "new", element: HTMLElement) => {
    trigger.current = element;
    setEditing(item);
  };
  const close = () => {
    setEditing(null);
    requestAnimationFrame(() => trigger.current?.focus());
  };
  const control = async (
    item: Subscription,
    action = item.state === "active" ? "pause" : "resume",
  ) => {
    const result = await media.command("subscriptions/control", {
      subscription_id: item.subscription_id,
      expected_revision: item.revision,
      action,
    });
    if (result)
      media.setNotice(
        action === "remove"
          ? "订阅已删除，已入队任务和已归档文件保留。"
          : action === "pause"
            ? "订阅已暂停，已入队任务保留；取消任务请到下载任务。"
            : "订阅已恢复，后续扫描将按当前规则处理。",
      );
  };
  const scan = async (item: Subscription, recompute: boolean) => {
    const result = await media.command<{ queued: number }>(
      "subscriptions/scan",
      { subscription_id: item.subscription_id, recompute },
    );
    if (result)
      media.setNotice(
        `完整扫描已返回，本次入队 ${result.queued} 个任务。${recompute ? "历史成员按当前规则重新试算，已有成品仍按质量去重。" : ""}`,
      );
  };
  return (
    <section className="media-stack" aria-label="订阅管理">
      <div className="media-section-head">
        <div>
          <h2>订阅你关注的来源</h2>
          <p className="muted">
            以来源成员判断新增，规则变更默认仅影响以后的扫描。删除订阅保留文件和已入队任务。
          </p>
        </div>
        <button
          className="button primary"
          disabled={!!editing || !!media.busy}
          onClick={(event) => edit("new", event.currentTarget)}
        >
          <Plus aria-hidden="true" />
          新建订阅
        </button>
      </div>
      <dl className="media-subscription-stats" aria-label="已读取的订阅状态">
        <div>
          <dt>已读取</dt>
          <dd>{subscriptions.length} 个订阅</dd>
        </div>
        <div>
          <dt>运行中</dt>
          <dd>
            {subscriptions.filter((item) => item.state === "active").length}
          </dd>
        </div>
        <div>
          <dt>已暂停</dt>
          <dd>
            {subscriptions.filter((item) => item.state === "paused").length}
          </dd>
        </div>
        <div>
          <dt>需要处理</dt>
          <dd>{subscriptions.filter(attention).length}</dd>
        </div>
        <div>
          <dt>待初始化</dt>
          <dd>{subscriptions.filter((item) => !item.baseline_ready).length}</dd>
        </div>
      </dl>
      {!!subscriptions.length && (
        <div className="media-subscription-search media-form-grid">
          <label>
            查找订阅
            <input
              type="search"
              value={query}
              placeholder="名称、来源 ID、账号、媒体库或目录"
              onChange={(event) => {
                setQuery(event.target.value);
                setLimit(10);
              }}
            />
          </label>
          <label>
            订阅状态
            <select
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value);
                setLimit(10);
              }}
            >
              <option value="all">全部状态</option>
              <option value="active">运行中</option>
              <option value="paused">已暂停</option>
              <option value="attention">需要处理</option>
              <option value="auth_required">需要重新登录</option>
              <option value="rule_error">规则需要修正</option>
              <option value="uninitialized">待初始化</option>
            </select>
          </label>
          <div className="media-actions media-subscription-match">
            <p className="muted">
              {filtered
                ? `找到 ${matches.length} 个订阅`
                : `已显示 ${Math.min(limit, matches.length)} / ${matches.length} 个订阅`}
            </p>
            {filtered && (
              <button className="button" onClick={clearFilters}>
                清除查找与筛选
              </button>
            )}
          </div>
        </div>
      )}
      {editing && (
        <SubscriptionEditor
          key={editing === "new" ? "new" : editing.subscription_id}
          item={editing === "new" ? undefined : editing}
          media={media}
          close={close}
        />
      )}
      {!subscriptions.length && (
        <StatePanel kind="empty" title="还没有订阅">
          <p>
            添加 UP 主投稿、收藏夹、合集、系列或稍后再看，即可持续发现新增视频。
          </p>
        </StatePanel>
      )}
      {!!subscriptions.length && !matches.length && (
        <StatePanel
          kind="empty"
          title="没有匹配的订阅"
          action={
            <button className="button" onClick={clearFilters}>
              查看全部订阅
            </button>
          }
        >
          <p>尝试其他名称或来源，或清除当前查找与状态筛选。</p>
        </StatePanel>
      )}
      <div className="media-subscriptions">
        {matches.slice(0, limit).map((item) => (
          <article className="media-card" key={item.subscription_id}>
            <div className="media-section-head">
              <div>
                <h3>{item.label}</h3>
                <p className="muted">
                  {sourceWords[item.source.kind]}
                  {item.source.kind !== "watch_later" && ` · ${item.source.id}`}
                </p>
              </div>
              <StatusRail tone={tone(item.state)} label={word(item.state)} />
            </div>
            <dl className="media-facts">
              <div>
                <dt>成员基线</dt>
                <dd>
                  {item.baseline_ready
                    ? `完整 · ${item.counts.members} 个成员`
                    : "尚未完整建立"}
                </dd>
              </div>
              <div>
                <dt>已入队</dt>
                <dd>{item.counts.queued} 个任务</dd>
              </div>
              <div>
                <dt>最后成功扫描</dt>
                <dd>
                  {item.last_scan_at == null
                    ? "尚未成功扫描"
                    : timestamp(item.last_scan_at)}
                </dd>
              </div>
              <div>
                <dt>下次计划</dt>
                <dd>
                  {item.state === "active"
                    ? item.next_scan_at > 0
                      ? timestamp(item.next_scan_at)
                      : "等待后台安排扫描"
                    : item.state === "auth_required"
                      ? "重新登录后继续"
                      : item.state === "rule_error"
                        ? "修正规则后继续"
                        : "恢复订阅后安排"}
                </dd>
              </div>
              <div>
                <dt>扫描间隔</dt>
                <dd>
                  每{" "}
                  {item.interval_seconds % 60 === 0
                    ? `${item.interval_seconds / 60} 分钟`
                    : `${item.interval_seconds} 秒`}
                </dd>
              </div>
              <div>
                <dt>目标媒体库</dt>
                <dd>{targets.get(item.target_id)?.label ?? item.target_id}</dd>
              </div>
              <div>
                <dt>整理方式</dt>
                <dd>
                  {destinationModeWords[item.destination_mode ?? "custom"]}
                </dd>
              </div>
              <div>
                <dt>整理目录</dt>
                <dd>{subscriptionDirectory(item)}</dd>
              </div>
            </dl>
            {!item.baseline_ready && (
              <p className="media-warning">
                初始化未完成，订阅尚未建立可判断新增的完整基线。
              </p>
            )}
            {item.state === "auth_required" && (
              <p className="media-warning">
                当前账号需要重新登录。
                <a href="#/media/accounts">更新 B 站登录</a>后，再恢复订阅。
              </p>
            )}
            {item.state === "rule_error" && (
              <p className="media-warning">
                请编辑并试算规则，修正后再恢复订阅。
              </p>
            )}
            <ReasonNote code={item.code} />
            <div className="media-actions">
              <button
                className="button"
                disabled={!!editing || !!media.busy}
                onClick={(event) => edit(item, event.currentTarget)}
              >
                编辑
              </button>
              <button
                className="button"
                disabled={!!media.busy}
                onClick={() => void control(item)}
              >
                {item.state === "active" ? "暂停订阅" : "恢复订阅"}
              </button>
              <button
                className="button"
                disabled={!!media.busy}
                onClick={() => void scan(item, false)}
              >
                <RefreshCw aria-hidden="true" />
                立即扫描
              </button>
              <button
                className="button"
                disabled={!!media.busy}
                onClick={() => void scan(item, true)}
              >
                按当前规则重算历史
              </button>
              <button
                className="button"
                disabled={!!media.busy}
                onClick={() => void control(item, "remove")}
              >
                <Trash2 aria-hidden="true" />
                删除订阅
              </button>
            </div>
          </article>
        ))}
      </div>
      {matches.length > limit && (
        <button className="button" onClick={() => setLimit(limit + 10)}>
          显示更多订阅
        </button>
      )}
    </section>
  );
}
