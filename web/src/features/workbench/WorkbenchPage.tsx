import { ArrowUpRight, CalendarDays } from "lucide-react";
import { HostResources } from "./HostResources";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useDomainResource } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import type { LifeAccessView, LifeState } from "../life/domainTypes";
import { activityLabels } from "../life/domainTypes";
import type { DevicesView } from "../home/deviceTypes";

type Upcoming = {
  id: string;
  title: string;
  due_at: string | null;
  timezone: string;
  state: string;
  reason: string | null;
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
  const devices = useDomainResource<DevicesView>("devices/view");
  const name =
    workspace.roles.find((row) => row.id === workspace.actor)?.name ?? "角色";
  return (
    <div className="workbench">
      <HostResources />
      <div className="workspace-columns">
        <section className="panel">
          <header className="section-heading">
            <h2>{name}的近况</h2>
            <a className="text-link" href="#/roles/life">
              生活
              <ArrowUpRight aria-hidden="true" />
            </a>
          </header>
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
              {state.data.activity && (
                <p>
                  {activityLabels[state.data.activity.state] ??
                    state.data.activity.state}
                </p>
              )}
              <p>穿着：{state.data.outfit?.description ?? "未选择服装"}</p>
              <p>
                {state.data.affect.feelings.find((feeling) =>
                  feeling.appraisal.trim(),
                )?.appraisal ?? "暂时没有新的主观记录。"}
              </p>
              {state.data.missing.length > 0 && (
                <p>本次缺失：{state.data.missing.join("、")}</p>
              )}
              <small>
                读取于{" "}
                {new Date(state.data.observed_at).toLocaleString("zh-CN")} ·
                角色生活场景为想象状态
              </small>
            </div>
          ) : (
            <p>正在读取当前生活状态…</p>
          )}
          <a className="text-link" href="#/roles/room">
            打开小屋
          </a>
        </section>
        <section className="panel">
          <header className="section-heading">
            <h2>家庭设备</h2>
            <a className="text-link" href="#/home/devices">
              设备与控制
              <ArrowUpRight aria-hidden="true" />
            </a>
          </header>
          {devices.error && <p role="alert">{devices.error}</p>}
          {devices.data ? (
            <div className="quiet-content">
              <p>
                {devices.data.connections.length}个已登记连接 ·{" "}
                {devices.data.readings.length}项已保存观测
              </p>
              {!devices.data.readings.length && <p>尚无实际设备读数。</p>}
              {devices.data.readings.slice(0, 6).map((row) => (
                <p key={`${row.connection_id}:${row.entity_id}`}>
                  <strong>{row.label || row.entity_id}</strong>：
                  {row.state ?? "未知"}{" "}
                  {row.attributes.unit_of_measurement ?? ""}
                  <br />
                  <small>
                    {row.connection_id} ·{" "}
                    {new Date(row.observed_at).toLocaleString("zh-CN")}
                  </small>
                </p>
              ))}
            </div>
          ) : (
            !devices.error && <p>正在读取已记录设备状态…</p>
          )}
        </section>
      </div>
      <section className="panel">
        <header className="section-heading">
          <h2>
            <CalendarDays aria-hidden="true" />
            近期约定
          </h2>
          <a className="text-link" href="#/roles/commitments">
            管理约定
            <ArrowUpRight aria-hidden="true" />
          </a>
        </header>
        {commitments.error && <p role="alert">{commitments.error}</p>}
        {commitments.data && (
          <>
            {commitments.data.items
              .filter(
                (row) =>
                  !["completed", "cancelled", "expired"].includes(row.state),
              )
              .sort((a, b) =>
                (a.due_at ?? "9999").localeCompare(b.due_at ?? "9999"),
              )
              .map((row) => (
                <article className="domain-record" key={row.id}>
                  <strong>{row.title}</strong>
                  <p>
                    {row.due_at
                      ? new Date(row.due_at).toLocaleString("zh-CN", {
                          timeZone: row.timezone,
                        })
                      : "尚未约定时间"}{" "}
                    · {row.state}
                  </p>
                  {row.reason && <p>{row.reason}</p>}
                </article>
              ))}
            {commitments.data.items.length === 0 && (
              <p>当前读取范围没有约定。</p>
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
