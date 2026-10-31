import { useEffect, useRef, useState } from "react";
import {
  integrationPost,
  IntegrationError,
  readFailure,
  writeFailure,
} from "../../app/integrationApi";
import { requestId } from "../../app/requestId";
import { ActorMemoryRecord, type MemoryCorrection } from "./ActorMemoryRecord";
import { InterestEvidence, memoryLabel } from "./ActorMemoryEvidence";
import type {
  Episode,
  Interests,
  ManagedMemory,
  MemoryImpact,
  MemoryList,
} from "./actorMemoryTypes";
import type { LifeAccess } from "./runtimeApi";
import { ActorMemoryImport } from "./ActorMemoryImport";

const kinds = [
  "episode",
  "understanding",
  "preference",
  "reflection",
  "autobiography",
];
const states = ["active", "superseded", "expired", "withdrawn", "unavailable"];
const key = (item: ManagedMemory) =>
  `${item.ref.kind}:${item.ref.id}:${item.ref.revision}`;

export function ActorMemoryPanel({ access }: { access: LifeAccess }) {
  const [kind, setKind] = useState("all"),
    [state, setState] = useState("active");
  const [page, setPage] = useState<MemoryList | null>(null),
    [interests, setInterests] = useState<Interests | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [impact, setImpact] = useState<MemoryImpact | null>(null),
    [episode, setEpisode] = useState<Episode | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [epoch, setEpoch] = useState(0),
    [visible, setVisible] = useState(!document.hidden);
  const active = useRef<AbortController | null>(null);
  const reading = useRef<AbortController | null>(null);
  const pending = useRef<{ semantic: string; id: string } | null>(null);
  function call<T>(name: string, value: object, signal: AbortSignal) {
    return integrationPost<T>(
      `actor-memory/${name}`,
      { actor_id: access.actor, ...value },
      access.csrf,
      signal,
    );
  }
  useEffect(() => {
    function visibility() {
      setVisible(!document.hidden);
      if (document.hidden) {
        reading.current?.abort();
        active.current?.abort();
      }
    }
    document.addEventListener("visibilitychange", visibility);
    return () => {
      document.removeEventListener("visibilitychange", visibility);
      active.current?.abort();
      reading.current?.abort();
    };
  }, []);
  useEffect(() => {
    const control = new AbortController();
    reading.current?.abort();
    active.current?.abort();
    reading.current = control;
    setPage(null);
    setInterests(null);
    setSelected(null);
    setImpact(null);
    setEpisode(null);
    setError("");
    if (!visible) {
      setBusy(false);
      return;
    }
    setBusy(true);
    void Promise.all([
      call<MemoryList>(
        "list",
        {
          states: state === "all" ? states : [state],
          kinds: kind === "all" ? kinds : [kind],
          cursor: null,
        },
        control.signal,
      ),
      call<Interests>("interests", {}, control.signal),
    ])
      .then(([rows, changes]) => {
        if (!control.signal.aborted) {
          setPage(rows);
          setInterests(changes);
        }
      })
      .catch((cause) => {
        if (!control.signal.aborted) setError(readFailure(cause));
      })
      .finally(() => {
        if (!control.signal.aborted) setBusy(false);
      });
    return () => control.abort();
  }, [access.actor, access.csrf, kind, state, epoch, visible]);
  async function inspect(item: ManagedMemory) {
    active.current?.abort();
    const control = new AbortController();
    active.current = control;
    setSelected(key(item));
    setImpact(null);
    setEpisode(null);
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const result = await call<MemoryImpact>(
        "impact",
        { ref: item.ref },
        control.signal,
      );
      if (control.signal.aborted) return;
      setImpact(result);
      if (item.ref.kind === "episode" && item.state === "active") {
        const detail = await call<Episode>(
          "episode",
          { ref: item.ref },
          control.signal,
        );
        if (!control.signal.aborted) setEpisode(detail);
      }
    } catch (cause) {
      if (!control.signal.aborted) {
        setImpact(null);
        setEpisode(null);
        setPage(null);
        setInterests(null);
        setError(readFailure(cause));
      }
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  async function write(
    item: ManagedMemory,
    name: "revise" | "forget",
    value?: MemoryCorrection,
  ) {
    if (busy || !impact) return;
    const body = {
      ref: item.ref,
      expected_domain_version: impact.domain_version,
      ...(value ? { value } : {}),
    };
    const semantic = JSON.stringify([access.actor, name, body]);
    const id =
      pending.current?.semantic === semantic ? pending.current.id : requestId();
    pending.current = { semantic, id };
    const control = new AbortController();
    active.current = control;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const receipt = await call<{ state: string }>(
        name,
        { ...body, client_request_id: id },
        control.signal,
      );
      if (control.signal.aborted) return;
      pending.current = null;
      setNotice(
        name === "forget" && receipt.state === "withdrawn"
          ? "记忆已忘记；正在刷新当前角色记录。"
          : name === "revise" &&
              ["accepted", "duplicate"].includes(receipt.state)
            ? "更正已保存；原记录保留为替代历史。"
            : "操作已记录，正在读取当前状态。",
      );
      setEpoch((old) => old + 1);
    } catch (cause) {
      if (!control.signal.aborted) {
        if (
          cause instanceof IntegrationError &&
          [401, 403, 404, 409].includes(cause.status)
        ) {
          setPage(null);
          setInterests(null);
          setImpact(null);
          setEpisode(null);
        }
        setError(writeFailure(cause));
      }
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  async function more() {
    if (!page?.next_cursor || busy) return;
    const control = new AbortController();
    reading.current = control;
    setBusy(true);
    setError("");
    try {
      const result = await call<MemoryList>(
        "list",
        {
          states: state === "all" ? states : [state],
          kinds: kind === "all" ? kinds : [kind],
          cursor: page.next_cursor,
        },
        control.signal,
      );
      if (!control.signal.aborted)
        setPage({ ...result, items: [...page.items, ...result.items] });
    } catch (cause) {
      if (!control.signal.aborted) {
        setPage(null);
        setInterests(null);
        setImpact(null);
        setEpisode(null);
        setError(readFailure(cause));
      }
    } finally {
      if (!control.signal.aborted) setBusy(false);
    }
  }
  return (
    <div className="life-runtime-grid">
      <section className="panel">
        <h3>角色自身记忆</h3>
        <p className="muted">
          这里记录角色自己的经历与认识；用户事实在用户记忆页独立管理。
        </p>
        <ActorMemoryImport
          access={access}
          updated={() => setEpoch((old) => old + 1)}
        />
        <div className="life-form-row">
          <label>
            记忆类型
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              <option value="all">全部类型</option>
              {kinds.map((value) => (
                <option key={value} value={value}>
                  {memoryLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <label>
            有效状态
            <select
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="all">全部状态与历史</option>
              {states.map((value) => (
                <option key={value} value={value}>
                  {memoryLabel(value)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button
          className="button"
          disabled={busy}
          onClick={() => setEpoch((old) => old + 1)}
        >
          刷新自身记忆
        </button>
        {busy && <p role="status">正在读取角色记忆…</p>}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {page && !page.items.length && <p>当前筛选下没有可展示的记录。</p>}
        {page?.omitted && (
          <p className="muted">本次读取范围有省略，不代表完整历史。</p>
        )}
        {page?.items.map((item) => (
          <ActorMemoryRecord
            key={key(item)}
            item={item}
            busy={busy}
            impact={selected === key(item) ? impact : null}
            episode={selected === key(item) ? episode : null}
            onInspect={() => void inspect(item)}
            onCorrect={(value) => void write(item, "revise", value)}
            onForget={() => void write(item, "forget")}
          />
        ))}
        {page?.next_cursor && (
          <button
            className="button"
            disabled={busy}
            onClick={() => void more()}
          >
            继续读取记忆
          </button>
        )}
      </section>
      <section className="panel">
        <h3>长期兴趣与变化</h3>
        {interests?.items.map((item) => (
          <InterestEvidence
            key={`${item.ref.id}:${item.ref.revision}`}
            item={item}
          />
        ))}
        {interests && !interests.items.length && (
          <p>尚无当前可读的长期兴趣。</p>
        )}
        {interests?.omitted && <p>当前只展示本次预算内的兴趣。</p>}
      </section>
    </div>
  );
}
