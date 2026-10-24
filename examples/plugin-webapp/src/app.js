const button = document.querySelector('#health');
const result = document.querySelector('#result');
const locationLabel = document.querySelector('#location');

locationLabel.textContent = `entry: ${window.location.pathname}`;

button.addEventListener('click', async () => {
  button.disabled = true;
  result.textContent = 'Calling…';
  try {
    const response = await fetch('/api/health', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    result.textContent = `ok · Pan ${body.version}`;
    document.documentElement.dataset.panCall = 'ok';
  } catch (error) {
    result.textContent = `failed · ${error.message}`;
    document.documentElement.dataset.panCall = 'failed';
  } finally {
    button.disabled = false;
  }
});
