import { DetailCard } from "../../components/DetailCard";
import { useState } from "react";
import {
  UserRound,
  Fingerprint,
  MessageCircle,
  Brain,
  ShieldCheck,
  Heart,
  MapPin,
  Clock3,
} from "lucide-react";
import RelationshipPanel from "../companion/relationships/RelationshipPanel";
import { SharedPortrait } from "./SharedPortrait";
import { Interactions } from "./Interactions";
import { ReplyPermissions } from "./ReplyPermissions";
import { ProactivePanel } from "./ProactivePanel";
import { AdminToggle } from "./AdminToggle";
import { AccountLinkPanel } from "./AccountLinkPanel";
import "../life/life.css";
import {
  dateText,
  personName,
  type Person,
  type Role,
  type Observations,
} from "./types";
const tabs = [
  { id: "overview", label: "概览", icon: UserRound },
  { id: "portrait", label: "画像与记忆", icon: Brain },
  { id: "messages", label: "最近互动", icon: MessageCircle },
  { id: "permissions", label: "回复权限", icon: ShieldCheck },
  { id: "relationship", label: "关系", icon: Heart },
  { id: "proactive", label: "主动偏好", icon: Clock3 },
  { id: "accounts", label: "账号关联", icon: Fingerprint },
];
export function PersonDetail({
  person,
  role,
  roles,
  memoryAvailable,
  csrf,
  observations,
  onObservationLocked,
}: {
  person: Person;
  role: Role | null;
  roles: Role[];
  memoryAvailable: boolean;
  csrf: string;
  observations: Observations | null;
  onObservationLocked: () => void;
}) {
  const [tab, setTab] = useState("overview");
  const latest = Math.max(0, ...person.sources.map((item) => item.last_at));
  return (
    <section
      className="people-detail"
      aria-label={`${personName(person)}的用户档案`}
    >
      <header className="people-person-header">
        <span className="people-avatar large">
          {person.profile?.display_name?.slice(0, 1) || (
            <UserRound aria-hidden="true" />
          )}
        </span>
        <div>
          <div className="people-person-title">
            <h2>{personName(person)}</h2>
            <span className="people-chip">QQ 用户</span>
            <AdminToggle
              key={`${csrf}:${person.qqId}`}
              qqId={person.qqId}
              csrf={csrf}
            />
          </div>
          <p>
            QQ {person.qqId}
            <span className="people-header-divider">·</span>
            {person.profile ? "已登记身份" : "已观察，身份待确认"}
          </p>
          <small>
            {role
              ? `以 ${role.label} 的视角查看`
              : "选择角色可查看共享画像与关系"}
          </small>
        </div>
      </header>
      <div className="people-detail-tabs" role="tablist" aria-label="人物详情">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            role="tab"
            type="button"
            key={id}
            id={`person-tab-${id}`}
            aria-controls={`person-panel-${id}`}
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <div
        className="people-tab-content"
        role="tabpanel"
        id={`person-panel-${tab}`}
        aria-labelledby={`person-tab-${tab}`}
      >
        {tab === "overview" && (
          <>
            <div className="people-overview-grid">
              <article className="people-info-card">
                <h3>
                  <Fingerprint size={18} aria-hidden="true" />
                  身份档案
                </h3>
                <p>
                  {person.profile
                    ? "账号已映射到独立人物身份。"
                    : "已收到该账号的消息，尚未取得确认的人物身份。"}
                </p>
                <dl>
                  <div>
                    <dt>QQ 账号</dt>
                    <dd>{person.qqId}</dd>
                  </div>
                  <div>
                    <dt>人物身份</dt>
                    <dd>{person.profile ? "已确认" : "同步中"}</dd>
                  </div>
                </dl>
              </article>
              <article className="people-info-card">
                <h3>
                  <Clock3 size={18} aria-hidden="true" />
                  最近收到消息
                </h3>
                <strong className="people-last-seen">
                  {latest ? dateText(latest) : "暂无可读观察记录"}
                </strong>
                <p>
                  {latest
                    ? "这是入站消息的观察时间，不代表机器人已回复。"
                    : "身份存在与消息归档是两件独立的事。"}
                </p>
              </article>
            </div>
            <article className="people-info-card">
              <h3>
                <MapPin size={18} aria-hidden="true" />
                相遇的地方
              </h3>
              {person.sources.length ? (
                <ul className="people-source-list">
                  {person.sources.map((source) => (
                    <li key={`${source.connection.id}:${source.conversation}`}>
                      <span className="people-source-icon">
                        <MessageCircle size={17} aria-hidden="true" />
                      </span>
                      <div>
                        <strong>
                          {source.conversation.startsWith("private:")
                            ? "私聊"
                            : `群 ${source.conversation.slice(6)}`}
                        </strong>
                        <small>
                          {source.connection.name} · 机器人{" "}
                          {source.connection.account_id}
                        </small>
                      </div>
                      <span>{source.count} 条已观察</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="people-muted">
                  当前已加载范围内没有可读会话记录。
                </p>
              )}
            </article>
            <article className="people-info-card">
              <h3>名字与别名</h3>
              {person.profile?.aliases.length ? (
                <div className="people-aliases">
                  {person.profile.aliases.map((alias, index) => (
                    <span
                      key={`${alias.kind}:${alias.group_id}:${index}`}
                      title={`${alias.group_id ? `群 ${alias.group_id} · ` : ""}${alias.observed_at ? dateText(alias.observed_at) : ""}`}
                    >
                      {alias.value}
                      <small>
                        {alias.group_id
                          ? `群 ${alias.group_id}`
                          : alias.kind === "nickname"
                            ? "昵称"
                            : "账号别名"}
                      </small>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="people-muted">暂时没有已记录别名。</p>
              )}
            </article>
            <p className="people-boundary">
              身份档案用于认出这个人；共享画像是角色可以读取的事实；互动记录是已归档的收到消息。任何一项存在，都不意味着另外两项已生成。
            </p>
          </>
        )}
        {tab === "portrait" && (
          <SharedPortrait
            person={person}
            role={role}
            available={memoryAvailable}
            csrf={csrf}
          />
        )}
        {tab === "messages" && <Interactions person={person} csrf={csrf} />}
        {tab === "proactive" &&
          (role ? (
            <ProactivePanel
              key={`${csrf}:${role.id}:${person.qqId}`}
              person={person}
              role={role}
              csrf={csrf}
            />
          ) : (
            <p>请选择查看角色。</p>
          ))}
        {tab === "accounts" &&
          (role ? (
            <DetailCard title={"账号关联设置"}>
              <AccountLinkPanel
                key={`${csrf}:${role.id}:${person.qqId}`}
                actor={role.id}
                qqId={person.qqId}
                csrf={csrf}
              />
            </DetailCard>
          ) : (
            <p>请选择要关联的角色。</p>
          ))}
        {tab === "permissions" && (
          <DetailCard title={"回复权限设置"}>
            <ReplyPermissions
              person={person}
              roles={roles}
              csrf={csrf}
              observations={observations}
              onLocked={onObservationLocked}
            />
          </DetailCard>
        )}
        {tab === "relationship" &&
          (person.profile?.person_id && role ? (
            <RelationshipPanel
              csrf={csrf}
              selection={{
                roleId: role.id,
                personId: person.profile.person_id,
              }}
            />
          ) : (
            <div className="people-empty">
              {person.profile
                ? "请选择查看角色。"
                : "该账号的人物身份尚未确认，暂不能读取或修改关系。"}
            </div>
          ))}
      </div>
    </section>
  );
}
