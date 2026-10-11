// The video page around the shared media player (docs/setup.md): frames saved next to the MP3,
// the shot images lit while their shot plays, and a click on a shot image playing from it.
(() => {
  const root = document.getElementById("ytp");
  const video = root && root.querySelector("video");
  if (!video || typeof MediaPlayer === "undefined") return;
  const id = root.dataset.id;

  async function saveFrame({ time, blob }) {
    const res = await fetch(
      "/api/grab?video_id=" + encodeURIComponent(id) + "&t=" + encodeURIComponent(String(time)),
      { method: "POST", headers: { "Content-Type": "image/png" }, body: blob },
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return "Saved " + data.name;
  }

  // Each marker's id is its shot image's file. The player announces the shot at the playhead,
  // including while it is being built, so this listens before enhancing.
  const figures = new Map();
  document.querySelectorAll(".shots figure[data-file]").forEach((fig) => figures.set(fig.dataset.file, fig));
  let lit = null;
  root.addEventListener("media-player:marker", (event) => {
    const marker = event.detail.marker;
    const fig = (marker && figures.get(marker.id)) || null;
    if (lit) lit.classList.remove("now");
    if (fig) fig.classList.add("now");
    lit = fig;
  });

  const player = MediaPlayer.enhance(video, { saveFrame });

  document.querySelectorAll(".shots figure[data-t]").forEach((fig) => {
    fig.addEventListener("click", (event) => {
      if (event.shiftKey || event.metaKey || event.ctrlKey || event.altKey) return;
      event.preventDefault();
      player.seek(Number(fig.dataset.t), fig.querySelector("figcaption").textContent, "shot");
      player.play();
      player.focus();
    });
  });
})();
