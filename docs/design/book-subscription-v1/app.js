"use strict";

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
  );

const sources = [
  {
    id: "qinghe",
    name: "青禾书屋",
    kind: "web",
    enabled: true,
    state: "ready",
    detail: "示例规则 · 支持搜索、详情、目录与正文",
  },
  {
    id: "xinghe",
    name: "星河书源",
    kind: "web",
    enabled: true,
    state: "ready",
    detail: "示例规则 · 同书备用来源，章节数量可能不同",
  },
  {
    id: "local",
    name: "本地书库",
    kind: "local",
    enabled: true,
    state: "ready",
    detail: "示例索引 · 已整理的书籍和原文",
  },
  {
    id: "far",
    name: "远山书库",
    kind: "web",
    enabled: true,
    state: "failed",
    detail: "示例失败状态 · 最近请求超时，已保留最后可用规则",
  },
];

const books = [
  {
    id: "fog",
    title: "雾港来信",
    author: "林见山",
    status: "连载中",
    chapters: 241,
    color: "#506d77",
    tags: "悬疑 / 城市 / 慢生活",
    language: "zh-CN",
    sources: ["qinghe", "xinghe"],
    description: "一封没有署名的信，带她回到海边的小城。沿着旧邮路，重新认识那些以为早已熟悉的人。",
  },
  {
    id: "river",
    title: "山河慢行",
    author: "许知遥",
    status: "连载中",
    chapters: 124,
    color: "#7c8370",
    tags: "旅行 / 散文",
    language: "zh-CN",
    sources: ["qinghe", "local"],
    description:
      "走过山谷与旧车站，在每一次停留中记录天气、食物和偶然相逢。一本适合慢慢读的旅行手记。",
  },
  {
    id: "night",
    title: "夜航手记",
    author: "林见山",
    status: "已完结",
    chapters: 38,
    color: "#66658a",
    tags: "幻想 / 短篇",
    language: "zh-CN",
    sources: ["xinghe"],
    description: "三十八个夜晚，三十八次出发。灯塔之外，有人守着一盏灯，也有人带着未完的故事返航。",
  },
];

const subscriptions = new Map([
  ["river", { active: true, frequency: "6", downloaded: 120, total: 124, pending: false }],
]);
const artifacts = new Map([
  ["river", { chapters: 120, revision: 1, path: "/media/Books/许知遥/山河慢行 [river]/book.epub" }],
]);
const jobs = new Map();
const state = {
  page: "search",
  selectedBook: null,
  selectedChapter: 0,
  searched: false,
  toastTimer: null,
};

function notify(message) {
  clearTimeout(state.toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  state.toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 3600);
}

function closeDialog(id) {
  $("#" + id).close();
}

const pageInfo = {
  search: ["找到下一本想读的书", "搜索多个书源，先读几页，再让天枢替你收好后续章节。"],
  subscriptions: ["故事在继续，章节会收好", "查看更新、调整追更节奏，随时接着读。"],
  library: ["你的书，都留在这里", "正文、封面与资料整理在一起，保存成方便阅读的 EPUB。"],
  sources: ["管理找到书的方式", "导入阅读 3 规则，分别维护来源与规则集合。"],
  settings: ["按自己的习惯整理", "一次设置目录与资料来源，后续下载沿用。"],
};

