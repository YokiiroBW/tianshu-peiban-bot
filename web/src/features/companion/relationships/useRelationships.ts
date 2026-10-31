import { useEffect, useRef, useState } from "react";
import {
  call,
  RelationshipError,
  type Catalog,
  type Command,
  type Person,
  type Projection,
  type Selection,
  type View,
} from "./api";

export type RelationshipTarget = { roleId: string; personId: string };

export function useRelationships(csrf: string, target?: RelationshipTarget) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [roleId, setRoleId] = useState("");
  const [personId, setPersonId] = useState("");
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const active = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const role = catalog?.roles.find((item) => item.id === roleId);
  const person = catalog?.items.find((item) => item.id === personId);
  const selection: Selection | null =
    role && person
      ? {
          role_id: role.id,
          role_version: role.version,
          person_id: person.id,
          people_after: person.after,
        }
      : null;
  const selected = useRef("");
  selected.current = JSON.stringify(selection);

  function begin() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    return { controller, serial: ++generation.current };
  }
  function current(serial: number, controller: AbortController) {
    return serial === generation.current && !controller.signal.aborted;
  }
  function failed(cause: unknown) {
    setError(
      cause instanceof Error ? cause.message : "请求未完成，请刷新核对。",
    );
    if (
      cause instanceof RelationshipError &&
      [
        "unauthorized",
        "session_expired",
        "forbidden",
        "scope_changed",
      ].includes(cause.code)
    )
      setView(null);
  }
  async function loadCatalog() {
    const { controller, serial } = begin();
    setBusy(true);
    setView(null);
    setPersonId("");
    setError("");
    setNotice("");
    try {
      const result = await call<Catalog>(
        "catalog",
        {},
        csrf,
        controller.signal,
      );
      if (!current(serial, controller)) return;
      // The user directory and relationship catalog paginate independently. Resolve
      // the selected identity using the existing catalog instead of guessing a cursor.
      while (
        target &&
        !result.items.some((item) => item.id === target.personId) &&
        result.next_after
      ) {
        const page = await call<{ items: Person[]; next_after: string | null }>(
          "people",
          { after: result.next_after },
          csrf,
          controller.signal,
        );
        if (!current(serial, controller)) return;
        result.items.push(...page.items);
        result.next_after = page.next_after;
      }
      setCatalog(result);
      if (target) {
        const chosenRole = result.roles.find(
          (item) => item.id === target.roleId,
        );
        const chosenPerson = result.items.find(
          (item) => item.id === target.personId,
        );
        setRoleId(chosenRole?.available ? chosenRole.id : "");
        setPersonId(chosenPerson?.id ?? "");
        if (!chosenRole?.available || !chosenPerson)
          setError("当前人物或角色暂不可管理关系，请刷新核对。");
      } else {
        setRoleId(result.roles.find((item) => item.available)?.id ?? "");
      }
    } catch (cause) {
      if (current(serial, controller)) {
        setCatalog(null);
        failed(cause);
      }
    } finally {
      if (current(serial, controller)) setBusy(false);
    }
  }
  async function refresh() {
    if (!selection) return;
    const key = JSON.stringify(selection);
    const { controller, serial } = begin();
    setBusy(true);
    setView(null);
    setError("");
    setNotice("");
    try {
      const result = await call<View>(
        "view",
        selection,
        csrf,
        controller.signal,
      );
      if (current(serial, controller) && selected.current === key) {
        setView(result);
        setUncertain(false);
      }
    } catch (cause) {
      if (current(serial, controller)) failed(cause);
    } finally {
      if (current(serial, controller)) setBusy(false);
    }
  }
  function chooseRole(id: string) {
    begin();
    setRoleId(id);
    setPersonId("");
    setView(null);
    setError("");
    setNotice("");
    setUncertain(false);
    setBusy(false);
    setSaving(false);
    inFlight.current = false;
  }
  function choosePerson(id: string) {
    begin();
    setPersonId(id);
    setView(null);
    setError("");
    setNotice("");
    setUncertain(false);
    setBusy(false);
    setSaving(false);
    inFlight.current = false;
  }
  async function morePeople() {
    if (!catalog?.next_after || busy || saving) return;
    const { controller, serial } = begin();
    setBusy(true);
    try {
      const page = await call<{ items: Person[]; next_after: string | null }>(
        "people",
        { after: catalog.next_after },
        csrf,
        controller.signal,
      );
      if (current(serial, controller))
        setCatalog({
          ...catalog,
          items: [
            ...catalog.items,
            ...page.items.filter(
              (item) => !catalog.items.some((old) => old.id === item.id),
            ),
          ],
          next_after: page.next_after,
        });
    } catch (cause) {
      if (current(serial, controller)) failed(cause);
    } finally {
      if (current(serial, controller)) setBusy(false);
    }
  }
  async function save(command: Command) {
    if (!selection || !view || inFlight.current || uncertain) return;
    inFlight.current = true;
    const key = JSON.stringify(selection);
    const { controller, serial } = begin();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await call<{ projection: Projection }>(
        "manage",
        {
          ...selection,
          client_request_id: crypto.randomUUID(),
          command: { ...command, expected_revision: view.projection.version },
        },
        csrf,
        controller.signal,
      );
      if (!current(serial, controller) || selected.current !== key) return;
      setView({ projection: result.projection, items: [], has_more: false });
      setNotice("已保存。新的对话将读取当前关系；不会改变实际权限。");
      try {
        const history = await call<View>(
          "view",
          selection,
          csrf,
          controller.signal,
        );
        if (current(serial, controller) && selected.current === key)
          setView(history);
      } catch (cause) {
        if (current(serial, controller)) {
          setNotice("已保存，历史暂未刷新。请刷新核对。");
          failed(cause);
        }
      }
    } catch (cause) {
      if (current(serial, controller)) {
        failed(cause);
        setUncertain(true);
      }
    } finally {
      if (current(serial, controller)) {
        setSaving(false);
        inFlight.current = false;
      }
    }
  }
  function cancelWaiting() {
    begin();
    setSaving(false);
    setBusy(false);
    inFlight.current = false;
    setView(null);
    setUncertain(true);
    setNotice("已取消等待。操作可能已提交，请刷新核对。");
  }
  useEffect(() => {
    void loadCatalog();
    return () => {
      active.current?.abort();
      ++generation.current;
    };
  }, [csrf]);
  useEffect(() => {
    if (selection) void refresh();
  }, [roleId, personId]);
  const displayed =
    view &&
    selection &&
    view.projection.pair.actor_id === selection.role_id &&
    view.projection.pair.person_id === selection.person_id
      ? view
      : null;
  return {
    catalog,
    roleId,
    personId,
    view: displayed,
    busy,
    saving,
    error,
    notice,
    uncertain,
    chooseRole,
    choosePerson,
    refresh,
    loadCatalog,
    morePeople,
    save,
    cancelWaiting,
  };
}
