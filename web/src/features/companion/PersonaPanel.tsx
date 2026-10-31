import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { LoginLink, useAuth } from "../../app/Auth";
import PersonaAuthor from "./PersonaAuthor";
import "./personas.css";

export default function PersonaPanel() {
  const { session, loading, error, refresh } = useAuth();
  const workspace = useRoleWorkspace();

  return (
    <section className="persona-page glass scroll-page" aria-label="人格管理">
      <header className="persona-page-heading">
        <h2>
          {workspace.roles.find((role) => role.id === workspace.actor)?.name ??
            "角色"}{" "}
          · 人格与世界
        </h2>
      </header>
      {loading ? (
        <p role="status">正在读取人格…</p>
      ) : error ? (
        <div role="alert">
          <p>{error}</p>
          <button className="button" onClick={() => void refresh()}>
            重新连接
          </button>
        </div>
      ) : session?.authenticated ? (
        <PersonaAuthor
          key={workspace.actor}
          actor={workspace.actor}
          session={session}
        />
      ) : (
        <LoginLink />
      )}
    </section>
  );
}
