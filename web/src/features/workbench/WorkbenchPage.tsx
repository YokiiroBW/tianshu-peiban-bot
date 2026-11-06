import { ArrowUpRight, CalendarDays } from "lucide-react";
import { HostResources } from "./HostResources";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import type { LifeAccessView, LifeState } from "../life/domainTypes";
import { activityLabels } from "../life/domainTypes";
import type { DevicesView } from "../home/deviceTypes";
import { CurioSectionHead } from "../../components/curio/OrientalWidgets";

type Upcoming = {
  id: string;
  title: string;
  due_at: string | null;
  timezone: string;
  state: string;
  reason: string | null;
};

const commitmentStateLabels: Record<string, string> = {
  planned: "已约定",
  active: "进行中",
  pending: "待履行",
  overdue: "已到约定时间",
  completed: "已完成",
  cancelled: "已取消",
  expired: "已过期",
};

export default function WorkbenchPage() {
  const workspace = useRoleWorkspace();
  const access = useDomainResource<LifeAccessView>(
    "life/access",
    {},
    !!workspace.actor,
  );
  const canRead = !!workspace.actor && access.data?.can_read === true;
  const state = useDomainResource<LifeState>("life/state", {}, canRead);
  const commitments = useDomainResource<Page<Upcoming>>(
    "life/commitments",
    { limit: 20 },
    canRead,
  );
  const devices = useDomainResource<DevicesView>("devices/view", GLOBAL_SCOPE);
  const name =
    workspace.roles.find((row) => row.id === workspace.actor)?.name ?? "角色";

  return (
    <div className="workbench">
      <HostResources />
      <div className="workspace-columns">
        <section className="curio-card panel">
          <CurioSectionHead
            seal="况"
            level={2}
            title={`${name}的近况`}
            subtitle="当前活动、衣着与主观心绪"
            action={
              <a className="ink-link text-link" href="#/roles/life">
                生活
                <ArrowUpRight aria-hidden="true" />
              </a>
            }
          />
          {access.error && <p role="alert">{access.error}</p>}
          {state.error && <p role="alert">{state.error}</p>}
          {!workspace.actor ? (
            <p>先选择一位角色。</p>
          ) : access.data && !access.data.can_read ? (
            <p>当前范围没有生活状态读取权限。</p>
          ) : state.data ? (
            <div className="quiet-content">
              <p className="empty-title">
                {state.data.activity?.title ?? "当前没有活动安排"}
              </p>
              {state.data.role_status && <p>角色状态：{state.data.role_status === "resting" ? "休息" : "清醒"}</p>}
              {state.data.background_work && <p>后台：{state.data.background_work.length ? state.data.background_work.map((item) => `${item.title}（${activityLabels[item.state] ?? item.state}）`).join("、") : "当前没有后台活动"}</p>}
              {state.data.activity && (
                <p>
                  <span className="celadon-tag">
                    {activityLabels[state.data.activity.state] ??
                      state.data.activity.state}
                  </span>
                </p>
              )}
              <p>穿着：{state.data.outfit?.description ?? "未选择服装"}</p>
              <p className="mood-quote-box">
                {state.data.affect.feelings.find((feeling) =>
                  feeling.appraisal.trim(),
                )?.appraisal ?? "暂时没有新的主观记录。"}
              </p>
              {state.data.missing.length > 0 && (
                <p>本次缺失：{state.data.missing.join("、")}</p>
              )}
              <small className="muted">
                读取于{" "}
                {new Date(state.data.observed_at).toLocaleString("zh-CN")} ·
                角色生活场景为想象状态
              </small>
            </div>
          ) : (
            <p>正在读取当前生活状态…</p>
          )}
          <div className="curio-card-foot-link">
            <a className="ink-link text-link" href="#/roles/room">
              打开小屋 →
            </a>
          </div>
        </section>

        <section className="curio-card panel">
          <CurioSectionHead
            seal="器"
            level={2}
            title="家庭设备"
            subtitle="已登记连接与环境观测读数"
            action={
              <a className="ink-link text-link" href="#/home/devices">
                设备与控制
                <ArrowUpRight aria-hidden="true" />
              </a>
            }
          />
          {devices.error && <p role="alert">{devices.error}</p>}
          {devices.data ? (
            <div className="quiet-content">
              <p className="meta-tag">
                {devices.data.connections.length}个已登记连接 ·{" "}
                {devices.data.readings.length}项已保存观测
              </p>
              {!devices.data.readings.length && <p>尚无实际设备读数。</p>}
              <div className="kv-list" style={{ marginTop: "10px" }}>
                {devices.data.readings.slice(0, 6).map((row) => (
                  <div
                    className="kv-row"
                    key={`${row.connection_id}:${row.entity_id}`}
                  >
                    <div>
                      <strong>{row.label || "未命名设备"}</strong>
                      <br />
                      <small className="muted">
                        家庭设备 ·{" "}
                        {new Date(row.observed_at).toLocaleString("zh-CN")}
                      </small>
                    </div>
                    <span className="v">
                      {row.state ?? "未知"}{" "}
                      {row.attributes.unit_of_measurement ?? ""}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            !devices.error && <p>正在读取已记录设备状态…</p>
          )}
        </section>
      </div>

      <section className="curio-card panel">
        <CurioSectionHead
          seal="约"
          level={2}
          title={
            <>
              <CalendarDays aria-hidden="true" className="sr-only" />
              近期约定
            </>
          }
          subtitle="角色与伙伴之间待履行的明确约定"
          action={
            <a className="ink-link text-link" href="#/roles/commitments">
              管理约定
              <ArrowUpRight aria-hidden="true" />
            </a>
          }
        />
        {commitments.error && <p role="alert">{commitments.error}</p>}
        {commitments.data && (
          <>
            <div className="domain-grid">
              {commitments.data.items
                .filter(
                  (row) =>
                    !["completed", "cancelled", "expired"].includes(row.state),
                )
                .sort((a, b) =>
                  (a.due_at ?? "9999").localeCompare(b.due_at ?? "9999"),
                )
                .map((row) => (
                  <article className="curio-card domain-record" key={row.id}>
                    <strong>{row.title}</strong>
                    <p className="muted" style={{ marginTop: "6px" }}>
                      {row.due_at
                        ? new Date(row.due_at).toLocaleString("zh-CN", {
                            timeZone: row.timezone,
                          })
                        : "尚未约定时间"}{" "}
                      ·{" "}
                      <span className="celadon-tag">
                        {commitmentStateLabels[row.state] ?? row.state}
                      </span>
                    </p>
                    {row.reason && (
                      <p style={{ marginTop: "6px" }}>{row.reason}</p>
                    )}
                  </article>
                ))}
            </div>
            {commitments.data.items.length === 0 && (
              <p className="muted">当前读取范围没有约定。</p>
            )}
            {!commitments.data.coverage.complete && (
              <p className="muted">
                仅显示本页可读约定；完整安排请打开约定页。
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
