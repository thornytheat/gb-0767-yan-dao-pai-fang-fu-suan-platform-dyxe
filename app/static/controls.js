const form = document.querySelector('#monitor-form');
const sample = document.querySelector('#sample');
const feedback = document.querySelector('#feedback');
const locationFor = (name, run) => {
  const url = new URL(location.href);
  url.searchParams.set('sample', name);
  if (run) url.searchParams.set('run', run);
  else url.searchParams.delete('run');
  return url;
};
sample.addEventListener('change', () => location.assign(locationFor(sample.value)));
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  feedback.hidden = false;
  feedback.dataset.failed = 'false';
  feedback.textContent = '正在复算与归档';
  try {
    const response = await fetch('/api/runs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sample: sample.value}),
    });
    const record = await response.json();
    if (!response.ok) throw new Error(record.detail);
    const target = locationFor(sample.value, record.run_id);
    if (target.href === location.href) feedback.textContent = '已复用原归档';
    else location.assign(target);
  } catch (error) {
    feedback.dataset.failed = 'true';
    feedback.textContent = error.message || '归档失败，请重试';
  } finally {
    button.disabled = false;
  }
});
const filter = document.querySelector('#state-filter');
filter?.addEventListener('change', () => {
  for (const row of document.querySelectorAll('#verdict-table tbody tr')) {
    row.hidden = filter.value !== 'all' && (filter.value === 'late' ? row.dataset.late !== 'True' : row.dataset.state !== filter.value);
  }
});
const restoreForm = document.querySelector('#restore-form');
const restoreFile = document.querySelector('#restore-file');
const restoreFeedback = document.querySelector('#restore-feedback');
restoreForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const button = restoreForm.querySelector('button');
  button.disabled = true;
  restoreFeedback.hidden = false;
  restoreFeedback.dataset.failed = 'false';
  restoreFeedback.textContent = '正在校验证据单';
  try {
    let payload;
    try { payload = JSON.parse(await restoreFile.files[0].text()); }
    catch (_) { throw new Error('证据单不是有效 JSON'); }
    const response = await fetch('/api/restore', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    const record = await response.json();
    if (!response.ok) throw new Error(record.detail);
    restoreFeedback.textContent = record.reused ? '已复用现有留档' : '留档已恢复';
    location.assign(`/monitor/evidence?run=${encodeURIComponent(record.run_id)}`);
  } catch (error) {
    restoreFeedback.dataset.failed = 'true';
    restoreFeedback.textContent = error.message || '恢复失败，请重试';
  } finally {
    button.disabled = false;
  }
});
