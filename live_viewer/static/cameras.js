(() => {
  "use strict";

  const BASE_PATH = (document.documentElement.dataset.basePath || "").replace(/\/$/, "");
  const apiUrl = (path) => `${BASE_PATH}${path.startsWith("/") ? path : "/" + path}`;

  const dialog = document.getElementById("add-camera-dialog");
  const form = document.getElementById("add-camera-form");
  const openButton = document.getElementById("add-camera-btn");
  const errorBox = document.getElementById("add-camera-error");
  const submitButton = document.getElementById("add-camera-submit");
  const sourceType = document.getElementById("cam-source-type");
  const sourceUrlField = document.querySelector("[data-source-url-field]");
  const rtspFields = Array.from(document.querySelectorAll("[data-rtsp-only]"));
  let rtspSnapshot = null;

  if (!dialog || !form || !openButton) return;

  function setAddValue(id, value) {
    const field = document.getElementById(id);
    if (field) field.value = value;
  }

  function getAddValue(id, fallback) {
    const field = document.getElementById(id);
    return field ? field.value : fallback;
  }

  function applySourceType() {
    const isPublic = Boolean(sourceType && sourceType.value === "public");
    if (sourceUrlField) sourceUrlField.hidden = !isPublic;
    rtspFields.forEach((field) => { field.hidden = isPublic; });

    if (isPublic && rtspSnapshot === null) {
      rtspSnapshot = {
        host: getAddValue("cam-host", ""),
        port: getAddValue("cam-port", "554"),
        username: getAddValue("cam-user", "admin"),
        password: getAddValue("cam-password", ""),
        path: getAddValue("cam-path", "/onvif1"),
        transport: getAddValue("cam-transport", "udp"),
      };
      // A fonte pública não usa RTSP; o viewer só precisa do link. Estes
      // valores existem para manter o cadastro compatível com o config.json.
      setAddValue("cam-host", "youtube.com");
      setAddValue("cam-port", "443");
      setAddValue("cam-user", "publico");
      setAddValue("cam-password", "publico");
      setAddValue("cam-path", "/");
      setAddValue("cam-transport", "tcp");
    } else if (!isPublic && rtspSnapshot !== null) {
      setAddValue("cam-host", rtspSnapshot.host);
      setAddValue("cam-port", rtspSnapshot.port);
      setAddValue("cam-user", rtspSnapshot.username);
      setAddValue("cam-password", rtspSnapshot.password);
      setAddValue("cam-path", rtspSnapshot.path);
      setAddValue("cam-transport", rtspSnapshot.transport);
      rtspSnapshot = null;
    }
  }

  if (sourceType) sourceType.addEventListener("change", applySourceType);
  applySourceType();

  function closeDialog() {
    if (typeof dialog.close === "function" && dialog.open) dialog.close();
    else dialog.removeAttribute("open");
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    if (!errorBox) return;
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  openButton.addEventListener("click", () => {
    clearError();
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    const nameField = document.getElementById("cam-name");
    if (nameField) nameField.focus();
  });

  dialog.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", closeDialog);
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });
  dialog.addEventListener("cancel", clearError);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();

    const data = new FormData(form);
    const sourceTypeValue = String(data.get("source_type") || "rtsp");
    const sourceUrl = String(data.get("source_url") || "").trim();
    if (sourceTypeValue === "public" && !sourceUrl) {
      showError("Informe o link da transmissão pública.");
      return;
    }
    const payload = {
      source_type: sourceTypeValue,
      source_url: sourceUrl,
      name: String(data.get("name") || ""),
      host: String(data.get("host") || ""),
      port: Number(data.get("port")),
      username: String(data.get("username") || ""),
      password: String(data.get("password") || ""),
      path: String(data.get("path") || ""),
      transport: String(data.get("transport") || "udp"),
      output_width: Number(data.get("output_width")),
      output_height: Number(data.get("output_height")),
      input_fps: Number(data.get("input_fps")),
      video_bitrate: String(data.get("video_bitrate") || ""),
      audio_bitrate: String(data.get("audio_bitrate") || ""),
    };

    submitButton.disabled = true;
    submitButton.textContent = "Adicionando…";
    try {
      const response = await fetch(apiUrl("/api/cameras"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) {
        showError(result.error || `Não foi possível adicionar a câmera (HTTP ${response.status}).`);
        return;
      }
      closeDialog();
      window.location.reload();
    } catch (_) {
      showError("Não foi possível contatar o servidor do visualizador.");
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "Adicionar câmera";
    }
  });

  const renameDialog = document.getElementById("rename-camera-dialog");
  const renameForm = document.getElementById("rename-camera-form");
  const renameInput = document.getElementById("rename-name");
  const renameHint = document.getElementById("rename-camera-hint");
  const renameError = document.getElementById("rename-camera-error");
  const renameSubmit = document.getElementById("rename-camera-submit");
  let editingId = null;

  if (renameDialog && renameForm && renameInput) {
    function closeRename() {
      if (typeof renameDialog.close === "function" && renameDialog.open) renameDialog.close();
      else renameDialog.removeAttribute("open");
    }

    function showRenameError(message) {
      if (!renameError) return;
      renameError.textContent = message;
      renameError.hidden = false;
    }

    function clearRenameError() {
      if (!renameError) return;
      renameError.textContent = "";
      renameError.hidden = true;
    }

    document.addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest('[data-role="rename"]')
        : null;
      if (!button) return;
      const card = button.closest("[data-camera]");
      if (!card) return;
      editingId = card.dataset.camera;
      const title = card.querySelector("h2");
      const currentName = title ? title.textContent.trim() : editingId;
      renameInput.value = currentName;
      if (renameHint) renameHint.textContent = `Câmera atual: ${currentName}`;
      clearRenameError();
      if (typeof renameDialog.showModal === "function") renameDialog.showModal();
      else renameDialog.setAttribute("open", "");
      renameInput.focus();
      renameInput.select();
    });

    renameDialog.querySelectorAll("[data-rename-close]").forEach((button) => {
      button.addEventListener("click", closeRename);
    });
    renameDialog.addEventListener("click", (event) => {
      if (event.target === renameDialog) closeRename();
    });
    renameDialog.addEventListener("cancel", clearRenameError);

    renameForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearRenameError();
      const name = renameInput.value.trim();
      if (!name || !editingId) {
        showRenameError("Informe o nome da câmera.");
        return;
      }

      renameSubmit.disabled = true;
      renameSubmit.textContent = "Salvando…";
      try {
        const response = await fetch(
          apiUrl(`/api/cameras/${encodeURIComponent(editingId)}/rename`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
          }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
          showRenameError(result.error || `Não foi possível salvar o nome (HTTP ${response.status}).`);
          return;
        }
        closeRename();
        window.location.reload();
      } catch (_) {
        showRenameError("Não foi possível contatar o servidor do visualizador.");
      } finally {
        renameSubmit.disabled = false;
        renameSubmit.textContent = "Salvar nome";
      }
    });
  }

  const removeDialog = document.getElementById("remove-camera-dialog");
  const removeForm = document.getElementById("remove-camera-form");
  const removeHint = document.getElementById("remove-camera-hint");
  const removeError = document.getElementById("remove-camera-error");
  const removeSubmit = document.getElementById("remove-camera-submit");
  let removingId = null;

  if (removeDialog && removeForm && removeSubmit) {
    function closeRemove() {
      if (typeof removeDialog.close === "function" && removeDialog.open) removeDialog.close();
      else removeDialog.removeAttribute("open");
    }

    function showRemoveError(message) {
      if (!removeError) return;
      removeError.textContent = message;
      removeError.hidden = false;
    }

    function clearRemoveError() {
      if (!removeError) return;
      removeError.textContent = "";
      removeError.hidden = true;
    }

    document.addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest('[data-role="remove"]')
        : null;
      if (!button) return;
      const card = button.closest("[data-camera]");
      if (!card) return;
      removingId = card.dataset.camera;
      const title = card.querySelector("h2");
      const name = title ? title.textContent.trim() : removingId;
      if (removeHint) {
        removeHint.textContent = `Remover "${name}"? O stream desta câmera será encerrado.`;
      }
      clearRemoveError();
      if (typeof removeDialog.showModal === "function") removeDialog.showModal();
      else removeDialog.setAttribute("open", "");
      removeSubmit.focus();
    });

    removeDialog.querySelectorAll("[data-remove-close]").forEach((button) => {
      button.addEventListener("click", closeRemove);
    });
    removeDialog.addEventListener("click", (event) => {
      if (event.target === removeDialog) closeRemove();
    });
    removeDialog.addEventListener("cancel", clearRemoveError);

    removeForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearRemoveError();
      if (!removingId) {
        showRemoveError("Selecione uma câmera para remover.");
        return;
      }

      removeSubmit.disabled = true;
      removeSubmit.textContent = "Removendo…";
      try {
        const response = await fetch(
          apiUrl(`/api/cameras/${encodeURIComponent(removingId)}/remove`),
          { method: "POST" }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
          showRemoveError(result.error || `Não foi possível remover a câmera (HTTP ${response.status}).`);
          return;
        }
        closeRemove();
        window.location.reload();
      } catch (_) {
        showRemoveError("Não foi possível contatar o servidor do visualizador.");
      } finally {
        removeSubmit.disabled = false;
        removeSubmit.textContent = "Remover câmera";
      }
    });
  }

  const settingsDialog = document.getElementById("settings-camera-dialog");
  const settingsForm = document.getElementById("settings-camera-form");
  const settingsHint = document.getElementById("settings-camera-hint");
  const settingsError = document.getElementById("settings-camera-error");
  const settingsSubmit = document.getElementById("settings-camera-submit");
  const passwordField = document.getElementById("settings-password");
  const showPassword = document.getElementById("settings-show-password");
  let settingsId = null;

  if (settingsDialog && settingsForm && settingsSubmit) {
    function closeSettings() {
      if (typeof settingsDialog.close === "function" && settingsDialog.open) settingsDialog.close();
      else settingsDialog.removeAttribute("open");
    }

    function showSettingsError(message) {
      if (!settingsError) return;
      settingsError.textContent = message;
      settingsError.hidden = false;
    }

    function clearSettingsError() {
      if (!settingsError) return;
      settingsError.textContent = "";
      settingsError.hidden = true;
    }

    function setSetting(name, value) {
      const field = settingsForm.elements[name];
      if (field) field.value = value == null ? "" : String(value);
    }

    function fillSettings(camera) {
      setSetting("id", camera.id || settingsId);
      setSetting("name", camera.name || "");
      setSetting("host", camera.host || "");
      setSetting("port", camera.port || 554);
      setSetting("username", camera.username || "");
      setSetting("secret", camera.secret || "");
      setSetting("password", camera.password || "");
      setSetting("path", camera.path || "");
      setSetting("transport", camera.transport || "udp");
      setSetting("enabled", camera.enabled === false ? "false" : "true");
      setSetting("detection_enabled",
        camera.detection_enabled === true ? "true"
          : camera.detection_enabled === false ? "false" : "inherit");
      setSetting("source_url", camera.source_url || "");
      setSetting("output_width", camera.output_width || 1280);
      setSetting("output_height", camera.output_height == null ? 0 : camera.output_height);
      setSetting("input_fps", camera.input_fps || 15);
      setSetting("video_bitrate", camera.video_bitrate || "2500k");
      setSetting("audio_bitrate", camera.audio_bitrate || "64k");
      if (settingsHint) {
        settingsHint.textContent = `Câmera ${camera.name || settingsId} (${settingsId}). ` +
          "Salvar aplica as mudanças e reinicia o stream.";
      }
    }

    if (showPassword) {
      showPassword.addEventListener("change", () => {
        if (passwordField) passwordField.type = showPassword.checked ? "text" : "password";
      });
    }

    document.addEventListener("click", async (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest('[data-role="settings"]')
        : null;
      if (!button) return;
      const card = button.closest("[data-camera]");
      if (!card) return;

      settingsId = card.dataset.camera;
      clearSettingsError();
      if (showPassword) showPassword.checked = false;
      if (passwordField) passwordField.type = "password";
      settingsSubmit.disabled = true;
      if (typeof settingsDialog.showModal === "function") settingsDialog.showModal();
      else settingsDialog.setAttribute("open", "");

      try {
        const response = await fetch(
          apiUrl(`/api/cameras/${encodeURIComponent(settingsId)}`),
          { headers: { "Cache-Control": "no-store" } }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
          throw new Error(result.error || `Não foi possível carregar (HTTP ${response.status}).`);
        }
        fillSettings(result.camera || {});
        const nameField = settingsForm.elements["name"];
        if (nameField) nameField.focus();
      } catch (error) {
        showSettingsError(error && error.message
          ? error.message
          : "Não foi possível contatar o servidor do visualizador.");
      } finally {
        settingsSubmit.disabled = false;
      }
    });

    settingsDialog.querySelectorAll("[data-settings-close]").forEach((button) => {
      button.addEventListener("click", closeSettings);
    });
    settingsDialog.addEventListener("click", (event) => {
      if (event.target === settingsDialog) closeSettings();
    });
    settingsDialog.addEventListener("cancel", clearSettingsError);

    settingsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearSettingsError();
      if (!settingsId) {
        showSettingsError("Selecione uma câmera para configurar.");
        return;
      }

      const data = new FormData(settingsForm);
      const detection = String(data.get("detection_enabled") || "inherit");
      const payload = {
        name: String(data.get("name") || ""),
        host: String(data.get("host") || ""),
        port: Number(data.get("port")),
        username: String(data.get("username") || ""),
        password: String(data.get("password") || ""),
        path: String(data.get("path") || ""),
        transport: String(data.get("transport") || "udp"),
        enabled: String(data.get("enabled") || "true") === "true",
        detection_enabled: detection === "inherit" ? null : detection === "true",
        source_url: String(data.get("source_url") || ""),
        output_width: Number(data.get("output_width")),
        output_height: Number(data.get("output_height")),
        input_fps: Number(data.get("input_fps")),
        video_bitrate: String(data.get("video_bitrate") || ""),
        audio_bitrate: String(data.get("audio_bitrate") || ""),
      };

      settingsSubmit.disabled = true;
      settingsSubmit.textContent = "Salvando…";
      try {
        const response = await fetch(
          apiUrl(`/api/cameras/${encodeURIComponent(settingsId)}/update`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
          showSettingsError(result.error || `Não foi possível salvar (HTTP ${response.status}).`);
          return;
        }
        closeSettings();
        window.location.reload();
      } catch (_) {
        showSettingsError("Não foi possível contatar o servidor do visualizador.");
      } finally {
        settingsSubmit.disabled = false;
        settingsSubmit.textContent = "Salvar configurações";
      }
    });
  }
})();
