import { actorScope, useDomainResource } from "../../api/useDomainResource";
import { VoiceProfileEditor } from "./VoiceProfileEditor";
import { roleVoiceProfile, type SpeechView } from "./types";
import "./multimodal.css";

export function RoleSpeechSettings({ actor }: { actor: string }) {
  const view = useDomainResource<SpeechView>("speech/view", actorScope(actor));
  return (
    <section className="panel domain-workspace" aria-label="角色声音设置">
      <header className="domain-toolbar">
        <div>
          <h3>角色声音</h3>
          <p>选择已连接账户中的音色，试听后绑定到当前角色。</p>
        </div>
        <button
          className="button"
          disabled={view.loading}
          onClick={() => void view.refresh()}
        >
          刷新连接
        </button>
      </header>
      {view.error && <p role="alert">{view.error}</p>}
      {view.loading && !view.data && <p role="status">正在读取角色声音…</p>}
      {view.data && (
        <VoiceProfileEditor
          key={JSON.stringify(
            view.data.profiles.map((profile) => [profile.id, profile.revision]),
          )}
          actor={actor}
          profile={roleVoiceProfile(view.data.profiles, actor)}
          view={view.data}
          refresh={view.refresh}
        />
      )}
    </section>
  );
}
