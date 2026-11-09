import { useEffect, useState } from "react";
import { useDomainCommand } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { MediaPreview } from "../../components/MediaPreview";
import type { FishConnection, Voice, VoiceSearchResult } from "./types";

/** Mounted per connection so results and pending reads never cross accounts. */
export function VoicePicker({
  connection,
  selected,
  onSelect,
}: {
  connection: FishConnection;
  selected?: string;
  onSelect?: (voice: Voice) => void;
}) {
  const command = useDomainCommand<VoiceSearchResult>(
    "voice-picker:" + connection.id,
    "actor",
  );
  const supportsOwned = ["mcp", "native"].includes(connection.transport);
  const [owned, setOwned] = useState(supportsOwned);
  const [query, setQuery] = useState("");
  const [criteria, setCriteria] = useState({ query: "", owned: supportsOwned });
  const [voices, setVoices] = useState<Voice[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const execution = command.result?.receipt?.execution;

  async function search(value: typeof criteria, next: string | null = null) {
    if (!next) {
      setVoices([]);
      setCursor(null);
      setLoaded(false);
    }
    setCriteria(value);
    const result = await command.run("speech/voices.search", {
      connection_id: connection.id,
      ...value,
      cursor: next,
    });
    if (result?.data?.items) {
      setVoices((current) => {
        const combined = next
          ? [...current, ...result.data!.items]
          : result.data!.items;
        return [
          ...new Map(combined.map((voice) => [voice.id, voice])).values(),
        ];
      });
      setCursor(result.data.next_cursor);
      setLoaded(true);
    }
  }

  useEffect(() => {
    void search({ query: "", owned: supportsOwned });
    // The parent keys this picker by account; requests use the existing command lifecycle.
  }, []);

  return (
    <section className="voice-picker" aria-label="选择音色">
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          void search({ query: query.trim(), owned });
        }}
      >
        <div className="domain-grid">
          {supportsOwned && (
            <label>
              音色范围
              <select
                value={owned ? "owned" : "public"}
                disabled={command.busy}
                onChange={(event) => {
                  const nextOwned = event.target.value === "owned";
                  setOwned(nextOwned);
                  void search({ query: query.trim(), owned: nextOwned });
                }}
              >
                <option value="owned">我的音色</option>
                <option value="public">公开音色库</option>
              </select>
            </label>
          )}
          <label>
            查找音色
            <input
              type="search"
              value={query}
              placeholder="输入音色名称或描述"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        </div>
        <button className="button" disabled={command.busy}>
          {command.busy ? "正在读取音色…" : "刷新音色列表"}
        </button>
      </form>
      {(command.error || (execution && execution !== "succeeded")) && (
        <CommandFeedback {...command} />
      )}
      {command.result?.receipt?.error_code ===
        "speech_owned_voices_unsupported" && (
        <p role="alert">
          当前连接未提供“我的音色”查询，可切换公开音色库，或手动填写音色 ID。
        </p>
      )}
      {loaded && !voices.length && (
        <p role="status">
          {criteria.owned
            ? "当前账户未查到符合条件的自有音色。可以切换到公开音色库，或手动填写音色 ID。"
            : "未查到符合条件的音色，可以换个名称搜索，或手动填写音色 ID。"}
        </p>
      )}
      <div className="voice-picker-list">
        {voices.map((voice) => (
          <article className="domain-record voice-option" key={voice.id}>
            <div>
              <strong>{voice.name || "未命名音色"}</strong>
              <p className="muted voice-id">音色 ID：{voice.id}</p>
              {voice.preview && (
                <MediaPreview media={voice.preview} alt={voice.name + "试听"} />
              )}
            </div>
            {onSelect && (
              <button
                type="button"
                className="button"
                aria-pressed={selected === voice.id}
                onClick={() => onSelect(voice)}
              >
                {selected === voice.id ? "已选中" : "选择此音色"}
              </button>
            )}
          </article>
        ))}
      </div>
      {cursor && (
        <button
          type="button"
          className="button"
          disabled={command.busy}
          onClick={() => void search(criteria, cursor)}
        >
          加载更多音色
        </button>
      )}
    </section>
  );
}
