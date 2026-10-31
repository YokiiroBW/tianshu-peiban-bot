// Types generated from the coordinator-published Life v2 schema. Server derives identity and scope.
export type source_ref = {
  owner: "companion" | "memory" | "platform" | "assetlibrary";
  object_id: string;
  version: number;
};
export type content_ref = {
  owner: "companion" | "memory" | "assetlibrary";
  object_id: string;
  version: number;
  kind: "image" | "text" | "article" | "video" | "audio";
  sha256: string;
  sources: source_ref[];
  coverage: {
    unit: "bytes" | "characters" | "seconds" | "pages";
    start: number;
    end: number;
    total: number | null;
  };
};
export type range = {
  unit: "bytes" | "characters" | "seconds" | "pages";
  start: number;
  end: number;
};
export type read_request = {
  schema_version: 2;
  query: Record<string, unknown>;
  actor_id: string;
  resource:
    | "state"
    | "autonomy"
    | "activities"
    | "open_work"
    | "affect"
    | "outfits"
    | "album"
    | "media"
    | "works"
    | "chapter"
    | "proactive"
    | "reading"
    | "image_backend"
    | "image_jobs"
    | "image_reference";
  object_id: string | null;
  expected_revision: number | null;
  limit: number;
  after: string | null;
  scope: Record<string, unknown> | null;
  chapter_view?: "published" | "current";
};
export type content_read_response = {
  schema_version: 2;
  request_id: string;
  actor_id: string;
  reading_id: string | null;
  content_ref: content_ref;
  text: string | null;
  media_url: string | null;
  coverage: range;
  complete: boolean;
  representations: content_representation[];
  gaps: content_gap[];
};
export type operation_result = {
  id: string;
  version: number;
  state: string;
  operation_ref: string;
  content_ref?: content_ref;
  next_cursor?: string | null;
  imported?: number;
  skipped?: number;
  complete?: boolean;
};
export type manage_response = {
  schema_version: 2;
  request_id: string;
  actor_id: string;
  operation:
    | "concern.save"
    | "actor-memory.import"
    | "concern.close"
    | "activity.save"
    | "activity.pause"
    | "activity.resume"
    | "activity.cancel"
    | "affect.feedback"
    | "outfit.put"
    | "outfit.select"
    | "image.request"
    | "image.cancel"
    | "album.attach"
    | "album.remove"
    | "diary.request"
    | "diary.revise"
    | "diary.publish"
    | "writing.create"
    | "chapter.add"
    | "chapter.generate"
    | "chapter.revise"
    | "chapter.review"
    | "chapter.publish"
    | "proactive.subscription"
    | "proactive.motive"
    | "proactive.cancel"
    | "content.acquire"
    | "reading.open"
    | "reading.read"
    | "reading.pause"
    | "reading.resume"
    | "reading.close"
    | "image.backend.configure"
    | "proactive.subscription.state"
    | "actor.image-reference.configure";
  result: operation_result;
};
export type content_representation = {
  kind: "image" | "video_frame" | "audio_clip";
  media_type: string;
  sha256: string;
  source_sha256: string;
  data_base64: string;
  at_seconds: number | null;
};
export type content_gap =
  | "frames_sampled"
  | "audio_not_transcribed"
  | "image_resized"
  | "page_images_not_extracted"
  | "no_text_layer"
  | "range_truncated";
