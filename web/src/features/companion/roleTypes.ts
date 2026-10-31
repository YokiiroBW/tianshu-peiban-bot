export type Actor = {
  actor_id: string;
  name: string;
  enabled: boolean;
  revision: number;
  persona_id: string | null;
  persona_revision: number | null;
  can_manage?: boolean;
};
export type PersonaIndex = {
  persona_id: string;
  name: string;
  revision: number;
  current_revision: number;
};
export type Persona = {
  persona_id: string;
  name: string;
  revision: number;
  author_text: string;
  tone: string;
  style: string;
  address: string;
  world: string;
};
export type PersonaCatalog = { items: PersonaIndex[]; can_manage: boolean };