function showPage(page) {
  state.page = page;
  document.querySelectorAll(".page").forEach((node) => {
    node.hidden = node.id !== "page-" + page;
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
    if (button.dataset.page === page) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#page-title").textContent = pageInfo[page][0];
  $("#page-description").textContent = pageInfo[page][1];
  $("#heading-action").hidden = page !== "sources" && page !== "search";
  renderAll();
}

function cover(book) {
  return `<div class="book-cover" style="--cover:${book.color}"><b>${escapeHtml(book.title)}</b><small>${escapeHtml(book.author)} · 示例</small></div>`;
}

function renderSearch() {
  const query = $("#query").value.trim().toLowerCase();
  const group = $("#source-group").value;
  const activeSources = sources.filter(
    (source) => source.enabled && (group === "all" || source.kind === group),
  );
  const readyIds = new Set(
    activeSources.filter((source) => source.state === "ready").map((source) => source.id),
  );
  const matched = books.filter(
    (book) =>
      (!query || (book.title + book.author).toLowerCase().includes(query)) &&
      book.sources.some((id) => readyIds.has(id)),
  );
  $("#results-title").textContent = state.searched ? "搜索结果" : "探索书目";
  const failed = activeSources.filter((source) => source.state === "failed").length;
  const ready = activeSources.filter((source) => source.state === "ready").length;
  $("#search-summary").textContent =
    `${matched.length} 本示例书 · ${ready} 个来源返回${failed ? ` · ${failed} 个超时` : ""}`;
  $("#empty").hidden = matched.length > 0;
  $("#results").innerHTML = matched
    .map((book) => {
      const candidates = book.sources.filter((id) => readyIds.has(id));
      const sub = subscriptions.get(book.id);
      return `<div class="book-card">${cover(book)}<div><div class="book-head"><h3>${escapeHtml(book.title)}</h3><span class="status ${book.status === "已完结" ? "good" : ""}">${book.status}</span></div><p class="book-author">${escapeHtml(book.author)} · ${escapeHtml(book.tags)}</p><p class="book-intro">${escapeHtml(book.description)}</p><div class="book-meta"><span>${book.chapters} 章</span><span>来自 ${candidates.length} 个来源</span>${book.manualMetadata ? "<span>资料已人工选择</span>" : ""}</div><div class="book-actions"><span class="muted small">${candidates.map((id) => escapeHtml(sources.find((item) => item.id === id).name)).join(" · ")}</span><div class="row"><button class="secondary" data-preview="${book.id}">预览与目录</button><button class="primary" data-subscribe="${book.id}">${sub ? "查看订阅" : "订阅下载"}</button></div></div></div></div>`;
    })
    .join("");
}

function renderSubscriptions() {
  const active = [...subscriptions.values()].filter((sub) => sub.active).length;
  $("#nav-count").textContent = subscriptions.size;
  $("#subscription-stats").innerHTML = [
    [active, "追更中的书"],
    [jobs.size, "示例待完成任务"],
    [artifacts.size, "已有 EPUB 成品"],
  ]
    .map(([count, label]) => `<div class="stat"><b>${count}</b><span>${label}</span></div>`)
    .join("");
  $("#subscriptions").innerHTML = [...subscriptions]
    .map(([id, sub]) => {
      const book = books.find((item) => item.id === id);
      const pending = jobs.has(id);
      return `<div class="subscription"><div class="row spread"><div><h3>${escapeHtml(book.title)}</h3><p class="small muted">${escapeHtml(book.author)} · ${escapeHtml(sources.find((source) => source.id === book.sources[0]).name)}</p></div><span class="status ${sub.active ? "good" : "warning"}">${!sub.active ? "已暂停追更" : pending ? "正在整理 · 示例" : "追更中"}</span></div><div class="progress"><i style="width:${Math.round((sub.downloaded / sub.total) * 100)}%"></i></div><div class="row spread muted"><span>已取 ${sub.downloaded} / ${sub.total} 章</span><span>${pending ? "等待示例任务推进" : `还有 ${sub.total - sub.downloaded} 章待更新`}</span></div><div class="controls"><label class="sr-only" for="frequency-${id}">${escapeHtml(book.title)}追更周期</label><select id="frequency-${id}" data-frequency="${id}">${[
        ["1", "每小时"],
        ["6", "每 6 小时"],
        ["24", "每天"],
      ]
        .map(
          ([value, label]) =>
            `<option value="${value}" ${sub.frequency === value ? "selected" : ""}>${label}</option>`,
        )
        .join(
          "",
        )}</select><button class="secondary" data-scan="${id}">立即检查</button><button class="secondary" data-toggle="${id}">${sub.active ? "暂停追更" : "恢复追更"}</button><button class="quiet" data-preview="${id}">查看正文</button>${pending ? `<button class="quiet" data-cancel="${id}">取消本次任务</button>` : ""}</div></div>`;
    })
    .join("");
}

function renderLibrary() {
  $("#library").innerHTML =
    [...artifacts]
      .map(([id, artifact]) => {
        const book = books.find((item) => item.id === id);
        return `<div class="book-card">${cover(book)}<div><div class="book-head"><h3>${escapeHtml(book.title)}</h3><span class="status good">EPUB · 示例成品</span></div><p class="book-author">${escapeHtml(book.author)} · ${artifact.chapters} 章 · 版本 ${artifact.revision}</p><p class="book-intro">${escapeHtml(book.description)}</p><div class="book-actions"><span class="muted small">目录、封面、资料与正文一并整理</span><div class="row"><button class="secondary" data-preview="${id}">预览正文</button><button class="primary" data-artifact="${id}">查看成品</button></div></div></div></div>`;
      })
      .join("") || '<div class="empty">完成下载与整理后，书籍会出现在这里。</div>';
}

function renderSources() {
  $("#sources").innerHTML = sources
    .map(
      (source) =>
        `<div class="source-row"><div><div class="row"><h3>${escapeHtml(source.name)}</h3><span class="status ${source.state === "failed" ? "warning" : "good"}">${!source.enabled ? "已停用" : source.state === "ready" ? "示例可用" : source.state === "pending" ? "已导入 · 尚未执行规则" : "请求超时"}</span></div><p>${escapeHtml(source.detail)}</p></div><button class="secondary" data-source-toggle="${escapeHtml(source.id)}">${source.enabled ? "停用" : "启用"}</button></div>`,
    )
    .join("");
}

function renderAll() {
  renderSearch();
  renderSubscriptions();
  renderLibrary();
  renderSources();
  $("#pending-count").textContent = `${jobs.size} 个待完成`;
  $("#advance").disabled = jobs.size === 0;
}

function openBook(id) {
  const book = books.find((item) => item.id === id);
  state.selectedBook = book;
  state.selectedChapter = 0;
  $("#detail-title").textContent = book.title;
  $("#detail-subtitle").textContent = `${book.author} · ${book.chapters} 章 · ${book.status}`;
  $("#detail-source").innerHTML = book.sources
    .map(
      (sourceId) =>
        `<option value="${sourceId}">${escapeHtml(sources.find((source) => source.id === sourceId).name)} · ${sourceId === "xinghe" && book.id === "fog" ? book.chapters - 2 : book.chapters} 章</option>`,
    )
    .join("");
  $("#chapters").innerHTML = [
    "第一章 雨停之前",
    "第二章 来自远方",
    "第三章 一盏灯",
    "第四章 明日来信",
  ]
    .map(
      (name, index) =>
        `<button class="chapter-button ${index === 0 ? "active" : ""}" data-chapter="${index}">${name}</button>`,
    )
    .join("");
  renderChapter();
  $("#book-dialog").showModal();
}

function renderChapter() {
  const names = ["第一章 雨停之前", "第二章 来自远方", "第三章 一盏灯", "第四章 明日来信"];
  const paragraphs = [
    [
      "雨停的时候，街角的书店刚刚亮起灯。她把伞靠在门外，在门口等了一会儿，听见楼上传来翻动书页的声音。",
      "柜台上放着一封信。信封没有邮票，只有一行整齐的小字：等你读到这里，我们就从这一天重新开始。",
      "她没有急着拆开。窗外的海雾正在散去，远处有人推开一扇很久没有打开的窗。",
    ],
    [
      "第二天清晨，邮差带来一本蓝色封面的旧册子。夹在书里的车票上，写着一个已经改名的站点。",
      "她沿着地图找到那条路。沿途的树长高了，店铺换过招牌，只有桥边的石阶还和记忆里一样。",
    ],
    [
      "灯塔并不总在夜里亮起。雾浓的时候，守塔人会提前开灯，好让归航的人辨认海岸。",
      "他说，每一本书也是这样的灯。读到哪里，就先照亮哪里，剩下的路可以慢慢走。",
    ],
    [
      "她在最后一页留下今天的日期，把没有写完的话夹进书中。明天翻开时，仍然能够找到这里。",
      "窗外天色已晚，码头上传来轻轻的汽笛声。",
    ],
  ];
  $("#chapter-content").innerHTML =
    `<h3>${names[state.selectedChapter]}</h3>${paragraphs[state.selectedChapter].map((text) => `<p>${text}</p>`).join("")}<p class="caption">以上为原创演示正文，用于检查预览排版与章节切换。</p>`;
  document
    .querySelectorAll("[data-chapter]")
    .forEach((button) =>
      button.classList.toggle("active", Number(button.dataset.chapter) === state.selectedChapter),
    );
  const source = sources.find((item) => item.id === $("#detail-source").value);
  $("#detail-hint").textContent =
    `${source.name} · 当前第 ${state.selectedChapter + 1} 章 · 示例预览`;
}

function queueBook(id, subscribe = true) {
  const book = books.find((item) => item.id === id);
  if (subscribe && subscriptions.has(id)) {
    if ($("#book-dialog").open) closeDialog("book-dialog");
    showPage("subscriptions");
    notify("已订阅该书，复用原有订阅与正文。");
    return;
  }
  if (subscribe)
    subscriptions.set(id, {
      active: true,
      frequency: $("#default-frequency").value,
      downloaded: artifacts.get(id)?.chapters || 0,
      total: book.chapters,
    });
  jobs.set(id, { chapters: book.chapters });
  if ($("#book-dialog").open) closeDialog("book-dialog");
  renderAll();
  notify(
    subscribe
      ? "已创建示例订阅；可点击“推进示例任务”查看整理结果。"
      : "已排入单次示例任务，不开启追更。",
  );
}

function showArtifact(id) {
  const book = books.find((item) => item.id === id);
  const artifact = artifacts.get(id);
  $("#artifact-content").innerHTML =
    `<div class="candidate"><span class="status good">示例已归档</span><h3>${escapeHtml(book.title)}</h3></div><dl><dt>成品位置</dt><dd><code>${escapeHtml(artifact.path)}</code></dd><dt>正文范围</dt><dd>第 1—${artifact.chapters} 章</dd><dt>元数据</dt><dd>${escapeHtml(book.author)} · ${escapeHtml(book.language)} · ${escapeHtml(book.tags)}</dd><dt>配套文件</dt><dd>cover.jpg / metadata.opf / source.json</dd><dt>内容版本</dt><dd>第 ${artifact.revision} 版 · 同一本书持续更新</dd><dt>后续追更</dt><dd>${subscriptions.has(id) ? "仅获取新增或需要修订的章节" : "本次为单次下载"}</dd></dl>`;
  $("#artifact-dialog").showModal();
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.page) showPage(button.dataset.page);
  if (button.dataset.close) closeDialog(button.dataset.close);
  if (button.dataset.preview) openBook(button.dataset.preview);
  if (button.dataset.subscribe) queueBook(button.dataset.subscribe);
  if (button.dataset.artifact) showArtifact(button.dataset.artifact);
  if (button.dataset.chapter !== undefined) {
    state.selectedChapter = Number(button.dataset.chapter);
    renderChapter();
  }
  if (button.dataset.toggle) {
    const sub = subscriptions.get(button.dataset.toggle);
    sub.active = !sub.active;
    renderAll();
  }
  if (button.dataset.scan) {
    const id = button.dataset.scan;
    const sub = subscriptions.get(id);
    jobs.set(id, { chapters: sub.total });
    renderAll();
    notify("示例检查已完成，缺失章节已加入本页任务。");
  }
  if (button.dataset.cancel) {
    jobs.delete(button.dataset.cancel);
    renderAll();
    notify("本次示例任务已取消，订阅及原成品保留。");
  }
  if (button.dataset.sourceToggle) {
    const source = sources.find((item) => item.id === button.dataset.sourceToggle);
    source.enabled = !source.enabled;
    renderAll();
  }
});

