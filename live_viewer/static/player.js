(() => {
  "use strict";

  const BASE_PATH = (document.documentElement.dataset.basePath || "").replace(/\/$/, "");
  const apiUrl = (p) => `${BASE_PATH}${p.startsWith("/") ? p : "/" + p}`;

  const cards = Array.from(document.querySelectorAll("[data-camera]"));
  const players = new Map();
  const pendingRestarts = new Set();
  const STATE_LABEL = {
    connecting: "conectando",
    live: "ao vivo",
    replay: "replay",
    reconnecting: "reconectando",
    offline: "offline",
    error: "erro na transmissão",
    running: "ao vivo",
    starting: "conectando",
    unreachable: "offline",
    disabled: "desativada",
    stopped: "parada",
  };

  function q(card, role) {
    return card.querySelector(`[data-role="${role}"]`);
  }

  function showOverlay(card, text) {
    const overlay = q(card, "overlay");
    if (!overlay) return;
    overlay.textContent = text;
    overlay.classList.remove("hidden");
  }

  function hideOverlay(card) {
    const overlay = q(card, "overlay");
    if (overlay) overlay.classList.add("hidden");
  }

  function fmtClock(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function applyStatus(card, status) {
    const badge = q(card, "badge");
    const message = q(card, "message");
    const meta = q(card, "meta");
    const state = status.ui_state || status.state || "stopped";
    const label = status.ui_state_label || STATE_LABEL[state] || STATE_LABEL[status.state] || state;
    badge.textContent = label;
    badge.className = `badge ${state}`;
    if (status.last_error) {
      const at = fmtClock(status.last_error_at);
      message.textContent = `${status.message || ""}${at ? ` (${at})` : ""} · ${status.last_error}`;
    } else {
      message.textContent = status.message || "";
    }
    const parts = [];
    if (status.restarts != null) parts.push(`Reconexões: ${status.restarts}`);
    if (status.connection_uptime_seconds != null) parts.push(`Tempo conectado: ${Math.round(status.connection_uptime_seconds)}s`);
    if (status.detected_resolution || status.detected_fps) {
      parts.push(`Vídeo: ${status.detected_resolution || "?"}${status.detected_fps ? ` @ ${status.detected_fps} fps` : ""}`);
    } else if (status.configured_resolution) {
      parts.push(`Saída: ${status.configured_resolution}${status.configured_input_fps ? ` @ ${status.configured_input_fps} fps cfg` : ""}`);
    }
    const lastSeg = fmtClock(status.last_segment_at);
    if (lastSeg) parts.push(`Último segmento: ${lastSeg}`);
    if (status.window_start && status.window_end) {
      parts.push(`Janela: ${fmtClock(status.window_start)}–${fmtClock(status.window_end)}`);
    }
    if (status.stream_healthy === true) parts.push("Saúde: OK");
    if (meta) meta.textContent = parts.join(" · ");

    // Enquanto um reinício manual está em curso, não deixa o polling trocar
    // o aviso "Reiniciando stream…" por "Reconectando…" e piscar a tela.
    if (pendingRestarts.has(card.dataset.camera)) return;

    if (state === "live" || status.state === "running") {
      if (q(card, "video").readyState >= 2) hideOverlay(card);
    } else if (state === "connecting" || status.state === "starting") {
      showOverlay(card, "Conectando à câmera…");
    } else if (state === "reconnecting") {
      showOverlay(card, "Reconectando à câmera…");
    } else if (state === "offline" || status.state === "unreachable") {
      showOverlay(card, "Câmera offline. Tentando novamente…");
    } else if (state === "error" || status.state === "error") {
      showOverlay(card, "Erro na transmissão. Tentando recuperar…");
    } else {
      showOverlay(card, status.message || "Transmissão indisponível");
    }
  }

  // ---------------------------------------------------------------- detecções
  function drawDetections(card, state) {
    const canvas = q(card, "detections");
    const badge = q(card, "ai-badge");
    if (!canvas) return;

    const detections = Array.isArray(state && state.detections) ? state.detections : [];
    const updated = state && state.updated_at ? Date.parse(state.updated_at) : 0;
    const stale = !updated || Date.now() - updated > 6000;
    const visible = stale ? [] : detections;

    if (badge) {
      const enabled = Boolean(state && state.enabled);
      badge.classList.toggle("hidden", !enabled);
      if (enabled) {
        badge.textContent = state.status === "error"
          ? "IA: erro"
          : `IA: ${visible.length}${visible.length === 1 ? " pessoa" : " pessoas"}`;
      }
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const backingWidth = Math.max(1, Math.round(width * ratio));
    const backingHeight = Math.max(1, Math.round(height * ratio));
    if (canvas.width !== backingWidth || canvas.height !== backingHeight) {
      canvas.width = backingWidth;
      canvas.height = backingHeight;
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (!visible.length) return;

    // O vídeo usa object-fit: contain; as caixas vêm normalizadas pelo frame
    // processado, então precisamos desenhar dentro do retângulo real do vídeo.
    const video = q(card, "video");
    const videoWidth = video && video.videoWidth ? video.videoWidth : 16;
    const videoHeight = video && video.videoHeight ? video.videoHeight : 9;
    const scale = Math.min(width / videoWidth, height / videoHeight);
    const drawWidth = videoWidth * scale;
    const drawHeight = videoHeight * scale;
    const offsetX = (width - drawWidth) / 2;
    const offsetY = (height - drawHeight) / 2;

    ctx.lineWidth = 2;
    ctx.strokeStyle = "#39d0a3";
    for (const item of visible) {
      // O backend já só envia pessoas; a checagem evita qualquer classe
      // interna (veículos) aparecer caso a API mude.
      if (item.class_id != null && Number(item.class_id) !== 0) continue;
      if (item.nx == null || item.ny == null) continue;
      const x = offsetX + item.nx * drawWidth;
      const y = offsetY + item.ny * drawHeight;
      ctx.strokeRect(x, y, (item.nw || 0) * drawWidth, (item.nh || 0) * drawHeight);
    }
  }

  // ---------------------------------------------------------- recortes laterais
  function cropUrl(card, item) {
    return apiUrl(
      `/events/${encodeURIComponent(card.dataset.camera)}/${encodeURIComponent(item.file)}`
    );
  }

  function makeThumb(card, item, kind) {
    const thumb = document.createElement("div");
    thumb.className = `crop-thumb ${kind}`;
    const img = document.createElement("img");
    img.src = cropUrl(card, item);
    img.alt = kind === "face" ? "Rosto detectado" : "Placa detectada";
    img.title = fmtClock(item.timestamp) || "";
    img.loading = "lazy";
    thumb.appendChild(img);
    return thumb;
  }

  // Mantém só o que cabe na altura disponível: quando enche, os primeiros
  // (mais antigos) ficam escondidos e os novos aparecem embaixo.
  function fitRail(container) {
    if (!container) return;
    const items = Array.from(container.children);
    if (!items.length) return;
    // Medir com `hidden` daria altura 0; desoculta só durante a medição.
    items.forEach((el) => { el.hidden = false; });
    const gap = Number.parseFloat(getComputedStyle(container).rowGap) || 0;
    const available = container.clientHeight;
    let used = 0;
    let keep = 0;
    for (let i = items.length - 1; i >= 0; i -= 1) {
      used += items[i].offsetHeight + (keep ? gap : 0);
      if (used > available) break;
      keep += 1;
    }
    items.forEach((el, index) => {
      el.hidden = index < items.length - keep;
    });
  }

  function renderCrops(card, state) {
    const faces = Array.isArray(state && state.faces) ? state.faces : [];
    const plates = Array.isArray(state && state.plates) ? state.plates : [];
    const signature = `${faces.map((item) => item.file).join("|")}#${plates
      .map((item) => item.file)
      .join("|")}`;
    if (card.dataset.cropSignature === signature) {
      fitRail(q(card, "face-items"));
      fitRail(q(card, "plate-items"));
      return;
    }
    card.dataset.cropSignature = signature;
    const faceItems = q(card, "face-items");
    const plateItems = q(card, "plate-items");
    if (!faceItems || !plateItems) return;
    faceItems.replaceChildren(...faces.map((item) => makeThumb(card, item, "face")));
    plateItems.replaceChildren(...plates.map((item) => makeThumb(card, item, "plate")));
    fitRail(faceItems);
    fitRail(plateItems);
  }

  async function pollDetections() {
    try {
      const response = await fetch(apiUrl("/api/detections"), { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const byId = new Map((data.cameras || []).map((c) => [c.camera_id || c.id, c]));
      for (const card of cards) {
        const state = byId.get(card.dataset.camera);
        drawDetections(card, state);
        renderCrops(card, state);
      }
    } catch (_) {
      for (const card of cards) drawDetections(card, null);
    }
  }

  const LIVE_EPSILON = 3;

  function replayParts(card) {
    return {
      range: q(card, "replay-range"),
      time: q(card, "replay-time"),
      live: q(card, "live"),
    };
  }

  function formatBehind(seconds) {
    const total = Math.max(0, Math.round(seconds));
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    return `-${minutes}:${String(rest).padStart(2, "0")}`;
  }

  function setReplayLabel(card, behind, windowEndIso) {
    const { time, live } = replayParts(card);
    const mode = q(card, "mode");
    if (behind < LIVE_EPSILON) {
      let label = "AO VIVO";
      if (windowEndIso) {
        const c = fmtClock(windowEndIso);
        if (c) label = `AO VIVO · ${c}`;
      }
      time.textContent = label;
      time.classList.remove("replaying");
      live.disabled = true;
      card.classList.remove("replay-mode");
      card.classList.add("live-mode");
      if (mode) { mode.textContent = ""; mode.className = "badge mode hidden"; }
    } else {
      let label = formatBehind(behind);
      if (windowEndIso) {
        const t = new Date(windowEndIso).getTime() - behind * 1000;
        if (Number.isFinite(t)) {
          const c = new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
          label = `REPLAY ${c} (${formatBehind(behind)})`;
        }
      }
      time.textContent = label;
      time.classList.add("replaying");
      live.disabled = false;
      card.classList.add("replay-mode");
      card.classList.remove("live-mode");
      if (mode) { mode.textContent = "REPLAY"; mode.className = "badge mode replay"; }
    }
  }

  function seekableRange(video) {
    try {
      if (video && video.seekable && video.seekable.length) {
        const start = video.seekable.start(0);
        const end = video.seekable.end(video.seekable.length - 1);
        if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
          return { start, end };
        }
      }
    } catch (_) { /* noop */ }
    return null;
  }

  function livePosition(card) {
    const entry = players.get(card.dataset.camera);
    const video = q(card, "video");
    const range = seekableRange(video);
    if (entry && entry.hls && Number.isFinite(entry.hls.liveSyncPosition)) {
      const pos = entry.hls.liveSyncPosition;
      if (range) return Math.min(range.end, Math.max(range.start, pos));
      return pos;
    }
    if (range) return Math.max(range.start, range.end - 0.5);
    if (Number.isFinite(video.duration) && video.duration > 0) {
      return Math.max(0, video.duration - 0.5);
    }
    return null;
  }

  function maxSeekableBehind(card) {
    const video = q(card, "video");
    const edge = livePosition(card);
    const range = seekableRange(video);
    if (edge === null || !range) return null;
    return Math.max(0, edge - range.start - 0.5);
  }

  function seekBehind(card, behind) {
    const entry = players.get(card.dataset.camera);
    const video = q(card, "video");
    const edge = livePosition(card);
    const range = seekableRange(video);
    if (edge === null || !range) return false;
    const safeMaxBehind = Math.max(0, edge - range.start - 0.5);
    const targetBehind = Math.max(0, Math.min(behind, safeMaxBehind));
    if (entry) entry.behind = targetBehind;
    if (targetBehind < LIVE_EPSILON) {
      goLive(card);
      return true;
    }
    const target = Math.min(range.end - 0.2, Math.max(range.start + 0.1, edge - targetBehind));
    try {
      video.currentTime = target;
    } catch (_) {
      return false;
    }
    applyDesiredRate(card);
    video.play().catch(() => {});
    return true;
  }

  function goLive(card) {
    const entry = players.get(card.dataset.camera);
    if (entry) {
      entry.behind = 0;
      entry.dragging = false;
      entry.scaleMax = 0;
    }
    const edge = livePosition(card);
    if (edge !== null) {
      const video = q(card, "video");
      try { video.currentTime = Math.max(0, edge); } catch (_) { /* noop */ }
      applyDesiredRate(card);
      video.play().catch(() => {});
    }
    const { range } = replayParts(card);
    if (range && !range.disabled) range.value = range.max;
    setReplayLabel(card, 0, entry ? entry.windowEnd : null);
  }

  function syncReplay(card, status) {
    const entry = players.get(card.dataset.camera);
    if (!entry) return;
    const { range } = replayParts(card);
    const limit = Number(status.replay_seconds) || 0;
    const available = Math.max(
      0,
      Math.floor(Math.min(limit, Number(status.history_seconds) || 0)),
    );
    entry.available = available;
    entry.windowEnd = status.window_end || null;
    if (available < LIVE_EPSILON) {
      // Ainda não há histórico suficiente (ou replay desativado).
      entry.behind = 0;
      entry.dragging = false;
      entry.scaleMax = 0;
      range.max = String(available);
      range.value = String(available);
      range.disabled = true;
      setReplayLabel(card, 0, entry.windowEnd);
      return;
    }
    const enabled = (status.ui_state || status.state) === "live" || status.state === "running";
    range.disabled = !enabled;
    if (!enabled) return; // mantém o rótulo/posição enquanto reconecta
    if (entry.dragging) return; // não mexe na escala durante o arrasto

    // Limita ao que realmente está seekable no player para evitar saltos para fora da gravação.
    const seekMax = maxSeekableBehind(card);
    const effectiveMax = seekMax === null ? available : Math.max(0, Math.min(available, Math.floor(seekMax)));

    if (entry.behind > 0 && seekMax !== null && entry.behind > seekMax + 1) {
      // Se o ponto atual saiu da área gravada/seekable, volta ao vivo automaticamente.
      entry.behind = 0;
      entry.scaleMax = 0;
      range.max = String(effectiveMax);
      range.value = String(effectiveMax);
      setReplayLabel(card, 0, entry.windowEnd);
      goLive(card);
      return;
    }

    if (entry.behind > effectiveMax) entry.behind = effectiveMax;
    if (entry.behind < LIVE_EPSILON) {
      entry.behind = 0;
      entry.scaleMax = 0;
      range.max = String(effectiveMax);
      range.value = String(effectiveMax);
      setReplayLabel(card, 0, entry.windowEnd);
    } else {
      // Durante o replay a escala fica congelada. Se o max acompanhasse o
      // histórico, o botão voltaria sozinho na direção do ao vivo.
      const scale = Math.max(
        Number(entry.scaleMax) || 0,
        Math.min(effectiveMax, Math.round(entry.behind)),
      );
      range.max = String(scale);
      range.value = String(Math.max(0, scale - Math.round(entry.behind)));
      setReplayLabel(card, entry.behind, entry.windowEnd);
    }
  }

  function destroyPlayer(card) {
    const entry = players.get(card.dataset.camera);
    if (!entry) return;
    clearTimeout(entry.timer);
    if (entry.hls) {
      try { entry.hls.destroy(); } catch (_) { /* noop */ }
    }
    players.delete(card.dataset.camera);
  }

  function getDesiredRate(card) {
    const speed = q(card, "speed");
    const rate = speed ? Number(speed.value) : 1;
    return Number.isFinite(rate) && rate > 0 ? rate : 1;
  }

  function applyDesiredRate(card) {
    const entry = players.get(card.dataset.camera);
    const video = q(card, "video");
    if (!entry || !video) return;
    const rate = Number.isFinite(entry.desiredRate) && entry.desiredRate > 0 ? entry.desiredRate : 1;
    const isReplay = entry.behind >= LIVE_EPSILON;
    if (entry.hls && entry.hls.config) {
      // Em AO VIVO com 1x, permite que o hls.js ajuste para sincronizar (catch-up).
      // Em replay ou com velocidade personalizada, prioriza a escolha do usuário.
      if (!isReplay && Math.abs(rate - 1) < 0.01) {
        entry.hls.config.maxLiveSyncPlaybackRate = 1.5;
        return;
      }
      entry.hls.config.maxLiveSyncPlaybackRate = Math.max(1.5, rate);
    }
    if (Math.abs(video.playbackRate - rate) > 0.01) {
      try { video.playbackRate = rate; } catch (_) { /* noop */ }
    }
  }

  function startPlayer(card, attempt = 0) {
    const camId = card.dataset.camera;
    const video = q(card, "video");
    const src = card.dataset.hls;
    const prev = players.get(camId);
    const desiredRate = prev && Number.isFinite(prev.desiredRate) ? prev.desiredRate : getDesiredRate(card);
    destroyPlayer(card);

    const entry = { hls: null, timer: null, attempt, behind: 0, dragging: false, scaleMax: 0, available: 0, windowEnd: null, desiredRate };
    players.set(camId, entry);

    const retryLater = (reason) => {
      if (entry.timer) return;
      showOverlay(card, reason);
      const delay = Math.min(1500 * Math.max(1, attempt + 1), 8000);
      entry.timer = setTimeout(() => {
        startPlayer(card, attempt + 1);
      }, delay);
    };

    video.onplaying = () => { hideOverlay(card); applyDesiredRate(card); };
    video.onwaiting = () => showOverlay(
      card,
      entry.behind >= LIVE_EPSILON ? "Carregando gravação…" : "Carregando vídeo…",
    );
    video.onerror = () => showOverlay(card, "Falha ao reproduzir. Reconectando…");
    video.onratechange = () => {
      const current = players.get(camId);
      if (!current) return;
      const rate = Number.isFinite(current.desiredRate) ? current.desiredRate : 1;
      // Em ao vivo 1x, o hls.js pode ajustar a taxa para sincronizar; não interferir.
      if (current.behind < LIVE_EPSILON && Math.abs(rate - 1) < 0.01) return;
      if (Math.abs(video.playbackRate - rate) > 0.01) applyDesiredRate(card);
    };
    video.onseeked = () => applyDesiredRate(card);
    video.onloadedmetadata = () => applyDesiredRate(card);

    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls({
        lowLatencyMode: true,
        liveSyncDurationCount: 2,
        maxLiveSyncPlaybackRate: 1.5,
        backBufferLength: 120,
        maxBufferLength: 60,
        maxMaxBufferLength: 180,
        manifestLoadingTimeOut: 8000,
        levelLoadingTimeOut: 8000,
        fragLoadingTimeOut: 15000,
        nudgeOffset: 0.2,
        nudgeMaxRetry: 5,
      });
      entry.hls = hls;
      hls.loadSource(`${src}?t=${Date.now()}`);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        applyDesiredRate(card);
        video.play().catch(() => showOverlay(card, "Clique para iniciar o vídeo"));
      });
      hls.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data || !data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && attempt < 2) {
          try {
            hls.recoverMediaError();
            return;
          } catch (_) { /* recreate below */ }
        }
        retryLater("Falha na transmissão. Reconectando…");
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;
      video.play().catch(() => showOverlay(card, "Clique para iniciar o vídeo"));
    } else {
      showOverlay(card, "Navegador sem suporte a HLS (use Chrome, Edge, Firefox ou Safari atual).");
    }

    applyDesiredRate(card);
    q(card, "overlay").onclick = () => video.play().catch(() => {});
  }

  async function currentStatus(card) {
    const response = await fetch(apiUrl("/api/status"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    return (data.cameras || []).find((c) => c.id === card.dataset.camera) || null;
  }

  function waitForStreamReady(card, beforeRestarts, timeoutMs) {
    const started = Date.now();
    return new Promise((resolve, reject) => {
      const check = async () => {
        try {
          const status = await currentStatus(card);
          if (status) {
            const state = status.state || "";
            const uiState = status.ui_state || "";
            const restarts = Number.isFinite(status.restarts) ? status.restarts : 0;
            const live = state === "running" || uiState === "live";
            if (live && restarts > beforeRestarts) {
              resolve();
              return;
            }
          }
          if (Date.now() - started >= timeoutMs) {
            reject(new Error("A transmissão não voltou a tempo."));
            return;
          }
        } catch (_) { /* tenta novamente */ }
        setTimeout(check, 400);
      };
      check();
    });
  }

  function wireButtons(card) {
    const camId = card.dataset.camera;
    const video = q(card, "video");
    const sound = q(card, "sound");
    const retry = q(card, "retry");
    const volume = q(card, "volume");
    const speed = q(card, "speed");
    const fullscreen = q(card, "fullscreen");
    const wrap = q(card, "wrap");

    sound.addEventListener("click", () => {
      video.muted = !video.muted;
      if (!video.muted && video.volume === 0) video.volume = 1;
      if (volume) volume.value = String(video.muted ? 0 : (video.volume || 1));
      sound.textContent = video.muted ? "Ativar som" : "Silenciar";
    });

    if (volume) {
      volume.addEventListener("input", () => {
        const val = Number(volume.value);
        video.volume = Number.isFinite(val) ? val : 1;
        video.muted = video.volume === 0;
        sound.textContent = video.muted ? "Ativar som" : "Silenciar";
      });
    }

    if (speed) {
      const initial = getDesiredRate(card);
      const entry = players.get(camId);
      if (entry) entry.desiredRate = initial;
      speed.addEventListener("change", () => {
        const rate = getDesiredRate(card);
        const current = players.get(camId);
        if (current) current.desiredRate = rate;
        applyDesiredRate(card);
        // feedback visual: reflete a velocidade aplicada/selecionada
        speed.title = `Velocidade: ${rate}x`;
      });
      speed.title = `Velocidade: ${initial}x`;
    }

    if (fullscreen) {
      fullscreen.addEventListener("click", () => {
        const el = wrap || video;
        if (document.fullscreenElement) {
          document.exitFullscreen().catch(() => {});
        } else if (el && el.requestFullscreen) {
          el.requestFullscreen().catch(() => {
            if (video.requestFullscreen) video.requestFullscreen().catch(() => {});
          });
        } else if (video.webkitRequestFullscreen) {
          video.webkitRequestFullscreen();
        }
      });
    }

    retry.addEventListener("click", async () => {
      // Um clique por vez: evita filas de reinício e o loop de reconexão.
      if (pendingRestarts.has(camId)) return;
      pendingRestarts.add(camId);
      retry.disabled = true;
      showOverlay(card, "Reiniciando stream…");

      let beforeRestarts = 0;
      try {
        try {
          const previous = await currentStatus(card);
          if (previous && Number.isFinite(previous.restarts)) beforeRestarts = previous.restarts;
        } catch (_) { /* segue mesmo sem status anterior */ }

        const response = await fetch(apiUrl(`/api/cameras/${encodeURIComponent(camId)}/restart`), {
          method: "POST",
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        try {
          // Só recria o player depois que o supervisor terminou o reinício.
          await waitForStreamReady(card, beforeRestarts, 20000);
        } catch (_) {
          showOverlay(card, "Aguardando a câmera responder…");
        }
        startPlayer(card, 0);
      } catch (_) {
        showOverlay(card, "Não foi possível pedir o reinício. Tente novamente.");
      } finally {
        pendingRestarts.delete(camId);
        retry.disabled = false;
      }
    });
  }

  function wireReplay(card) {
    const camId = card.dataset.camera;
    const { range, time, live } = replayParts(card);

    const preview = () => {
      const entry = players.get(camId);
      if (!entry) return;
      const max = Number(range.max) || 0;
      entry.behind = Math.max(0, max - Number(range.value));
      setReplayLabel(card, entry.behind, entry.windowEnd);
    };

    range.addEventListener("input", () => {
      const entry = players.get(camId);
      if (entry) entry.dragging = true;
      preview();
    });

    range.addEventListener("change", () => {
      const entry = players.get(camId);
      if (!entry) return;
      entry.dragging = false;
      preview();
      if (entry.behind < LIVE_EPSILON) {
        goLive(card);
        return;
      }
      const scale = Number(range.max) || Math.round(entry.behind);
      entry.scaleMax = scale;
      const seekMax = maxSeekableBehind(card);
      const effectiveMax = seekMax === null ? entry.available : Math.min(entry.available, Math.floor(seekMax));
      if (entry.behind >= scale - 2 && effectiveMax > scale + 2) {
        // Arrastou até o início: estende apenas até o que realmente está gravado/seekable.
        entry.scaleMax = effectiveMax;
        entry.behind = effectiveMax;
        range.max = String(effectiveMax);
        range.value = "0";
        setReplayLabel(card, entry.behind, entry.windowEnd);
      }
      if (seekMax !== null && entry.behind > seekMax + 1) {
        // Fora do intervalo gravado: volta ao vivo automaticamente.
        goLive(card);
        return;
      }
      if (!seekBehind(card, entry.behind)) {
        showOverlay(card, "Preparando gravação…");
        setTimeout(() => {
          const current = players.get(camId);
          if (!current || current.dragging) return;
          if (!seekBehind(card, current.behind)) {
            // Se ainda não for possível buscar no intervalo, evita loading infinito.
            goLive(card);
          }
        }, 600);
      }
    });

    live.addEventListener("click", () => goLive(card));

    time.addEventListener("click", () => {
      const entry = players.get(camId);
      if (entry && entry.behind >= LIVE_EPSILON) goLive(card);
    });
  }

  async function poll() {
    try {
      const response = await fetch(apiUrl("/api/status"), { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const byId = new Map((data.cameras || []).map((c) => [c.id, c]));
      for (const card of cards) {
        const status = byId.get(card.dataset.camera);
        if (status) {
          applyStatus(card, status);
          syncReplay(card, status);
        }
      }
    } catch (_) {
      for (const card of cards) {
        const badge = q(card, "badge");
        badge.textContent = "erro na transmissão";
        badge.className = "badge error";
        showOverlay(card, "Servidor local indisponível. Verifique o terminal.");
      }
    }
  }

  function tickClock() {
    const el = document.getElementById("clock");
    if (el) el.textContent = new Date().toLocaleString("pt-BR");
  }

  for (const card of cards) {
    wireButtons(card);
    wireReplay(card);
    startPlayer(card);
  }
  poll();
  pollDetections();
  tickClock();
  setInterval(poll, 2000);
  setInterval(pollDetections, 1000);
  setInterval(tickClock, 1000);
  window.addEventListener("resize", () => {
    for (const card of cards) {
      fitRail(q(card, "face-items"));
      fitRail(q(card, "plate-items"));
    }
  });
})();
