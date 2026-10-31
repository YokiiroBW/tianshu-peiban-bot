import type { Module } from "../app/modules";
import { StatePanel } from "./StatePanel";

export function UnavailablePage({
  module,
  section,
}: {
  module: Module;
  section: number;
}) {
  const info: Record<string, string> = {
    "memory/2": "账号关联目前没有面向浏览器的读取或修改流程。",
    "home/1":
      "容器清单与实时健康观测的浏览器接口尚未提供。连接摘要只报告已登记业务能力的读取状态。",
    "home/2": "节点与游戏服管理的浏览器流程尚未提供。",
    "home/3": "身体与活动数据的浏览器流程尚未提供。",
  };
  const key = `${module.id}/${section}`;
  return (
    <StatePanel
      kind="unconfigured"
      title={`${module.sections[section] ?? module.label}尚未提供网页功能`}
      action={
        <a className="button" href="#/workbench">
          返回工作台
        </a>
      }
    >
      <p>{info[key] ?? "当前没有这项功能的网页接口。"}</p>
    </StatePanel>
  );
}
