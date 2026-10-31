import { GLOBAL_SCOPE, useDomainResource } from "../../api/useDomainResource";
import { DeviceSettings } from "../home/DeviceSettings";
import { ConnectionSettings } from "../resources/ConnectionSettings";
import type { PageState } from "../resources/api";

function AssetSettings() {
  const view = useDomainResource<PageState>(
    "resources/assets/view",
    GLOBAL_SCOPE,
  );
  return (
    <section className="domain-workspace">
      <header className="section-heading">
        <h2>资产库连接</h2>
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
          {view.data.can_manage ? (
            <ConnectionSettings
              rows={view.data.configurations}
              onSaved={() => void view.refresh()}
            />
          ) : (
            <p>当前账号没有资产库配置权限。</p>
          )}
          <a className="text-link" href="#/resources/assets">
            浏览已授权资产
          </a>
        </>
      )}
    </section>
  );
}
export function ExternalConnectionsPanel({
  kind,
}: {
  kind: "assets" | "home";
}) {
  return kind === "home" ? <DeviceSettings /> : <AssetSettings />;
}
