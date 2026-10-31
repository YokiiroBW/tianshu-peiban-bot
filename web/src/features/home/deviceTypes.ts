export type DeviceEntity = { entity_id: string; label?: string };
export type DeviceTemplate = {
  template_id: string;
  label?: string;
  entity_id: string;
  service: "turn_on" | "turn_off";
};
export type DeviceConnection = {
  id: string;
  revision: number;
  base_url: string;
  entities: DeviceEntity[];
  templates: DeviceTemplate[];
  enabled: boolean;
  configured: boolean;
  state: string;
  reason: string | null;
  timeout_seconds: number;
  status_max_age_seconds: number;
};
export type DeviceReading = {
  connection_id: string;
  entity_id: string;
  label?: string;
  revision: number;
  state: string | null;
  attributes: {
    unit_of_measurement?: string;
    friendly_name?: string;
    brightness?: number;
    color_temp_kelvin?: number;
  };
  observed_at: string;
  code: string;
  connection_revision: number;
};
export type DevicesView = {
  connections: DeviceConnection[];
  readings: DeviceReading[];
  control_capability: "device.control";
  can_manage: boolean;
  can_control: boolean;
};
