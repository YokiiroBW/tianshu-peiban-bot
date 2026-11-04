import { RefreshCw } from "lucide-react";
import {
  useDomainCommand,
  useDomainResource,
  GLOBAL_SCOPE,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { StatusRail } from "../../components/StatusRail";
import type { DevicesView } from "./deviceTypes";
import "./home.css";

export default function HomePage() {
  const view = useDomainResource<DevicesView>("devices/view", GLOBAL_SCOPE);
  const action = useDomainCommand("devices", "global");
  async function run(
    path: string,
    body: object,
    revision: number | null = null,
  ) {
    const result = await action.run(path, body, revision);
    if (result) await view.refresh();
  }
  return (
    <section className="panel home-console" aria-labelledby="devices-title">
      <header className="home-toolbar">
        <div>
          <p className="eyebrow">家庭与服务</p>
          <h2 id="devices-title">家庭设备</h2>
          <p>
            读数来自登记的 Home Assistant 实体；控制后以实际读回状态确认效果。
          </p>
        </div>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          <RefreshCw aria-hidden="true" />
          读取已记录状态
        </button>
      </header>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && !view.data && <p role="status">正在读取登记设备…</p>}
      <CommandFeedback {...action} />
      {view.data && (
        <>
          {view.data.can_manage && (
            <a className="text-link" href="#/settings/home">
              管理连接、实体与动作模板
            </a>
          )}
          {!view.data.connections.length && (
            <p className="muted">当前没有登记的设备连接。</p>
          )}
          {view.data.connections.map((connection) => (
            <section key={connection.id} className="home-connector">
              <header className="section-heading">
                <h3>{connection.id}</h3>
                <span className="badge">
                  {!connection.enabled
                    ? "已停用"
                    : connection.configured
                      ? "已配置"
                      : "未配置"}
                </span>
              </header>
              {connection.reason && <p>{connection.reason}</p>}
              <button
                className="button"
                disabled={
                  action.busy || !connection.enabled || !connection.configured
                }
                onClick={() =>
                  void run("devices/refresh", { connection_id: connection.id })
                }
              >
                从设备读取一次
              </button>
              <div className="home-entities">
                {connection.entities.map((entity) => {
                  const reading = view.data!.readings.find(
                    (row) =>
                      row.connection_id === connection.id &&
                      row.entity_id === entity.entity_id,
                  );
                  const templates = connection.templates.filter(
                    (row) => row.entity_id === entity.entity_id,
                  );
                  const stale =
                    reading &&
                    Date.parse(reading.observed_at) +
                      connection.status_max_age_seconds * 1000 <
                      Date.now();
                  return (
                    <article className="home-entity" key={entity.entity_id}>
                      <div className="home-entity-head">
                        <h4>
                          {entity.label ||
                            reading?.attributes.friendly_name ||
                            entity.entity_id}
                        </h4>
                        <StatusRail
                          tone={
                            !reading || stale || reading.state === null
                              ? "gray"
                              : "blue"
                          }
                          label={
                            !reading
                              ? "尚无读数"
                              : stale
                                ? "读数已过时"
                                : reading.code === "observed"
                                  ? "已实际观测"
                                  : reading.code
                          }
                        />
                      </div>
                      <code>{entity.entity_id}</code>
                      <p className="empty-title">
                        {reading?.state ?? "未知"}
                        {reading?.attributes.unit_of_measurement
                          ? ` ${reading.attributes.unit_of_measurement}`
                          : ""}
                      </p>
                      {reading && (
                        <dl className="home-facts">
                          <div>
                            <dt>实际观测时间</dt>
                            <dd>
                              {new Date(reading.observed_at).toLocaleString(
                                "zh-CN",
                              )}
                            </dd>
                          </div>
                          <div>
                            <dt>读数修订</dt>
                            <dd>{reading.revision}</dd>
                          </div>
                          {reading.attributes.brightness !== undefined && (
                            <div>
                              <dt>亮度原值</dt>
                              <dd>{reading.attributes.brightness}</dd>
                            </div>
                          )}
                          {reading.attributes.color_temp_kelvin !==
                            undefined && (
                            <div>
                              <dt>色温</dt>
                              <dd>{reading.attributes.color_temp_kelvin} K</dd>
                            </div>
                          )}
                        </dl>
                      )}
                      {view.data!.can_control && templates.length > 0 && (
                        <div className="home-entity-actions">
                          {templates.map((template) => (
                            <button
                              className="button"
                              key={template.template_id}
                              disabled={
                                action.busy ||
                                !reading ||
                                !connection.enabled ||
                                !connection.configured
                              }
                              onClick={() =>
                                reading &&
                                void run(
                                  "devices/control",
                                  {
                                    connection_id: connection.id,
                                    template_id: template.template_id,
                                  },
                                  reading.revision,
                                )
                              }
                            >
                              {template.label ||
                                (template.service === "turn_on"
                                  ? "开启"
                                  : "关闭")}
                            </button>
                          ))}
                        </div>
                      )}
                      {reading &&
                        reading.connection_revision !== connection.revision && (
                          <p className="muted">
                            连接设置已改变，控制前请重新读取设备。
                          </p>
                        )}
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </>
      )}
    </section>
  );
}
