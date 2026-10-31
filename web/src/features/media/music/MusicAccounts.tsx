import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useMemo, useRef, useState } from "react";
import qrcode from "qrcode-generator";
import { QrCode } from "lucide-react";
import type { MediaController } from "../useMediaController";
import { timestamp, word } from "../wording";
import {
  providerNames,
  type MusicAccount,
  type MusicProvider,
  type MusicView,
} from "./types";

type Qr = {
  qr_id: string;
  url: string;
  image?: string | null;
  expires_at: number;
};

export function MusicAccounts({
  media,
  accounts,
  refresh,
  providers,
}: {
  media: MediaController;
  accounts: MusicAccount[];
  refresh: () => Promise<void>;
  providers?: MusicView["providers"];
}) {
  const [qr, setQr] = useState<Qr | null>(null);
  const [provider, setProvider] = useState<MusicProvider>("netease");
  const [loginType, setLoginType] = useState("qq");
  const [state, setState] = useState("waiting");
  const [starting, setStarting] = useState(false);
  const current = useRef({ media, refresh });
  current.current = { media, refresh };
  const image = useMemo(() => {
    if (!qr) return null;
    if (qr.image?.startsWith("data:image/")) return qr.image;
    if (!qr.url) return null;
    const code = qrcode(0, "M");
    code.addData(qr.url);
    code.make();
    return code.createDataURL(5, 16);
  }, [qr]);
  useEffect(() => {
    if (!qr) return;
    let stopped = false;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (stopped || document.hidden) return;
      const mine = ++generation;
      if (Date.now() >= qr.expires_at * 1000) {
        setState("expired");
        return;
      }
      const value = await current.current.media.request<{
        state: string;
        account: MusicAccount | null;
      }>("music-qr-poll", "music/accounts/qr/poll", { qr_id: qr.qr_id });
      if (stopped || mine !== generation) return;
      if (value) {
        setState(value.state);
        if (value.state === "ready") {
          setQr(null);
          current.current.media.setNotice("音乐账号已连接，可以选择歌单同步。");
          await current.current.refresh();
          return;
        }
        if (value.state === "expired") return;
      }
      timer = setTimeout(poll, value ? 5000 : 15000);
    };
    const visible = () => {
      generation += 1;
      clearTimeout(timer);
      current.current.media.cancel("music-qr-poll");
      if (!document.hidden) void poll();
    };
    void poll();
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      current.current.media.cancel("music-qr-poll");
      document.removeEventListener("visibilitychange", visible);
    };
  }, [qr]);
  useEffect(() => () => current.current.media.cancel("music-qr-start"), []);
  async function start() {
    setStarting(true);
    setQr(null);
    media.clearError();
    const value = await media.request<Qr>(
      "music-qr-start",
      "music/accounts/qr/start",
      { provider, login_type: provider === "qq" ? loginType : "qr" },
    );
    setStarting(false);
    if (value) {
      setState("waiting");
      setQr(value);
    }
  }
  return (
    <section className="media-card media-stack" aria-label="音乐账号">
      <div>
        <h3>连接音乐账号</h3>
        <p className="muted">
          扫码连接歌单。下载时自动检查已连接账号的权限，无需设置渠道优先级。
        </p>
      </div>
      <DetailCard title="连接音乐账号">
        {media.error && (
          <p className="media-message media-warning" role="alert">
            {media.error}
          </p>
        )}
        {media.notice && (
          <p className="media-message" role="status">
            {media.notice}
          </p>
        )}{" "}
        <label>
          音乐平台
          <select
            value={provider}
            disabled={starting}
            onChange={(event) => {
              setQr(null);
              setProvider(event.target.value as MusicProvider);
            }}
          >
            {(
              providers ?? [
                {
                  provider: "netease" as const,
                  label: "网易云音乐",
                  available: true,
                },
              ]
            ).map((item) => (
              <option
                key={item.provider}
                value={item.provider}
                disabled={!item.available}
              >
                {item.label}
                {item.available ? "" : "（工具不可用）"}
              </option>
            ))}
          </select>
        </label>
        {provider === "qq" && (
          <label>
            登录方式
            <select
              value={loginType}
              disabled={starting}
              onChange={(event) => {
                setQr(null);
                setLoginType(event.target.value);
              }}
            >
              <option value="qq">QQ 扫码</option>
              <option value="wx">微信扫码</option>
            </select>
          </label>
        )}
        <button
          className="button primary"
          disabled={starting || !!media.busy}
          onClick={() => void start()}
        >
          <QrCode aria-hidden="true" />
          {starting
            ? "正在生成…"
            : qr
              ? "重新生成二维码"
              : `${providerNames[provider]}扫码登录`}
        </button>
        {qr && (
          <div className="media-qr">
            <p role="status">
              {{
                waiting: "等待扫码",
                scanned: "已扫码，请在手机确认",
                expired: "二维码已过期",
                ready: "已连接",
              }[state] ?? "正在读取登录状态"}
            </p>
            {state !== "expired" && image && (
              <img src={image} alt={`${providerNames[provider]}登录二维码`} />
            )}
            <p className="muted">有效至 {timestamp(qr.expires_at)}</p>
          </div>
        )}
      </DetailCard>{" "}
      {accounts.length === 0 && <p className="muted">尚未连接音乐账号。</p>}
      {accounts.map((account) => (
        <div className="music-account" key={account.account_id}>
          <div>
            <strong>{account.label}</strong>
            <p className="muted">
              {word(account.state)} · {providerNames[account.provider]} ID{" "}
              {account.user_id}
            </p>
          </div>
          {account.state !== "revoked" && (
            <button
              className="button"
              disabled={!!media.busy}
              onClick={async () => {
                if (
                  await media.command("music/accounts/revoke", {
                    account_id: account.account_id,
                    expected_revision: account.revision,
                  })
                )
                  await refresh();
              }}
            >
              断开账号
            </button>
          )}
        </div>
      ))}
    </section>
  );
}
