// Synthetic empty group; no chat or profile has been collected.
export function group(id = "20001") {
  return {
    id: `directory-${id}`,
    owner_id: "obs:directory",
    owner_name: "测试 BOT",
    account_id: "90001",
    group_id: id,
    name: id === "20001" ? "随手聊天" : `合成群 ${id}`,
    member_count: null,
    max_member_count: null,
    first_seen_at: 1792418400,
    last_seen_at: 1792504799,
    revision: 1,
    rule: {
      mode: "disabled",
      actor_id: "actor:a" as string | null,
      keywords: [] as string[],
    },
    pending: false,
    read_enabled: true,
    has_archive: false,
    state: "ready",
    ordinary_claim_available: false,
    memory_group_id: null as string | null,
  };
}
