// local_llm Web チャット UI(素の JS、ビルドなし)
//
// - 会話一覧 / 新規 / 削除はサイドバーから
// - 送信は POST /api/conversations/{id}/messages を fetch + ReadableStream で SSE として読む
// - thinking は <details> で折りたたみ表示
// - Markdown は marked(CDN)で描画。コードブロックは <pre> のまま

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const el = {
    list: $("#conversation-list"),
    newChat: $("#new-chat"),
    health: $("#health"),
    modelSelect: $("#model-select"),
    thinking: $("#thinking-toggle"),
    rag: $("#rag-toggle"),
    ragStatus: $("#rag-status"),
    title: $("#conversation-title"),
    banner: $("#banner"),
    messages: $("#messages"),
    emptyState: $("#empty-state"),
    input: $("#input"),
    send: $("#send"),
    template: $("#message-template"),
  };

  const state = {
    conversations: [],
    currentId: null,
    streaming: false,
    healthy: null,
  };

  // ---------------------------------------------------------------- API

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {
        /* 本文が JSON でない */
      }
      throw new Error(`${res.status}: ${detail}`);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  // ---------------------------------------------------------------- 表示ユーティリティ

  function renderMarkdown(text) {
    if (window.marked) {
      return marked.parse(text, { breaks: true, gfm: true });
    }
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function showBanner(text) {
    el.banner.textContent = text;
    el.banner.classList.remove("hidden");
  }

  function hideBanner() {
    el.banner.classList.add("hidden");
  }

  function scrollToBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  function setStreaming(on) {
    state.streaming = on;
    el.send.disabled = on;
    el.input.disabled = on;
    el.newChat.disabled = on;
  }

  /** メッセージ要素を作って末尾に追加し、各パーツへの参照を返す */
  function appendMessage(role, { content = "", reasoning = "" } = {}) {
    el.emptyState.classList.add("hidden");
    const node = el.template.content.firstElementChild.cloneNode(true);
    node.classList.add(role);
    node.querySelector(".role").textContent = role === "user" ? "あなた" : "assistant";

    const thinking = node.querySelector(".thinking");
    const thinkingText = node.querySelector(".thinking-text");
    const contentEl = node.querySelector(".content");
    const sources = node.querySelector(".sources");
    const meta = node.querySelector(".meta");

    if (reasoning) {
      thinkingText.textContent = reasoning;
      thinking.classList.remove("hidden");
    }
    if (role === "user") {
      contentEl.textContent = content;
    } else {
      contentEl.innerHTML = renderMarkdown(content);
    }

    el.messages.appendChild(node);
    scrollToBottom();
    return { node, thinking, thinkingText, contentEl, sources, meta };
  }

  /** sources イベント({n, path, heading, score})を出典リストとして描く */
  function renderSources(listEl, items) {
    listEl.innerHTML = "";
    if (!items || items.length === 0) {
      const li = document.createElement("li");
      li.textContent = "該当する資料がありません(インデックスは作成済みですか?)";
      listEl.appendChild(li);
    }
    for (const it of items || []) {
      const li = document.createElement("li");
      li.value = it.n;
      const path = document.createElement("span");
      path.className = "source-path";
      path.textContent = it.path;
      li.appendChild(path);
      if (it.heading) {
        const h = document.createElement("span");
        h.className = "source-heading";
        h.textContent = " › " + it.heading;
        li.appendChild(h);
      }
      const score = document.createElement("span");
      score.className = "source-score";
      score.textContent = ` (${Number(it.score).toFixed(3)})`;
      li.appendChild(score);
      listEl.appendChild(li);
    }
    listEl.classList.remove("hidden");
  }

  async function loadRagStatus() {
    try {
      const st = await api("/api/rag/status");
      el.ragStatus.textContent = `資料 ${st.documents} 件 / ${st.chunks} チャンク`;
      el.ragStatus.title = `埋め込みモデル: ${st.embedding_model}\nコーパス: ${st.corpus_dir}`;
    } catch (err) {
      el.ragStatus.textContent = "";
    }
  }

  // ---------------------------------------------------------------- 会話一覧

  function renderConversationList() {
    el.list.innerHTML = "";
    for (const conv of state.conversations) {
      const li = document.createElement("li");
      if (conv.id === state.currentId) li.classList.add("active");

      const title = document.createElement("span");
      title.className = "conv-title";
      title.textContent = conv.title || "(無題)";
      title.title = `${conv.model}\n${conv.updated_at}`;
      li.appendChild(title);

      const del = document.createElement("button");
      del.className = "delete";
      del.textContent = "×";
      del.title = "削除";
      del.addEventListener("click", (ev) => {
        ev.stopPropagation();
        deleteConversation(conv.id);
      });
      li.appendChild(del);

      li.addEventListener("click", () => openConversation(conv.id));
      el.list.appendChild(li);
    }
  }

  async function loadConversations() {
    const data = await api("/api/conversations");
    state.conversations = data.conversations;
    renderConversationList();
  }

  function startNewConversation() {
    if (state.streaming) return;
    state.currentId = null;
    el.messages.querySelectorAll(".message").forEach((m) => m.remove());
    el.emptyState.classList.remove("hidden");
    el.title.textContent = "";
    renderConversationList();
    el.input.focus();
  }

  async function openConversation(id) {
    if (state.streaming) return;
    const conv = await api(`/api/conversations/${id}`);
    state.currentId = conv.id;
    el.messages.querySelectorAll(".message").forEach((m) => m.remove());
    el.emptyState.classList.add("hidden");
    el.title.textContent = conv.title || "";
    if (conv.model && [...el.modelSelect.options].some((o) => o.value === conv.model)) {
      el.modelSelect.value = conv.model;
    }
    for (const m of conv.messages) {
      appendMessage(m.role, { content: m.content, reasoning: m.reasoning || "" });
    }
    renderConversationList();
    scrollToBottom();
  }

  async function deleteConversation(id) {
    if (state.streaming) return;
    if (!confirm("この会話を削除しますか?")) return;
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    if (state.currentId === id) startNewConversation();
    await loadConversations();
  }

  // ---------------------------------------------------------------- モデル・疎通

  async function loadModels() {
    try {
      const data = await api("/api/models");
      el.modelSelect.innerHTML = "";
      for (const id of data.models) {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id;
        el.modelSelect.appendChild(opt);
      }
      if (data.models.includes(data.default)) el.modelSelect.value = data.default;
      return true;
    } catch (err) {
      return false;
    }
  }

  async function checkHealth() {
    try {
      const data = await api("/api/health");
      const wasHealthy = state.healthy;
      state.healthy = data.ok;
      if (data.ok) {
        el.health.className = "health ok";
        el.health.textContent = "llama-server: 接続中";
        hideBanner();
        // 復帰したらモデル一覧を取り直す
        if (wasHealthy !== true || el.modelSelect.options.length === 0) await loadModels();
      } else {
        el.health.className = "health down";
        el.health.textContent = "llama-server: 停止中";
        showBanner(
          `llama-server(${data.llama_base_url})に接続できません。` +
            " .\\scripts\\start-server.ps1 で起動してください。"
        );
      }
    } catch (err) {
      el.health.className = "health down";
      el.health.textContent = "API に接続できません";
    }
  }

  // ---------------------------------------------------------------- 送信(SSE)

  /** SSE のバイトストリームを読み、data: の JSON をイベントごとにコールバックへ渡す */
  async function readSse(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = block
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (!data) continue;
        try {
          onEvent(JSON.parse(data));
        } catch (_) {
          /* 壊れた行は無視 */
        }
      }
    }
  }

  async function sendMessage() {
    const text = el.input.value.trim();
    if (!text || state.streaming) return;

    setStreaming(true);
    el.input.value = "";
    autoResize();

    try {
      // 会話が無ければ作る
      if (state.currentId === null) {
        const conv = await api("/api/conversations", {
          method: "POST",
          body: JSON.stringify({ model: el.modelSelect.value || null }),
        });
        state.currentId = conv.id;
      }

      appendMessage("user", { content: text });
      const assistant = appendMessage("assistant");
      assistant.contentEl.classList.add("streaming");

      let reasoning = "";
      let content = "";

      const res = await fetch(`/api/conversations/${state.currentId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          enable_thinking: el.thinking.checked,
          model: el.modelSelect.value || null,
          use_rag: el.rag.checked,
        }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          detail = (await res.json()).detail || detail;
        } catch (_) {}
        throw new Error(`${res.status}: ${detail}`);
      }

      await readSse(res, (ev) => {
        if (ev.type === "sources") {
          renderSources(assistant.sources, ev.items);
        } else if (ev.type === "reasoning") {
          reasoning += ev.text;
          assistant.thinking.classList.remove("hidden");
          assistant.thinking.open = content === ""; // 本文が始まるまでは開いておく
          assistant.thinkingText.textContent = reasoning;
        } else if (ev.type === "content") {
          content += ev.text;
          assistant.thinking.open = false;
          assistant.contentEl.innerHTML = renderMarkdown(content);
        } else if (ev.type === "done") {
          const parts = [];
          if (ev.usage) {
            parts.push(`prompt ${ev.usage.prompt_tokens} / completion ${ev.usage.completion_tokens} tok`);
          }
          if (ev.tokens_per_second != null) parts.push(`${ev.tokens_per_second} tok/s`);
          if (ev.elapsed_seconds != null) parts.push(`${ev.elapsed_seconds}s`);
          if (ev.model) parts.push(ev.model);
          assistant.meta.textContent = parts.join(" · ");
        } else if (ev.type === "error") {
          const span = document.createElement("span");
          span.className = "error";
          span.textContent = `エラー: ${ev.text}`;
          assistant.meta.appendChild(span);
        }
        scrollToBottom();
      });

      assistant.contentEl.classList.remove("streaming");
      await loadConversations();
      const conv = state.conversations.find((c) => c.id === state.currentId);
      if (conv) el.title.textContent = conv.title || "";
    } catch (err) {
      showBanner(`送信に失敗しました: ${err.message}`);
      await loadConversations();
    } finally {
      setStreaming(false);
      el.input.focus();
    }
  }

  // ---------------------------------------------------------------- 入力欄

  function autoResize() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
  }

  el.input.addEventListener("input", autoResize);
  el.input.addEventListener("keydown", (ev) => {
    // Enter で送信、Shift+Enter で改行。日本語入力の変換確定(isComposing)は送信しない
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      sendMessage();
    }
  });
  el.send.addEventListener("click", sendMessage);
  el.newChat.addEventListener("click", startNewConversation);

  // ---------------------------------------------------------------- 初期化

  async function init() {
    await checkHealth();
    await loadConversations();
    await loadRagStatus();
    // URL が #c=<会話 id> なら、その会話を開いた状態で始める(共有・スクリーンショット用)
    const hashId = Number((location.hash.match(/^#c=(\d+)$/) || [])[1]);
    if (hashId) await openConversation(hashId);
    setInterval(checkHealth, 15000);
    el.input.focus();
  }

  init().catch((err) => showBanner(`初期化に失敗しました: ${err.message}`));
})();
