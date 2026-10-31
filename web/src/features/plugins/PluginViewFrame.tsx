import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import { pluginCall } from "./api";
import { readFailure } from "../../app/integrationApi";
import type { Instance, Invocation, PluginView } from "./types";

export function PluginViewFrame({
  instance,
  view,
  csrf,
  bindingId,
}: {
  instance: Instance;
  view: PluginView;
  csrf: string;
  bindingId: string;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const active = new AbortController();
    const nonce = requestId();
    let port: MessagePort | undefined;
    const subscriptions = new Map<string, number>();
    const context = () => ({
      instance_id: instance.id,
      page_id: view.id,
      theme: document.documentElement.dataset.theme ?? "light",
      theme_tokens: Object.fromEntries(
        [
          "--color-surface",
          "--color-text",
          "--color-muted",
          "--color-line",
          "--color-input",
          "--color-accent",
        ].map((name) => [
          name,
          getComputedStyle(document.documentElement).getPropertyValue(name),
        ]),
      ),
      language: navigator.language,
      actions: instance.contributions.actions.filter((action) =>
        view.actions.includes(action.id),
      ),
    });
    const listener = (event: MessageEvent) => {
      if (
        event.source !== frame.current?.contentWindow ||
        event.data?.type !== "tianshu:ready" ||
        event.data?.nonce !== nonce ||
        port
      )
        return;
      const channel = new MessageChannel();
      port = channel.port1;
      port.onmessage = async (event) => {
        const message = event.data;
        try {
          let result: unknown;
          if (message.method === "getContext") result = context();
          else if (message.method === "callAction") {
            if (!view.actions.includes(message.input?.action))
              throw new Error("此页面没有该动作。");
            result = await pluginCall(
              "invoke",
              {
                instance_id: instance.id,
                action: message.input.action,
                input: message.input.input ?? {},
                client_request_id:
                  message.input.client_request_id ?? requestId(),
                ...(bindingId ? { binding_id: bindingId } : {}),
              },
              csrf,
              active.signal,
            );
          } else if (
            ["getInvocation", "subscribeInvocation"].includes(message.method)
          ) {
            const id = String(message.input?.id ?? "");
            const read = async () => {
              const receipt = await pluginCall<Invocation>(
                "invocation",
                { id },
                csrf,
                active.signal,
              );
              if (receipt.instance_id !== instance.id)
                throw new Error("任务不属于此实例。");
              return receipt;
            };
            result = await read();
            if (
              message.method === "subscribeInvocation" &&
              !subscriptions.has(id)
            ) {
              subscriptions.set(
                id,
                window.setInterval(() => {
                  if (!document.hidden)
                    void read()
                      .then((receipt) =>
                        port?.postMessage({ type: "invocation", id, receipt }),
                      )
                      .catch(() => undefined);
                }, 2000),
              );
            }
          } else if (message.method === "selectFile")
            throw new Error("文件能力尚未接入此宿主。");
          else throw new Error("页面请求不受支持。");
          port?.postMessage({ id: message.id, result });
        } catch (cause) {
          port?.postMessage({ id: message.id, error: readFailure(cause) });
        }
      };
      frame.current?.contentWindow?.postMessage(
        { type: "tianshu:init", nonce },
        "*",
        [channel.port2],
      );
    };
    window.addEventListener("message", listener);
    const observer = new MutationObserver(() =>
      port?.postMessage({ type: "themeChanged", context: context() }),
    );
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    void pluginCall<{ url: string }>(
      "view-content",
      { instance_id: instance.id, page_id: view.id, nonce },
      csrf,
      active.signal,
    )
      .then((result) => setUrl(result.url))
      .catch((cause) => {
        if (!active.signal.aborted) setError(readFailure(cause));
      });
    return () => {
      active.abort();
      port?.close();
      observer.disconnect();
      subscriptions.forEach(clearInterval);
      window.removeEventListener("message", listener);
    };
  }, [instance.id, instance.revision, view.id, csrf, bindingId]);
  return (
    <div>
      {error && <p role="alert">{error}</p>}
      <iframe
        ref={frame}
        className="plugins-view"
        title={view.title}
        sandbox="allow-scripts allow-forms"
        referrerPolicy="no-referrer"
        src={url || undefined}
      />
    </div>
  );
}
