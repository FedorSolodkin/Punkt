function getCookie(name) {
  const match = document.cookie.match('(^|;\\s*)' + name + '=([^;]*)');
  return match ? decodeURIComponent(match[2]) : null;
}

function newRequestId() {
  if (window.crypto && window.crypto.randomUUID) {
    return window.crypto.randomUUID();
  }
  // Резервный вариант для старых браузеров (RFC 4122 v4).
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function showFieldError(form, field, message) {
  const el = form.querySelector('[data-error-for="' + field + '"]');
  if (el) el.textContent = message || '';
}

function clearFieldErrors(form) {
  form.querySelectorAll('[data-error-for]').forEach((el) => { el.textContent = ''; });
}

function setBusy(form, busyText) {
  const btn = form.querySelector('[type="submit"], [data-submit]');
  if (!btn) return;
  btn.disabled = true;
  btn.dataset.originalText = btn.dataset.originalText || btn.textContent;
  btn.textContent = busyText || 'Сохраняем…';
}

function clearBusy(form) {
  const btn = form.querySelector('[type="submit"], [data-submit]');
  if (!btn) return;
  btn.disabled = false;
  if (btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
}

async function submitPunktForm(form, { method = 'POST', asJson = true, extraFields = {} } = {}) {
  clearFieldErrors(form);
  const alertBox = form.querySelector('[data-alert]');
  if (alertBox) { alertBox.textContent = ''; alertBox.className = ''; }
  setBusy(form);

  const requestId = newRequestId();
  const url = form.action;

  try {
    let response;
    if (asJson) {
      const data = {};
      new FormData(form).forEach((value, key) => { data[key] = value; });
      if ('version' in data) data.version = Number(data.version);
      Object.assign(data, extraFields, { request_id: requestId });
      response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(data),
      });
    } else {
      const formData = new FormData(form);
      formData.append('request_id', requestId);
      Object.entries(extraFields).forEach(([k, v]) => formData.append(k, v));
      response = await fetch(url, {
        method,
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
        body: formData,
      });
    }

    let payload = null;
    try { payload = await response.json(); } catch (e) { /* нет тела */ }

    if (response.ok) {
      window.location.reload();
      return;
    }

    const err = (payload && payload.error) || { message: 'Не удалось получить подтверждение. Повторите запрос', code: 'NETWORK' };
    if (err.fields) {
      Object.entries(err.fields).forEach(([field, msg]) => showFieldError(form, field, msg));
    }
    if (alertBox) {
      alertBox.textContent = response.status === 409
        ? 'Карточка изменилась — обновите страницу перед повтором.'
        : err.message;
      alertBox.className = 'alert alert--error';
    }
  } catch (networkError) {
    if (alertBox) {
      alertBox.textContent = 'Не удалось получить подтверждение. Повторите запрос (' + requestId + ')';
      alertBox.className = 'alert alert--error';
    }
  } finally {
    clearBusy(form);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('form[data-punkt-json]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      submitPunktForm(form, { asJson: true });
    });
  });

  document.querySelectorAll('form[data-punkt-multipart]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      submitPunktForm(form, { asJson: false });
    });
  });

  document.querySelectorAll('form[data-punkt-review]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const decision = event.submitter ? event.submitter.value : 'accept';
      let reason = '';
      if (decision === 'return') {
        reason = window.prompt('Причина возврата:', '') || '';
        if (!reason.trim()) return;
      }
      submitPunktForm(form, { asJson: true, extraFields: { decision, reason } });
    });
  });
});
