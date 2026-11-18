(() => {
  "use strict";

  const BASE_PATH = (document.documentElement.dataset.basePath || "").replace(/\/$/, "");
  const apiUrl = (path) => `${BASE_PATH}${path.startsWith("/") ? path : "/" + path}`;

  const dialog = document.getElementById("add-camera-dialog");
  const form = document.getElementById("add-camera-form");
  const openButton = document.getElementById("add-camera-btn");
  const errorBox = document.getElementById("add-camera-error");
  const submitButton = document.getElementById("add-camera-submit");

  if (!dialog || !form || !openButton) return;

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
    const payload = {
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
})();
