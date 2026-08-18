(() => {
  const root = document.getElementById("ytp");
  if (!root) return;
  const video = root.querySelector("video");
  const id = root.dataset.id;
  const shots = JSON.parse(root.dataset.shots || "[]");
  const fill = root.querySelector(".ytp-scrub-fill");
  const buf = root.querySelector(".ytp-scrub-buf");
  const knob = root.querySelector(".ytp-scrub-knob");
  const tip = root.querySelector(".ytp-tip");
  const timeEl = root.querySelector(".ytp-time");
  const speedEl = root.querySelector(".ytp-speed");
  const flashEl = root.querySelector(".ytp-flash");
  const helpEl = root.querySelector(".ytp-help");
  const resumeEl = root.querySelector(".ytp-resume");
  const ticks = root.querySelector(".ytp-ticks");
  const scrub = root.querySelector(".ytp-scrub");
  const LS_POS = "ytarchive.pos." + id;
  const LS_SPEED = "ytarchive.speed";
  const LS_VOL = "ytarchive.vol";
  const LS_THEATER = "ytarchive.theater";
  const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  let showRemaining = false;
  let hideTimer = 0;
  let flashTimer = 0;
  let dragging = false;

  function fmt(t) {
    if (!Number.isFinite(t) || t < 0) t = 0;
    const s = Math.floor(t % 60);
    const m = Math.floor(t / 60) % 60;
    const h = Math.floor(t / 3600);
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
             : `${m}:${String(s).padStart(2, "0")}`;
  }
  function dur() { return video.duration || Number(root.dataset.duration) || 0; }
  function flash(msg) {
    flashEl.textContent = msg;
    flashEl.classList.add("on");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => flashEl.classList.remove("on"), 700);
  }
  function setSpeed(rate) {
    const nearest = SPEEDS.reduce((a, b) => Math.abs(b - rate) < Math.abs(a - rate) ? b : a);
    video.playbackRate = nearest;
    speedEl.textContent = nearest === 1 ? "1×" : nearest + "×";
    localStorage.setItem(LS_SPEED, String(nearest));
  }
  function bumpSpeed(dir) {
    const i = SPEEDS.indexOf(video.playbackRate);
    const next = SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? 3 : i) + dir))];
    setSpeed(next);
    flash(next + "×");
  }
  function seek(t, label) {
    const d = dur();
    video.currentTime = Math.max(0, Math.min(d || t, t));
    if (label) flash(label);
    paint();
  }
  function skip(delta) { seek(video.currentTime + delta, (delta > 0 ? "+" : "") + delta + "s"); }
  function togglePlay() {
    if (video.paused) video.play(); else video.pause();
  }
  function savePos() {
    const d = dur();
    const t = video.currentTime;
    if (!d || t < 5 || t > d - 5) {
      localStorage.removeItem(LS_POS);
      return;
    }
    localStorage.setItem(LS_POS, String(Math.floor(t)));
  }
  function paint() {
    const d = dur();
    const t = video.currentTime || 0;
    const pct = d ? (t / d) * 100 : 0;
    fill.style.width = pct + "%";
    knob.style.left = pct + "%";
    let end = 0;
    if (video.buffered && video.buffered.length) end = video.buffered.end(video.buffered.length - 1);
    buf.style.width = d ? (end / d) * 100 + "%" : "0";
    timeEl.textContent = showRemaining && d
      ? `-${fmt(d - t)} / ${fmt(d)}`
      : `${fmt(t)} / ${fmt(d)}`;
    const shotFigs = document.querySelectorAll(".shots figure");
    let current = -1;
    shotFigs.forEach((fig, i) => {
      const st = Number(fig.dataset.start || fig.dataset.t);
      if (!Number.isFinite(st)) return;
      const nxt = shotFigs[i + 1]
        ? Number(shotFigs[i + 1].dataset.start || shotFigs[i + 1].dataset.t)
        : Infinity;
      const on = t >= st && t < nxt;
      fig.classList.toggle("now", on);
      if (on) current = i;
    });
    ticks.querySelectorAll("i").forEach((el, i) => {
      el.style.opacity = i === current ? "1" : ".55";
    });
    root.classList.toggle("playing", !video.paused);
    root.querySelectorAll("[data-act=play]").forEach((el) => {
      if (el.classList.contains("ytp-bigplay")) {
        el.textContent = "▶";
      } else {
        el.textContent = video.paused ? "▶" : "❚❚";
      }
    });
    const muteBtn = root.querySelector("[data-act=mute]");
    if (muteBtn) muteBtn.textContent = video.muted || video.volume === 0 ? "unmute" : "mute";
  }
  function ratioFromEvent(ev) {
    const r = scrub.getBoundingClientRect();
    return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
  }
  function drawTicks() {
    ticks.innerHTML = "";
    const d = dur();
    if (!d) return;
    for (const s of shots) {
      const i = document.createElement("i");
      i.style.left = (s.t / d) * 100 + "%";
      ticks.appendChild(i);
    }
  }

  const savedSpeed = parseFloat(localStorage.getItem(LS_SPEED) || "1");
  if (savedSpeed) setSpeed(savedSpeed);
  const savedVol = localStorage.getItem(LS_VOL);
  if (savedVol != null) video.volume = Math.max(0, Math.min(1, parseFloat(savedVol)));
  if (localStorage.getItem(LS_THEATER) === "1") document.body.classList.add("theater");

  const saved = parseFloat(localStorage.getItem(LS_POS) || "0");
  if (saved >= 5 && !/[#&]t=/.test(location.hash)) {
    resumeEl.textContent = "Resume " + fmt(saved);
    resumeEl.classList.add("on");
    resumeEl.onclick = () => { seek(saved, "resume"); resumeEl.classList.remove("on"); };
  }

  video.addEventListener("loadedmetadata", () => {
    drawTicks();
    if (!applyHash()) paint();
  });
  window.addEventListener("hashchange", applyHash);
  video.addEventListener("timeupdate", paint);
  video.addEventListener("progress", paint);
  video.addEventListener("play", () => { resumeEl.classList.remove("on"); paint(); });
  video.addEventListener("pause", () => { savePos(); paint(); });
  video.addEventListener("click", togglePlay);
  video.addEventListener("volumechange", () => localStorage.setItem(LS_VOL, String(video.volume)));
  setInterval(savePos, 4000);
  window.addEventListener("pagehide", savePos);

  scrub.addEventListener("pointerdown", (ev) => {
    dragging = true;
    scrub.setPointerCapture(ev.pointerId);
    seek(ratioFromEvent(ev) * dur());
  });
  scrub.addEventListener("pointermove", (ev) => {
    const r = ratioFromEvent(ev);
    tip.style.left = r * 100 + "%";
    tip.textContent = fmt(r * dur());
    if (dragging) seek(r * dur());
  });
  scrub.addEventListener("pointerup", () => { dragging = false; });
  timeEl.addEventListener("click", () => { showRemaining = !showRemaining; paint(); });

  function applyHash() {
    const m = location.hash.match(/[#&]t=([\d.]+)/);
    if (!m) return false;
    seek(parseFloat(m[1]));
    resumeEl.classList.remove("on");
    return true;
  }
  root.querySelectorAll("[data-act=play]").forEach((el) => {
    el.onclick = togglePlay;
  });
  root.querySelector("[data-act=back]").onclick = () => skip(-10);
  root.querySelector("[data-act=fwd]").onclick = () => skip(10);
  root.querySelector("[data-act=slower]").onclick = () => bumpSpeed(-1);
  root.querySelector("[data-act=faster]").onclick = () => bumpSpeed(1);
  root.querySelector("[data-act=mute]").onclick = () => { video.muted = !video.muted; flash(video.muted ? "muted" : "sound"); };
  root.querySelector("[data-act=pip]").onclick = async () => {
    if (document.pictureInPictureElement) await document.exitPictureInPicture();
    else if (document.pictureInPictureEnabled) await video.requestPictureInPicture();
  };
  root.querySelector("[data-act=theater]").onclick = () => {
    document.body.classList.toggle("theater");
    localStorage.setItem(LS_THEATER, document.body.classList.contains("theater") ? "1" : "0");
  };
  root.querySelector("[data-act=fs]").onclick = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else root.requestFullscreen();
  };
  root.querySelector("[data-act=help]").onclick = () => helpEl.classList.toggle("on");

  document.querySelectorAll(".shots figure[data-t]").forEach((fig) => {
    fig.addEventListener("click", (ev) => {
      if (ev.shiftKey || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      ev.preventDefault();
      seek(Number(fig.dataset.t), Number(fig.dataset.t).toFixed(1) + "s");
      video.play();
    });
  });

  function nextShot(dir) {
    const t = video.currentTime;
    const times = shots.map((s) => s.t);
    if (!times.length) return;
    if (dir > 0) {
      const n = times.find((x) => x > t + 0.15);
      if (n != null) seek(n, "next shot");
    } else {
      const prev = [...times].reverse().find((x) => x < t - 0.4);
      seek(prev != null ? prev : 0, "prev shot");
    }
  }

  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    const k = ev.key;
    if (k === "Escape") { helpEl.classList.remove("on"); return; }
    if (k === "?" || (k === "/" && ev.shiftKey)) { helpEl.classList.toggle("on"); ev.preventDefault(); return; }
    const map = {
      " ": togglePlay, k: togglePlay,
      j: () => skip(-10), l: () => skip(10),
      ArrowLeft: () => skip(ev.shiftKey ? -1 : -5),
      ArrowRight: () => skip(ev.shiftKey ? 1 : 5),
      ArrowUp: () => { video.volume = Math.min(1, video.volume + 0.05); flash(Math.round(video.volume * 100) + "%"); },
      ArrowDown: () => { video.volume = Math.max(0, video.volume - 0.05); flash(Math.round(video.volume * 100) + "%"); },
      Home: () => seek(0, "start"),
      End: () => seek(dur(), "end"),
      m: () => { video.muted = !video.muted; flash(video.muted ? "muted" : "sound"); },
      f: () => root.querySelector("[data-act=fs]").click(),
      t: () => root.querySelector("[data-act=theater]").click(),
      i: () => root.querySelector("[data-act=pip]").click(),
      p: () => root.querySelector("[data-act=pip]").click(),
      ",": () => { video.pause(); seek(video.currentTime - 1 / 30); },
      ".": () => { video.pause(); seek(video.currentTime + 1 / 30); },
      "<": () => bumpSpeed(-1),
      ">": () => bumpSpeed(1),
      "[": () => bumpSpeed(-1),
      "]": () => bumpSpeed(1),
      PageUp: () => nextShot(-1),
      PageDown: () => nextShot(1),
    };
    if (k >= "0" && k <= "9") {
      seek(dur() * (Number(k) / 10), k + "0%");
      ev.preventDefault();
      return;
    }
    if (map[k]) { map[k](); ev.preventDefault(); }
  });

  root.addEventListener("mousemove", () => {
    root.classList.add("show-bar");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => root.classList.remove("show-bar"), 2000);
  });

  paint();
})();
