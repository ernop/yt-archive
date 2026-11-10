const box = document.getElementById('add');
const heading = document.getElementById('add-status');
const detail = document.getElementById('add-detail');
(async () => {
  try {
    const res = await fetch('/api/get', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: box.dataset.url, via: 'bookmarklet'}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    if (data.browse_url) {
      window.resizeTo(Math.min(screen.availWidth, 1280), screen.availHeight);
      location.replace(data.browse_url);
      return;
    }
    box.classList.add('done');
    heading.textContent = data.status === 'complete' ? 'Already archived'
      : data.existing ? 'Already in the queue' : 'Queued';
    detail.textContent = (data.title || data.video_id) + ' · this window closes itself';
    setTimeout(() => window.close(), 1500);
  } catch (err) {
    box.classList.add('error');
    heading.textContent = 'Not queued';
    detail.textContent = err.message;
  }
})();
