import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import type { MediaController } from "../useMediaController";
import type { AudioSettings } from "./audioTypes";

export function BookAudioSettings({ media }: { media: MediaController }) {
  const current = useRef(media);
  current.current = media;
  const [data, setData] = useState<AudioSettings | null>(null);
  useEffect(() => {
    void current.current.request<AudioSettings>("book-speech-settings", "books/audio/settings", {}).then((result) => {
      if (result) setData(result);
    });
    return () => current.current.cancel("book-speech-settings");
  }, []);
  return <DetailCard title="听书音色档案">
    {media.error && <p role="alert">{media.error}</p>}
    <p>听书与聊天使用同一份语音连接和音色档案。生成前选择音色与章节，已保存音频可继续播放。</p>
    <a className="button" href="#/settings/speech">管理语音连接、音色与词典</a>
    {!data ? <p role="status">正在读取音色档案…</p> : data.voice_profiles.length === 0 ? <p>尚无音色档案，请先在语音设置中添加。</p> :
      <ul>{data.voice_profiles.map((profile) => {
        const connection = data.connections.find((item) => item.id === profile.connection_id);
        return <li key={profile.id}><strong>{profile.name}</strong> · {profile.model} · 版本 {profile.revision} · {connection?.enabled && connection.configured ? "已配置" : "连接未配置或已停用"}</li>;
      })}</ul>}
  </DetailCard>;
}
