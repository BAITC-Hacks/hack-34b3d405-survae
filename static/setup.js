const form = document.getElementById('setup-form');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const status = document.getElementById('setup-status');
  const button = form.querySelector('button');
  button.disabled = true;
  status.textContent = 'Проверяем настоящий запрос к OpenAI…';
  try {
    const state = await (await fetch('/api/state')).json();
    const response = await fetch('/api/setup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf},
      body: JSON.stringify({api_key: document.getElementById('api-key').value.trim()})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте формат ключа.');
    document.getElementById('api-key').value = '';
    status.textContent = 'OpenAI ответил! Ключ проверен и сохранён. Можно вернуться к консультанту по ссылке ниже.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
