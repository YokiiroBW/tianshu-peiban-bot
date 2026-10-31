import { EmbeddingSettings } from "../settings/ProviderModelsPanel";
import { ModelFunctionsPanel } from "../settings/ModelFunctionsPanel";
import "./retrieval.css";
export function RetrievalSettings() {
  return (
    <div className="retrieval-settings">
      <EmbeddingSettings />
      <p>
        重排序与其它模型功能使用唯一 Models
        目录和共享容量；检索内部参数由部署配置统一管理。
      </p>
      <details>
        <summary>模型功能与重排序路由</summary>
        <ModelFunctionsPanel />
      </details>
    </div>
  );
}
