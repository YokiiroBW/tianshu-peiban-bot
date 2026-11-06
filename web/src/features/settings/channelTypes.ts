export type RegisteredConnection = {
  connection_id: string;
  actor_id: string;
  namespace: string;
  self_external_id: string;
  enabled: boolean;
  revision: number;
  event_permissions: string[];
  default_mode: "reply" | "observe" | "ignore";
};
export type ConversationLink = {
  actor_id: string;
  kind: string;
  label: string;
  target_ref: string;
  reply_keywords: string[];
  inherited_private_policy: boolean;
  connection_id: string;
  external_id: string;
  conversation_id: string;
  mode: "reply" | "observe" | "ignore";
  mention_required: boolean;
  reply_owner: string;
  revision: number;
};
export type IdentityDirectory = {
  connections: RegisteredConnection[];
  conversation_links: ConversationLink[];
};
export type SupportItem = {
  name?: string;
  type?: string;
  variant?: string;
  receive?: boolean;
  send?: boolean;
  implemented: boolean;
  provider_supported: boolean | null;
  verified_at: string | null;
  effect?: string;
  required?: string[];
  standard?: boolean;
  public?: boolean;
};
export type ChannelMatrix = {
  standard: string;
  spec_url?: string;
  apis: SupportItem[];
  segments?: SupportItem[];
};
export type ChannelConnection = {
  id: string;
  revision: number;
  adapter: string;
  transport: string;
  provider: string;
  enabled: boolean;
  configured: boolean;
  state: string;
  reason?: string;
  actor_id: string;
  self_external_id: string;
  namespace: string;
  endpoint?: string;
  api_endpoint?: string;
  token_ref?: string;
  extension_apis?: string[];
  api_support?: Record<string, boolean>;
  segment_support?: Record<string, boolean>;
  media_path_mappings?: { local_root: string; remote_root: string }[];
  allowed_media_hosts?: string[];
  max_media_bytes?: number;
  max_inline_bytes?: number;
  allow_at_all?: boolean;
  bridge_instance_id?: string;
  webhook_public_url?: string;
  webhook_secret_ref?: string;
  external_commands?: string[];
  matrix: ChannelMatrix;
};
export type ChannelView = {
  connections: ChannelConnection[];
  matrix: ChannelMatrix;
  owner: {
    owner_id: string;
    epoch: number;
    expires_at: string;
    active: boolean;
  } | null;
  adapters: {
    id: string;
    implemented: boolean;
    transports?: string[];
    transport?: string;
    sdk_version?: string;
  }[];
  deliveries: {
    expression_id: string;
    action_id: string;
    conversation_id: string;
  }[];
  observations: {
    connection_id: string;
    kind: string;
    name: string;
    body: Record<string, unknown>;
    observed_at: string;
  }[];
};
export const adapterLabels: Record<string, string> = {
  onebot: "OneBot / NapCat / SnowLuma",
  telegram: "Telegram",
  nonebot: "NoneBot",
  astrbot: "AstrBot",
};
export const transportLabels: Record<string, string> = {
  http: "正向 HTTP",
  reverse_http: "反向 HTTP",
  ws: "正向 WebSocket",
  reverse_ws: "反向 WebSocket",
  telegram_webhook: "Webhook 推送",
  telegram_poll: "主动轮询",
  bridge: "宿主插件桥接",
};
export const providerLabels: Record<string, string> = {
  generic: "通用 OneBot 服务",
  napcat: "NapCat",
  snowluma: "SnowLuma",
  telegram: "Telegram",
  nonebot: "NoneBot",
  astrbot: "AstrBot",
};
export const splitLines = (value: string) => [
  ...new Set(
    value
      .split(/[\n,，]+/)
      .map((x) => x.trim())
      .filter(Boolean),
  ),
];