export type control_read_response = {
  schema_version: 2;
  request_id: string;
  actor_id: string;
  person_id: string;
  channel: Record<string, unknown>;
  subscriptions: {
    timezone_name: string;
    quiet_start: string;
    quiet_end: string;
    cooldown_seconds: number;
    daily_quota: number;
    unanswered_limit: number;
    expiry_seconds: number;
    consent_basis: "explicit_user_request" | "explicit_admin_registration";
    id: string;
    version: number;
    state: "active" | "paused" | "revoked";
  }[];
  deliveries: {
    id: string;
    subscription_id: string;
    state: string;
    created_at: number | null;
    updated_at: number | null;
  }[];
};
export type state_record = {
  actor_id: string;
  actor_version: number;
  activity: string | null;
  activity_id: string | null;
  timezone: string;
  mood: string | null;
  outfit_ref: string | null;
  plan_id: string | null;
  changed_at: number | null;
  fictional: true;
};
export type activities_record = {
  id: string;
  version: number;
  title: string;
  state: "planned" | "running" | "paused" | "dormant" | "completed" | "cancelled";
  checkpoint: {
    step: number;
    position: number;
    unit: "step" | "characters" | "seconds" | "pages";
    note: string;
  };
  next_due_at: number | null;
  resume_condition: string | null;
  dormant_since?: number;
  sources: source_ref[];
  result_refs: content_ref[];
  scope: Record<string, unknown> | null;
  actor_id: string;
  started_at: number | null;
  updated_at: number | null;
};
export type open_work_record = {
  id: string;
  version: number;
  title: string;
  goal: string;
  state: "open" | "paused" | "completed" | "cancelled";
  scope: Record<string, unknown> | null;
  sources: source_ref[];
  fragments: {
    id: string;
    text: string;
    sources: source_ref[];
    expires_at: number | null;
    certainty: "observed" | "reported" | "inferred" | "uncertain";
    version: number;
    valid: boolean;
  }[];
  next_due_at: number | null;
  expires_at: number | null;
  result_refs: content_ref[];
  actor_id: string;
  intent_valid: boolean;
  updated_at: number | null;
};
export type affect_record = {
  actor_id: string;
  valence: number;
  feelings: {
    id: string;
    kind: "warm" | "neutral" | "dislike" | "distress" | "recovery";
    reason: string;
    intensity: number;
    sources: source_ref[];
  }[];
  observed_at: number | null;
  relationship_effect: "none";
};
export type outfits_record = {
  id: string;
  version: number;
  description: string;
  prompt: string;
  reference: string | null | (content_ref & { kind?: "image" });
  activities: string[];
  source_scope?: Record<string, unknown> | null;
};
export type album_record = {
  id: string;
  version: number;
  actor_id: string;
  media_id: string;
  activity_id: string | null;
  caption: string;
  scope: Record<string, unknown> | null;
  scene_at: number | null;
  completed_at: number | null;
  state: "available" | "unavailable" | "removed";
  content_ref: content_ref;
};
export type media_record = {
  id: string;
  version: number;
  actor_id: string;
  state: "available" | "unavailable" | "removed";
  media_type: string;
  size: number;
  content_ref: content_ref;
};
export type works_record = {
  id: string;
  version: number;
  actor_id: string;
  title: string;
  outline: string;
  state: string;
  characters: { name: string; description: string }[];
  chapter_ids: string[];
};
export type chapter_record = {
  id: string;
  version: number;
  work_id: string;
  title: string;
  goal: string;
  state: string;
  revision_id: string | null;
  content: string | null;
};
export type proactive_record = {
  id: string;
  version: number;
  actor_id: string;
  person_id: string;
  audience: "self_private" | "group";
  conversation_id: string;
  state: string;
  summary: string;
  due_at: number | null;
  expires_at: number | null;
  delivered: boolean;
  content_refs: content_ref[];
};
export type reading_record = {
  id: string;
  version: number;
  actor_id: string;
  scope: Record<string, unknown> | null;
  content_ref: content_ref;
  state: "open" | "reading" | "paused" | "completed" | "closed" | "unavailable";
  mode: "solo" | "together";
  participants: string[];
  coverage: range[];
  position: range;
  updated_at: number | null;
};
export type image_backend_record = {
  id: string;
  version: number;
  state: "not_configured" | "configured" | "disabled" | "unreachable";
  base_url: string | null;
  credential_ref: string | null;
  profile: "standard_sd" | null;
  checkpoint: string | null;
  models: string[];
  checked_at: number | null;
  error_code: string | null;
};
export type image_jobs_record = {
  id: string;
  version: number;
  actor_id: string;
  state:
    | "queued"
    | "running"
    | "completed"
    | "failed"
    | "unknown"
    | "cancelled"
    | "unavailable";
  error_code: string | null;
  created_at: number | null;
  completed_at: number | null;
  artifacts: { media_id: string; content_ref: content_ref }[];
};
export type image_reference_record = {
  id: string;
  version: number;
  actor_id: string;
  content_ref: (content_ref & { kind?: "image" }) | null;
  scope: Record<string, unknown> | null;
  state: "configured" | "not_configured";
};
export type Resource = read_request["resource"];
export type Records = {
  autonomy: {
    id: string;
    actor_id: string;
    version: number;
    enabled: boolean;
    goal_id: string | null;
    title: string | null;
    reason: string | null;
    state: string;
    next_due_at: number | null;
    updated_at: number | null;
    current_step: activities_record["checkpoint"] | null;
    step_state: string | null;
    error_code: string | null;
    sources: source_ref[];
  };
  state: state_record;
  activities: activities_record;
  open_work: open_work_record;
  affect: affect_record;
  outfits: outfits_record;
  album: album_record;
  media: media_record;
  works: works_record;
  chapter: chapter_record;
  proactive: proactive_record;
  reading: reading_record;
  image_backend: image_backend_record;
  image_jobs: image_jobs_record;
  image_reference: image_reference_record;
};
export type ReadResult<T> = {
  items: T[];
  next_cursor: string | null;
  actor_id: string;
  resource: Resource;
};