document.addEventListener("change", (event) => {
  if (event.target.dataset.frequency)
    subscriptions.get(event.target.dataset.frequency).frequency = event.target.value;
});
$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.searched = true;
  renderSearch();
});
$("#detail-source").addEventListener("change", renderChapter);
$("#subscribe-book").addEventListener("click", () => queueBook(state.selectedBook.id));
$("#download-once").addEventListener("click", () => queueBook(state.selectedBook.id, false));
$("#heading-action").addEventListener("click", () => $("#source-dialog").showModal());
$("#theme").addEventListener("click", () => {
  document.documentElement.dataset.theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
});
$("#reset").addEventListener("click", () => location.reload());
$("#settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  notify("设置已保存在本页演示中，刷新后重置。");
});

$("#advance").addEventListener("click", () => {
  for (const [id, job] of jobs) {
    const previous = artifacts.get(id);
    const book = books.find((item) => item.id === id);
    const base = $("#nas-path").value.replace(/\/+$/, "");
    artifacts.set(id, {
      chapters: job.chapters,
      revision: (previous?.revision || 0) + 1,
      path: previous?.path || `${base}/${book.author}/${book.title} [${id}]/book.epub`,
    });
    const sub = subscriptions.get(id);
    if (sub) sub.downloaded = job.chapters;
  }
  jobs.clear();
  showPage("library");
  notify("示例任务已推进到归档完成，可查看成品结构。");
});

