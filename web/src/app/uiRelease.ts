import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const UI_BUILD_ID = __TIANSHU_UI_BUILD_ID__;

if (typeof document !== "undefined") {
  document.documentElement.dataset.uiBuildId = UI_BUILD_ID;
}

export function publicAssetUrl(relativePath: string, baseUrl: string = import.meta.env.BASE_URL || '/'): string {
  const cleanBase = baseUrl.endsWith('/') ? baseUrl : baseUrl + '/';
  const cleanPath = relativePath.startsWith('/') ? relativePath.slice(1) : relativePath;
  return cleanBase + cleanPath;
}

export function UiVersionMonitor() {
  const [notice, setNotice] = useState<ReactNode | null>(null);

  useEffect(() => {
    let timer: number;
    const check = async () => {
      try {
        const response = await fetch('/_ui/version', { cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        if (
          data.schema_version === 'tianshu.ui.status.v1' &&
          data.compatible !== false &&
          typeof data.served_build_id === 'string' &&
          data.served_build_id !== UI_BUILD_ID &&
          (UI_BUILD_ID !== 'dev-local' || data.served_mode === 'external' || !data.served_build_id.startsWith('builtin-'))
        ) {
          setNotice(
            <div className="notification-card" role="status">
              <p>检测到新版本 UI ({data.served_build_id})。</p>
              <div className="notification-actions">
                <button type="button" className="text-link" onClick={() => window.location.reload()}>刷新加载新版本</button>
                <button type="button" className="text-link" onClick={() => setNotice(null)}>稍后</button>
              </div>
            </div>
          );
        }
      } catch {
        // Silently ignore
      }
    };

    const run = () => {
      check();
      timer = window.setTimeout(run, 60000);
    };

    window.addEventListener('focus', check);
    window.addEventListener('online', check);
    run();

    return () => {
      window.removeEventListener('focus', check);
      window.removeEventListener('online', check);
      clearTimeout(timer);
    };
  }, []);

  return notice ? createPortal(notice, document.body) : null;
}
