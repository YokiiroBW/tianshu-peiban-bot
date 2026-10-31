import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useState, type FormEvent } from "react";
import { credentialStatus } from "./api";
import type {
  CredentialChange,
  CredentialView,
  Skill,
  SkillAccess,
} from "./types";
import { readFailure } from "../../../app/integrationApi";

type Save = (
  value: object,
  credential: CredentialChange,
  revision: number | null,
) => Promise<boolean>;

/** Only this provider's installed adapter owns this connection shape. */
export function SkillConnection({
  skill,
  access,
  busy,
  save,
}: {
  skill: Skill;
  access: SkillAccess;
  busy: boolean;
  save: Save;
}) {
  const [url, setUrl] = useState(skill.config.base_url ?? "");
  const [credentials, setCredentials] = useState<CredentialView | null>(null);
  const [token, setToken] = useState("");
  const [clear, setClear] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setCredentials(null);
    setError("");
    credentialStatus(access, { skill_id: skill.id }, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setCredentials(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(readFailure(cause));
      });
    return () => controller.abort();
  }, [access.actor, access.csrf, skill.id, skill.revision]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!credentials) return;
    const success = await save(
      {
        definition: skill.definition,
        enabled: skill.enabled,
        config: {
          provider: "gscore",
          base_url: url,
          options: skill.config.options,
        },
      },
      clear
        ? { action: "clear" }
        : token
          ? { action: "replace", value: token }
          : { action: "keep" },
      credentials.revision,
    );
    if (success) {
      setToken("");
      setClear(false);
    }
  }

  return (
    <form
      className="skills-form external-form"
      onSubmit={(event) => void submit(event)}
    >
      <h4>GSCore 连接</h4>
      <p className="muted">
        连接支持插件调用的 GSCore HTTP 服务，查询游戏攻略与配队资料。
      </p>
      <DetailCard title="GSCore 服务要求">
        <p>
          当前已检查的实例接口未启用，仍需服务端兼容性验证。服务必须能完成插件调用；保存地址不代表已连接。
        </p>
      </DetailCard>
      <label>
        服务地址
        <input
          type="url"
          value={url}
          required
          placeholder="http://192.168.31.210:28765"
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>
      <label>
        GSCore WS_TOKEN
        <input
          type="password"
          autoComplete="new-password"
          value={token}
          disabled={clear}
          placeholder={
            credentials?.credential_configured
              ? "已保存；留空保留"
              : "填写 GSCore 的 WS_TOKEN"
          }
          onChange={(event) => setToken(event.target.value)}
        />
      </label>
      <small>使用 GSCore 的 WS_TOKEN，不填写网页登录密码。</small>
      {credentials?.credential_configured && (
        <label className="skills-check">
          <input
            type="checkbox"
            checked={clear}
            onChange={(event) => setClear(event.target.checked)}
          />
          清除已保存令牌
        </label>
      )}
      {error && (
        <p className="skills-error" role="alert">
          {error}
        </p>
      )}
      <button
        className="button button-primary"
        disabled={busy || !credentials}
        type="submit"
      >
        保存 GSCore 连接
      </button>
    </form>
  );
}