$("#metadata-open").addEventListener("click", () => {
  const book = state.selectedBook;
  $("#candidate-title").textContent = `${book.title} / ${book.author}`;
  $("#meta-title").value = book.title;
  $("#meta-author").value = book.author;
  $("#meta-language").value = book.language;
  $("#meta-tags").value = book.tags;
  $("#meta-description").value = book.description;
  $("#metadata-dialog").showModal();
});
$("#metadata-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const book = state.selectedBook;
  book.title = $("#meta-title").value.trim();
  book.author = $("#meta-author").value.trim();
  book.language = $("#meta-language").value.trim();
  book.tags = $("#meta-tags").value.trim();
  book.description = $("#meta-description").value.trim();
  book.manualMetadata = true;
  $("#detail-title").textContent = book.title;
  $("#detail-subtitle").textContent = `${book.author} · ${book.chapters} 章 · ${book.status}`;
  closeDialog("metadata-dialog");
  renderAll();
  notify("资料已应用于示例书籍，人工选择保持优先。");
});

$("#source-example").addEventListener("click", () => {
  $("#source-json").value = JSON.stringify(
    [
      {
        bookSourceUrl: "https://example.invalid/books",
        bookSourceName: "示例新书源",
        searchUrl: "/search?q={{key}}",
        ruleSearch: { bookList: "$.items", name: "$.title", bookUrl: "$.url" },
        ruleToc: { chapterList: "$.chapters", chapterName: "$.title", chapterUrl: "$.url" },
        ruleContent: { content: "$.text" },
      },
    ],
    null,
    2,
  );
});
$("#source-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (file) $("#source-json").value = await file.text();
});
$("#source-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const raw = $("#source-json").value.trim();
  const url = $("#source-url").value.trim();
  if (!raw) {
    $("#import-result").textContent = url
      ? "已在本页记录规则地址。原型不访问网络，尚未取得或导入远端规则。"
      : "请粘贴 JSON、选择文件或填写规则直链。";
    return;
  }
  let entries;
  try {
    const parsed = JSON.parse(raw);
    entries = Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    $("#import-result").textContent = "JSON 格式有误，已有书源保持不变。";
    return;
  }
  let imported = 0;
  let invalid = 0;
  for (const entry of entries) {
    if (
      !entry ||
      typeof entry.bookSourceUrl !== "string" ||
      typeof entry.bookSourceName !== "string" ||
      !entry.bookSourceName.trim() ||
      !entry.bookSourceUrl.trim()
    ) {
      invalid += 1;
      continue;
    }
    const existing = sources.find((source) => source.originalId === entry.bookSourceUrl);
    if (existing) {
      existing.name = entry.bookSourceName;
    } else
      sources.push({
        id: `import-${sources.length}`,
        originalId: entry.bookSourceUrl,
        name: entry.bookSourceName,
        kind: "web",
        enabled: entry.enabled !== false,
        state: "pending",
        detail: "本页结构导入成功 · 规则尚未运行，兼容性未验证",
      });
    imported += 1;
  }
  $("#import-result").textContent =
    `处理 ${entries.length} 项：导入/更新 ${imported} 项，无效 ${invalid} 项；既有启停状态保留。`;
  renderAll();
});
$("#local-import").addEventListener("click", () => $("#local-file").click());
$("#local-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file)
    notify(`已选择“${file.name}”。本原型不读取正文或执行转换；正式功能沿统一整理任务处理。`);
});

renderAll();
