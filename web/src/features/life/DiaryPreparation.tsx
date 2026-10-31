import { useEffect, useState } from "react";
import { RuntimeFeedback } from "./RuntimeFeedback";
import { useLifeAction } from "./useLifeRuntime";
import type { LifeAccess } from "./runtimeApi";

export function DiaryPreparation({
  access,
  today,
}: {
  access: LifeAccess;
  today: string;
}) {
  const [day, setDay] = useState(today);
  const action = useLifeAction(access, () => {});
  useEffect(() => setDay(today), [access.actor, today]);
  return (
    <section className="panel">
      <h3>准备日记草稿</h3>
      <p className="muted">按这一天的实际素材准备，草稿不会自动发布。</p>
      <form
        className="life-form"
        onSubmit={(event) => {
          event.preventDefault();
          void action.run("diary.request", { day });
        }}
      >
        <label>
          日记日期
          <input
            type="date"
            required
            value={day}
            onChange={(event) => setDay(event.target.value)}
          />
        </label>
        <button className="button" disabled={action.busy || !day}>
          准备日记草稿
        </button>
      </form>
      <RuntimeFeedback
        busy={action.busy}
        error={action.error}
        receipt={action.receipt}
      />
    </section>
  );
}
