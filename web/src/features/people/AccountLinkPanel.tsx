import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import {
  integrationPost,
  readFailure,
  writeFailure,
} from "../../app/integrationApi";
import { useAuth } from "../../app/Auth";
import {
  readRolePreference,
  saveRolePreference,
} from "../../app/rolePreference";
type Association = {
  association_id: string;
  version: number;
  state: "linked" | "revoked";
  scopes: { audience: string }[];
};
type Challenge = {
  challenge_id: string;
  state: string;
  expires_at?: string;
  consent_sentence?: string;
  association?: Association | null;
};
export function AccountLinkPanel({
  actor,
  qqId,
  csrf,
}: {
  actor: string;
  qqId: string;
  csrf: string;
}) {
  const { session } = useAuth();
  const storageKey = `tianshu-account-link:${session?.username ?? csrf}:${actor}:${qqId}`;
  const [challenge, setChallenge] = useState<Challenge | null>(null),
    [association, setAssociation] = useState<Association | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const current = useRef<AbortController | null>(null),
    beginId = useRef(requestId()),
    revokeId = useRef(requestId());
  useEffect(() => {
    const saved = readRolePreference(storageKey);
    if (saved) {
      setChallenge({ challenge_id: saved, state: "checking" });
      void act("status", saved);
    }
    return () => current.current?.abort();
  }, [storageKey, csrf]);
  async function act(
    name: "begin" | "status" | "complete" | "revoke",
    challengeId?: string,
  ) {
    if (current.current && !current.current.signal.aborted) return;
    const control = new AbortController();
    current.current = control;
    setBusy(true);
    setError("");
    const body =
      name === "begin"
        ? {
            actor_id: actor,
            target_account: { namespace: "qq", immutable_account_id: qqId },
            client_request_id: beginId.current,
          }
        : {
            actor_id: actor,
            challenge_id: challengeId ?? challenge?.challenge_id,
            ...(name === "revoke"
              ? {
                  expected_revision: association?.version,
                  client_request_id: revokeId.current,
                }
              : {}),
          };
    try {
      const answer = await integrationPost<Challenge | Association>(
        `memory-links/${name}`,
        body,
        csrf,
        control.signal,
      );
      if (control.signal.aborted) return;
      if (name === "begin") {
        setChallenge(answer as Challenge);
        saveRolePreference(storageKey, (answer as Challenge).challenge_id);
      } else if (name === "status") {
        setChallenge((old) => ({ ...old!, ...(answer as Challenge) }));
        setAssociation((answer as Challenge).association ?? null);
      } else {
        setAssociation(answer as Association);
        setChallenge((old) =>
          old ? { ...old, state: (answer as Association).state } : old,
        );
      }
    } catch (cause) {
      if (!control.signal.aborted)
        setError(name === "status" ? readFailure(cause) : writeFailure(cause));
    } finally {
      if (current.current === control) current.current = null;
      if (!control.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h3>关联自己的账号</h3>
      <p>
        把当前登录账号与 QQ {qqId} 关联，需要这个 QQ
        账号本人通过真实私聊确认。关联保持各自人物记录，并只接续批准的读取范围。
      </p>
      {!challenge ? (
        <button
          className="button primary"
          disabled={busy}
          onClick={() => void act("begin")}
        >
          开始账号关联
        </button>
      ) : (
        <>
          <p>
            状态：
            {challenge.state === "pending"
              ? "等待目标账号确认"
              : challenge.state === "consented"
                ? "本人已确认，可提交关联"
                : ["active", "linked"].includes(challenge.state)
                  ? "关联生效"
                  : challenge.state === "checking"
                    ? "正在恢复关联进度"
                    : challenge.state === "revoked"
                      ? "关联已撤销"
                      : challenge.state}
          </p>
          {challenge.expires_at && (
            <p>
              确认期限：{new Date(challenge.expires_at).toLocaleString("zh-CN")}
            </p>
          )}
          {challenge.consent_sentence && challenge.state === "pending" && (
            <div className="life-record">
              <p>请由目标 QQ 账号本人在这位角色的私聊中发送：</p>
              <pre className="life-consent-sentence">
                {challenge.consent_sentence}
              </pre>
              <button
                className="button"
                onClick={() =>
                  void navigator.clipboard
                    .writeText(challenge.consent_sentence!)
                    .catch(() => setError("未能复制，请手动选择上面的确认句。"))
                }
              >
                复制确认句
              </button>
            </div>
          )}
          <div className="life-action-row">
            <button
              className="button"
              disabled={busy}
              onClick={() => void act("status")}
            >
              查询确认状态
            </button>
            <button
              className="button primary"
              disabled={busy || challenge.state !== "consented"}
              onClick={() => void act("complete")}
            >
              完成账号关联
            </button>
            {association?.state === "linked" && (
              <button
                className="button"
                disabled={busy || !association.version}
                onClick={() => void act("revoke")}
              >
                撤销关联读取
              </button>
            )}
          </div>
          {association && (
            <p role="status">
              {association.state === "linked"
                ? "关联已由记忆服务确认"
                : "关联已撤销"}{" "}
              · 版本 {association.version}
              {association.state === "linked" &&
                ` · 已批准 ${association.scopes.length} 处来源范围`}
            </p>
          )}
        </>
      )}
      {challenge && ["expired", "revoked"].includes(challenge.state) && (
        <button
          className="button"
          onClick={() => {
            beginId.current = requestId();
            revokeId.current = requestId();
            saveRolePreference(storageKey, null);
            setChallenge(null);
            setAssociation(null);
            setError("");
          }}
        >
          重新发起关联
        </button>
      )}
      {busy && <p role="status">正在核对真实账号与关联回执…</p>}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </section>
  );
}
