(() => {
  let port;
  let sequence = 0;
  const pending = new Map();
  const applyTheme = (context) => {
    for (const [name, value] of Object.entries(context.theme_tokens || {})) {
      if (name.startsWith("--color-"))
        document.documentElement.style.setProperty(name, value);
    }
    document.documentElement.dataset.theme = context.theme;
  };
  window.tianshu = {
    call: (method, input = {}) =>
      new Promise((resolve, reject) => {
        if (!port) {
          reject(new Error("页面桥尚未连接"));
          return;
        }
        const id = ++sequence;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error("页面请求超时，请核对原任务"));
        }, 15000);
        pending.set(id, { resolve, reject, timer });
        port.postMessage({ id, method, input });
      }),
  };
  addEventListener("message", (event) => {
    if (
      event.source !== parent ||
      event.data?.type !== "tianshu:init" ||
      event.data.nonce !== __TIANSHU_NONCE__ ||
      port
    )
      return;
    port = event.ports[0];
    port.onmessage = (event) => {
      const data = event.data;
      const entry = pending.get(data.id);
      if (entry) {
        clearTimeout(entry.timer);
        pending.delete(data.id);
        data.error
          ? entry.reject(new Error(data.error))
          : entry.resolve(data.result);
      } else {
        if (data.type === "themeChanged") applyTheme(data.context);
        dispatchEvent(
          new CustomEvent("tianshu:" + data.type, { detail: data }),
        );
      }
    };
    tianshu.call("getContext").then((context) => {
      applyTheme(context);
      dispatchEvent(new Event("tianshu:ready"));
    });
  });
  parent.postMessage({ type: "tianshu:ready", nonce: __TIANSHU_NONCE__ }, "*");
})();
