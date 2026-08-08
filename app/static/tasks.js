(() => {
  const stageLabels = {
    queued: "Очікує",
    preparing: "Підготовка",
    analyzing: "Аналіз документа",
    pdf_text: "Текст PDF",
    pdf_ocr: "OCR PDF",
    djvu_hidden_text: "Прихований текст DJVU",
    rendering: "Рендеринг сторінок",
    djvu_ocr: "OCR DJVU",
    extracting: "Витягування тексту",
    postprocessing: "Постобробка",
    cleaning: "Очищення тексту",
    verifying: "Перевірка extraction",
    writing: "Запис результатів",
    ready: "Готово",
    completed: "Завершено",
    failed: "Помилка",
    cancelling: "Скасування",
    cancelled: "Скасовано",
    duplicate: "Дублікат",
  };

  function durationText(started, finished, status) {
    if (!started) return "";
    const seconds = Math.max(0, Math.floor((new Date(finished || Date.now()) - new Date(started)) / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    const value = [hours ? `${hours} год` : "", minutes ? `${minutes} хв` : "", `${remainder} с`]
      .filter(Boolean)
      .join(" ");
    return ["processing", "cancelling"].includes(status)
      ? `Обробляється: ${value}`
      : `Час обробки: ${value}`;
  }

  function form(action, label, dangerous = false, confirmation = "") {
    const element = document.createElement("form");
    element.method = "post";
    element.action = action;
    if (confirmation) element.onsubmit = () => window.confirm(confirmation);
    const button = document.createElement("button");
    button.textContent = label;
    if (dangerous) button.className = "danger";
    element.append(button);
    return element;
  }

  function renderActions(container, task) {
    container.replaceChildren();
    const base = `/tasks/${task.id}`;
    if (["queued", "processing"].includes(task.status)) {
      container.append(form(`${base}/cancel`, "Cancel", true));
      return;
    }
    if (task.status === "cancelling") return;
    if (task.status === "ready") container.append(form(`${base}/approve`, "Approve"));
    if (task.status === "failed") container.append(form(`${base}/retry`, "Retry"));
    if (["ready", "completed", "failed", "cancelled", "duplicate"].includes(task.status)) {
      const managed = task.library_managed;
      const confirmation = managed
        ? "Файл буде фізично видалено з Library разом із результатами обробки. Продовжити?"
        : "Завантажений файл і результати обробки буде видалено. Продовжити?";
      container.append(form(`${base}/delete`, managed ? "Видалити файл" : "Видалити", true, confirmation));
    }
  }

  function updateTask(task) {
    const row = document.querySelector(`[data-task-id="${task.id}"]`);
    if (!row) return;
    const badge = row.querySelector(".status");
    badge.textContent = task.status;
    badge.className = `status ${task.status}`;
    row.querySelector(".stage").textContent = stageLabels[task.stage] || task.stage;
    row.querySelector(".stage-detail").textContent = task.stage_detail || "";
    const progress = row.querySelector("progress");
    const value = row.querySelector(".progress-value");
    if (task.status === "completed") {
      progress.hidden = true;
      value.textContent = "";
    } else if (task.stage === "pdf_ocr" && task.status === "processing") {
      progress.hidden = false;
      progress.removeAttribute("value");
      value.textContent = "Обробка триває…";
    } else {
      progress.hidden = false;
      progress.value = task.progress;
      value.textContent = `${task.progress}%`;
    }
    row.querySelector(".duration").textContent = durationText(
      task.started_at,
      task.finished_at,
      task.status,
    );
    row.querySelector(".duration").dataset.started = task.started_at || "";
    row.querySelector(".duration").dataset.finished = task.finished_at || "";
    row.classList.toggle("active-task", ["processing", "cancelling"].includes(task.status));
    renderActions(row.querySelector(".actions"), task);
  }

  function updateDurations() {
    document.querySelectorAll("[data-task-id]").forEach((row) => {
      const duration = row.querySelector(".duration");
      if (!duration.dataset.started) return;
      const status = row.querySelector(".status").textContent.trim();
      duration.textContent = durationText(
        duration.dataset.started,
        duration.dataset.finished || null,
        status,
      );
    });
  }

  async function poll() {
    try {
      const response = await fetch("/api/tasks/status", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const payload = await response.json();
      Object.entries(payload.counts).forEach(([key, count]) => {
        const element = document.getElementById(`count-${key}`);
        if (element) element.textContent = count;
      });
      payload.tasks.forEach(updateTask);
    } catch (_error) {
      // A transient polling error must not interrupt upload/catalog forms.
    }
  }

  updateDurations();
  window.setInterval(poll, 2000);
  window.setInterval(updateDurations, 1000);
})();
