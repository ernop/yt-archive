function localizeTimes(root) {
  (root || document).querySelectorAll('time[data-local]').forEach((el) => {
    const date = new Date(el.dateTime);
    if (!el.dateTime || Number.isNaN(date.getTime())) return;
    const pad = (n) => String(n).padStart(2, '0');
    el.textContent = date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
      + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  });
}
localizeTimes();
