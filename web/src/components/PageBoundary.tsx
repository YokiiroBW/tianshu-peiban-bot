import { Component } from "react";
import type { ReactNode } from "react";
import { StatePanel } from "./StatePanel";

export class PageBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <StatePanel kind="error" title="页面未能加载">
          <p>请检查网络后重新加载。也可以从导航前往其他工作区。</p>
          <button className="button" onClick={() => window.location.reload()}>
            重新加载
          </button>
        </StatePanel>
      );
    return this.props.children;
  }
}
