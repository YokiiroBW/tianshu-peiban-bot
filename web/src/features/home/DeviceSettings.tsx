import { useState } from "react";
import {
  GLOBAL_SCOPE,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import type {
  DeviceConnection,
  DeviceEntity,
  DeviceTemplate,
  DevicesView,
} from "./deviceTypes";

function Editor({
  initial,
  onSaved,
}: {
  initial?: DeviceConnection;
  onSaved: () => void;
}) {
  const [entities, setEntities] = useState<DeviceEntity[]>(
    initial?.entities ?? [{ entity_id: "", label: "" }],
  );
  const [templates, setTemplates] = useState<DeviceTemplate[]>(
    initial?.templates ?? [],
  );
  const command = useDomainCommand(
    `device-settings:${initial?.id ?? "new"}`,
    "global",
  );
  return (
    <form
      className="domain-form"
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        void command
          .run(
            "devices/connections.save",
            {
              id: initial?.id || data.get("id"),
              base_url: data.get("base_url"),
              enabled: data.get("enabled") === "on",
              entities,
              templates,
              timeout_seconds: Number(data.get("timeout")),
              status_max_age_seconds: Number(data.get("max_age")),
              ...(data.get("token") ? { token: data.get("token") } : {}),
            },
            initial?.revision ?? 0,
          )
          .then((result) => {
            if (result?.receipt?.execution === "succeeded") {
              (form.elements.namedItem("token") as HTMLInputElement).value = "";
              onSaved();
            }
          });
      }}
    >
      <div className="domain-grid">
        <label>
          连接标识
          <input
            name="id"
            required
            defaultValue={initial?.id}
            readOnly={!!initial}
          />
        </label>
        <label>
          Home Assistant 基础地址
          <input
            type="url"
            name="base_url"
            required
            defaultValue={initial?.base_url}
            placeholder="http://home-assistant:8123"
          />
        </label>
        <label>
          新访问令牌（留空保留）
          <input type="password" name="token" autoComplete="new-password" />
        </label>
        <label>
          请求超时秒数
          <input
            type="number"
            name="timeout"
            min="1"
            max="15"
            required
            defaultValue={initial?.timeout_seconds ?? 5}
          />
        </label>
        <label>
          读数有效秒数
          <input
            type="number"
            name="max_age"
            min="10"
            max="3600"
            required
            defaultValue={initial?.status_max_age_seconds ?? 120}
          />
        </label>
      </div>
      <label>
        <input
          type="checkbox"
          name="enabled"
          defaultChecked={initial?.enabled ?? true}
        />
        启用连接
      </label>
      <fieldset>
        <legend>实际读取的实体</legend>
        {entities.map((entity, index) => (
          <div className="domain-actions" key={index}>
            <label>
              实体 ID
              <input
                required
                pattern="(light|switch|sensor)\.[a-z0-9_]{1,64}"
                value={entity.entity_id}
                placeholder="light.living_room"
                onChange={(event) =>
                  setEntities(
                    entities.map((row, i) =>
                      i === index
                        ? { ...row, entity_id: event.target.value }
                        : row,
                    ),
                  )
                }
              />
            </label>
            <label>
              显示名称
              <input
                value={entity.label ?? ""}
                onChange={(event) =>
                  setEntities(
                    entities.map((row, i) =>
                      i === index ? { ...row, label: event.target.value } : row,
                    ),
                  )
                }
              />
            </label>
            <button
              type="button"
              className="button"
              disabled={entities.length === 1}
              onClick={() =>
                setEntities(entities.filter((_, i) => i !== index))
              }
            >
              移除
            </button>
          </div>
        ))}
        <button
          type="button"
          className="button"
          disabled={entities.length >= 32}
          onClick={() =>
            setEntities([...entities, { entity_id: "", label: "" }])
          }
        >
          添加读取实体
        </button>
      </fieldset>
      <fieldset>
        <legend>允许执行的灯与开关模板</legend>
        {templates.map((template, index) => (
          <div className="domain-grid" key={index}>
            <label>
              模板标识
              <input
                required
                value={template.template_id}
                onChange={(event) =>
                  setTemplates(
                    templates.map((row, i) =>
                      i === index
                        ? { ...row, template_id: event.target.value }
                        : row,
                    ),
                  )
                }
              />
            </label>
            <label>
              显示名称
              <input
                value={template.label ?? ""}
                onChange={(event) =>
                  setTemplates(
                    templates.map((row, i) =>
                      i === index ? { ...row, label: event.target.value } : row,
                    ),
                  )
                }
              />
            </label>
            <label>
              已登记灯或开关
              <select
                required
                value={template.entity_id}
                onChange={(event) =>
                  setTemplates(
                    templates.map((row, i) =>
                      i === index
                        ? { ...row, entity_id: event.target.value }
                        : row,
                    ),
                  )
                }
              >
                <option value="">选择实体</option>
                {entities
                  .filter((row) => /^(light|switch)\./.test(row.entity_id))
                  .map((row) => (
                    <option key={row.entity_id} value={row.entity_id}>
                      {row.label || row.entity_id}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              目标状态
              <select
                value={template.service}
                onChange={(event) =>
                  setTemplates(
                    templates.map((row, i) =>
                      i === index
                        ? {
                            ...row,
                            service: event.target
                              .value as DeviceTemplate["service"],
                          }
                        : row,
                    ),
                  )
                }
              >
                <option value="turn_on">开启</option>
                <option value="turn_off">关闭</option>
              </select>
            </label>
            <button
              type="button"
              className="button"
              onClick={() =>
                setTemplates(templates.filter((_, i) => i !== index))
              }
            >
              移除模板
            </button>
          </div>
        ))}
        <button
          type="button"
          className="button"
          disabled={templates.length >= 32}
          onClick={() =>
            setTemplates([
              ...templates,
              { template_id: "", label: "", entity_id: "", service: "turn_on" },
            ])
          }
        >
          添加动作模板
        </button>
      </fieldset>
      <p className="muted">
        凭据仅写入服务端。保存配置不会控制设备；设备页按实际读数修订执行登记的模板。
      </p>
      <button className="button primary" disabled={command.busy}>
        保存连接设置
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
export function DeviceSettings() {
  const view = useDomainResource<DevicesView>("devices/view", GLOBAL_SCOPE);
  return (
    <section className="domain-workspace">
      <header className="section-heading">
        <h2>家庭设备连接</h2>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          读取当前配置
        </button>
      </header>
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <>
          {view.data.connections.map((row) => (
            <DetailCard
              key={`${row.id}:${row.revision}`}
              title={`${row.id} · 修订 ${row.revision} · ${row.configured ? "凭据已保存" : "未配置凭据"}`}
            >
              {view.data!.can_manage ? (
                <Editor initial={row} onSaved={() => void view.refresh()} />
              ) : (
                <p>
                  {row.state} · {row.reason}
                </p>
              )}
            </DetailCard>
          ))}
          {view.data.can_manage && (
            <DetailCard title="添加 Home Assistant 连接">
              <Editor onSaved={() => void view.refresh()} />
            </DetailCard>
          )}
          <a href="#/home/devices">查看读数与执行模板</a>
        </>
      )}
    </section>
  );
}
