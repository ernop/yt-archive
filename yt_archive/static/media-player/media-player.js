/* media-player: the one video and audio player for all of my projects.

   Put data-media-player on a <video> or <audio> and keep its `controls` attribute, so the page
   still plays without JavaScript; this script swaps the native controls for its own. The media
   element stays the source of truth: page scripts keep reading and setting currentTime on it,
   and everything drawn here follows the element's events. Media that is not one element (camera
   videos on one clock, an embedded YouTube player) plays through a source: an object that acts
   like a media element (Source, attach()).

   Projects keep a copy of this file made by copy-into.sh in the media-player repository, which
   holds SPEC.md (spec and decisions) and README.md (usage and theming). Never edit a copy: change
   the repository's file and copy it again. */
(function (root, factory) {
    'use strict';
    const api = factory(root);
    root.MediaPlayer = api;
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
    'use strict';

    const VERSION = '1.1.1';

    const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
    // Notch intervals in tenths of a second, so divisibility stays in integers: 0.1 s to 12 h.
    const SCALE_STEPS = [1, 2, 5, 10, 20, 50, 100, 150, 300, 600, 1200, 3000, 6000, 9000,
        18000, 36000, 72000, 108000, 216000, 432000];
    const LABEL_GAP_PX = 16;          // clear space between neighbouring notch labels
    const MINOR_MIN_PX = 8;           // unlabeled notches closer together than this are left out
    const MINOR_PER_MAJOR_MAX = 10;
    const JUMP_SECONDS = 15;          // a seek at least this long is a jump that Backspace undoes
    const RESUME_MIN_SECONDS = 120;   // shorter media always start at the beginning
    const RESUME_EDGE_SECONDS = 5;    // this close to either end counts as not started, or finished
    const RESUME_SAVE_MS = 5000;
    const FALLBACK_FPS = 30;          // frame step for a video that has not shown two frames yet
    const IDLE_MS = 2500;             // full screen hides the controls after this long without input
    const FLASH_MS = 900;
    const SNAP_PX = 6;                // a click this close to a marker lands on the marker
    const VOLUME_STEP = 0.05;
    const STORE_PREFIX = 'media-player:';

    const MEDIA_ERRORS = {
        1: 'loading was stopped',
        2: 'a network error broke off the download',
        3: 'the browser could not decode it',
        4: 'the browser does not support this format, or the file could not be loaded',
    };

    const ICONS = {
        play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15L20 12z"/></svg>',
        pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h4.2v14H6zM13.8 5H18v14h-4.2z"/></svg>',
        volume: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9h4l5-4v14l-5-4H3z"/>'
            + '<path class="mp-stroke" d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9.2 9.2 0 0 1 0 13"/></svg>',
        muted: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9h4l5-4v14l-5-4H3z"/>'
            + '<path class="mp-stroke" d="M15.5 9.5l5 5M20.5 9.5l-5 5"/></svg>',
        fullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="mp-stroke" d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/></svg>',
        windowed: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="mp-stroke" d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/></svg>',
        pip: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="mp-stroke" d="M3 5h18v14H3z"/><path d="M12 11h7v6h-7z"/></svg>',
        previous: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h2.6v14H5zM20 5v14L9.2 12z"/></svg>',
        next: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16.4 5H19v14h-2.6zM4 5v14l10.8-7z"/></svg>',
        camera: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="mp-stroke" d="M3 8h4l2-3h6l2 3h4v11H3z"/>'
            + '<circle class="mp-stroke" cx="12" cy="13" r="3.5"/></svg>',
        wide: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="mp-stroke" d="M2 6h20v12H2z'
            + 'M6 12h12M6 12l2.5-2.5M6 12l2.5 2.5M18 12l-2.5-2.5M18 12l-2.5 2.5"/></svg>',
    };

    // ---------------------------------------------------------------- pure helpers

    function clamp(value, low, high) {
        return Math.min(Math.max(value, low), high);
    }

    /** "m:ss", or "h:mm:ss" when `long`; `tenths` adds ".d". Negative and non-finite read as 0. */
    function formatTime(seconds, long, tenths) {
        const t = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
        const deciseconds = Math.floor(t * 10 + 1e-6);
        const whole = tenths ? Math.floor(deciseconds / 10) : Math.floor(t + 1e-6);
        const ss = String(whole % 60).padStart(2, '0');
        const m = Math.floor(whole / 60) % 60;
        const h = Math.floor(whole / 3600);
        const fraction = tenths ? `.${deciseconds % 10}` : '';
        return long || h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}${fraction}` : `${m}:${ss}${fraction}`;
    }

    /** "m:ss.mmm": the exact position a frame step landed on. */
    function formatPrecise(seconds, long) {
        const t = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
        const millis = Math.floor(t * 1000 + 1e-6);
        return `${formatTime(Math.floor(millis / 1000), long)}.${String(millis % 1000).padStart(3, '0')}`;
    }

    /** Seconds from "93.5", "90s", "1:33", "1:02:03" or "1h2m3s"; null when unreadable. */
    function parseTime(text) {
        const value = String(text == null ? '' : text).trim();
        let match = /^(\d+(?:\.\d+)?)s?$/.exec(value);
        if (match) {
            return Number(match[1]);
        }
        match = /^(?:(\d+):)?(\d+):(\d{1,2}(?:\.\d+)?)$/.exec(value);
        if (match) {
            return Number(match[1] || 0) * 3600 + Number(match[2]) * 60 + Number(match[3]);
        }
        match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$/.exec(value);
        if (match && value) {
            return Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0);
        }
        return null;
    }

    /** The start time in a page address's fragment (#t=93.5, #t=1:33, #t=1h2m3s; Media Fragments'
        npt: prefix and ",end" suffix are accepted and the end ignored): null when there is no t=,
        NaN when there is one that cannot be read. */
    function timeFromHash(hash) {
        const match = /(?:^#|&)t=([^&]*)/.exec(hash || '');
        if (!match) {
            return null;
        }
        let text;
        try {
            text = decodeURIComponent(match[1]);
        } catch (error) {
            if (error instanceof URIError) {
                return NaN;
            }
            throw error;
        }
        const seconds = parseTime(text.replace(/^npt:/, '').split(',')[0]);
        return seconds === null ? NaN : seconds;
    }

    /** Notch spacing for a scale `widthPx` wide showing `duration` seconds whose longest label is
        `labelPx` wide: labeled notches as close as the labels allow, unlabeled notches between them
        when they fit. In seconds; minor is 0 when no unlabeled notches fit. */
    function chooseScale(duration, widthPx, labelPx) {
        if (!(duration > 0) || !(widthPx > 0)) {
            return null;
        }
        const pxPerStep = widthPx / (duration * 10);
        const need = labelPx + LABEL_GAP_PX;
        const major = SCALE_STEPS.find((step) => step * pxPerStep >= need) || SCALE_STEPS[SCALE_STEPS.length - 1];
        const minor = SCALE_STEPS.find((step) => step < major && major % step === 0
            && step * pxPerStep >= MINOR_MIN_PX && major / step <= MINOR_PER_MAJOR_MAX) || 0;
        return { major: major / 10, minor: minor / 10 };
    }

    /** Markers as [{t, label, kind, id, color}] in time order; `kind` names one of the marker kinds
        ('' when the page gave none), `id` is the page's own, passed back in events, and `color`
        ('' for its kind's) is any CSS color. A marker without a time of 0 or more is a page bug. */
    function normalizeMarkers(list) {
        if (list == null) {
            return [];
        }
        if (!Array.isArray(list)) {
            throw new TypeError('markers must be an array of {t, label}');
        }
        return list.map(function (marker, index) {
            const t = Number(marker && marker.t);
            if (!Number.isFinite(t) || t < 0) {
                throw new TypeError(`marker ${index} has no time (t)`);
            }
            return {
                t,
                label: marker.label == null ? '' : String(marker.label),
                kind: marker.kind == null ? '' : String(marker.kind),
                id: marker.id === undefined ? null : marker.id,
                color: marker.color == null ? '' : String(marker.color),
            };
        }).sort((a, b) => a.t - b.t);
    }

    /** Marker kinds as [{kind, label, color}] in the order given, which is their order of
        importance: the first is drawn on top. `color` is any CSS color, custom properties included. */
    function normalizeKinds(list) {
        if (list == null) {
            return [];
        }
        if (!Array.isArray(list)) {
            throw new TypeError('marker kinds must be an array of {kind, label, color}');
        }
        const seen = new Set();
        return list.map(function (entry, index) {
            const kind = entry && entry.kind;
            if (typeof kind !== 'string' || !kind || seen.has(kind)) {
                throw new TypeError(`marker kind ${index} needs a name of its own (kind)`);
            }
            if (!entry.label) {
                throw new TypeError(`marker kind "${kind}" has no label`);
            }
            seen.add(kind);
            return { kind, label: String(entry.label), color: entry.color ? String(entry.color) : '' };
        });
    }

    /** Index of the last of the ascending `times` at or before `t`; -1 when none is. */
    function lastAtOrBefore(times, t) {
        let low = 0;
        let high = times.length - 1;
        let found = -1;
        while (low <= high) {
            const middle = (low + high) >> 1;
            if (times[middle] <= t) {
                found = middle;
                low = middle + 1;
            } else {
                high = middle - 1;
            }
        }
        return found;
    }

    /** Where a half jump from `t` lands: back (direction -1) to half the time played; forward to as
        far before the end as `t` is after the start, or past the middle, halfway to the end. */
    function halfJumpTarget(t, duration, direction) {
        if (direction < 0) {
            return t / 2;
        }
        return t < duration / 2 ? duration - t : t + (duration - t) / 2;
    }

    /** The seconds a player has skip buttons for, smallest first: a list or "5 30". Without any
        usable amount, 5 and 30. */
    function skipAmounts(value) {
        const list = Array.isArray(value) ? value : String(value ?? '').split(/[\s,]+/);
        const amounts = [...new Set(list.map(Number).filter((n) => Number.isFinite(n) && n > 0))];
        return amounts.length ? amounts.sort((a, b) => a - b) : [5, 30];
    }

    /** The file name for a frame saved at `seconds` into `title`: "Title 1-02.345.png". */
    function frameFileName(title, seconds, long) {
        const base = String(title || '').replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_').trim() || 'frame';
        return `${base} ${formatPrecise(seconds, long).replace(/:/g, '-')}.png`;
    }

    /** The marker after `t` (direction 1) or before it (-1). Going back passes over a marker less
        than a second behind, so pressing again keeps moving back while the media plays. */
    function adjacentMarker(markers, t, direction) {
        if (direction > 0) {
            return markers.find((marker) => marker.t > t + 0.25) || null;
        }
        for (let i = markers.length - 1; i >= 0; i -= 1) {
            if (markers[i].t < t - 1) {
                return markers[i];
            }
        }
        return null;
    }

    /** The next speed in SPEEDS above (direction 1) or below (-1) `rate`, which may sit between
        two of them; the slowest and fastest stay put. */
    function stepSpeed(rate, direction) {
        if (direction > 0) {
            return SPEEDS.find((speed) => speed > rate + 1e-9) || SPEEDS[SPEEDS.length - 1];
        }
        for (let i = SPEEDS.length - 1; i >= 0; i -= 1) {
            if (SPEEDS[i] < rate - 1e-9) {
                return SPEEDS[i];
            }
        }
        return SPEEDS[0];
    }

    function formatSpeed(rate) {
        return `${Number(rate.toFixed(2))}x`;
    }

    function formatSkip(seconds) {
        const amount = Math.abs(seconds);
        const sign = seconds < 0 ? '-' : '+';
        return amount >= 60 && amount % 60 === 0 ? `${sign}${amount / 60} min` : `${sign}${amount} s`;
    }

    // ---------------------------------------------------------------- keys

    // One table drives the key handler and the keys panel. Scope 'page': the player takes the key
    // wherever focus is, unless the focused element needs the key itself (see ownsKey). Scope
    // 'focus': only while focus is inside the player, because the page needs these keys for
    // scrolling. `needs` limits an entry to players that can do all of those things, `without` to
    // players that cannot (CAPABILITIES, Player.capabilities). Letters are matched lowercase, with
    // Shift as its own flag, so Shift+J and J are different keys. A `once` entry ignores the
    // repeats of a held key: holding S saves one frame, holding Space does not flicker.
    const CAPABILITIES = ['video', 'captions', 'pip', 'markers', 'items', 'wide', 'sound', 'frames'];
    const letter = (e, keys, shift) => keys.includes(e.key) && e.key.length === 1 && e.shiftKey === shift;
    const KEYS = [
        { keys: 'Space, K', once: true, help: 'Play or pause', scope: 'page',
            match: (e) => e.key === ' ' || letter(e, 'k', false), run: (p) => p.toggle() },
        { keys: 'Left, Right', help: 'Back or forward 5 s (Shift: 1 s, Ctrl: 1 min)', scope: 'page',
            match: (e) => e.key === 'ArrowLeft' || e.key === 'ArrowRight',
            run: (p, e) => p.skip((e.key === 'ArrowLeft' ? -1 : 1) * (e.ctrlKey ? 60 : e.shiftKey ? 1 : 5)) },
        { keys: 'J, L', help: 'Back or forward 10 s', scope: 'page',
            match: (e) => letter(e, 'jl', false), run: (p, e) => p.skip(e.key === 'j' ? -10 : 10) },
        { keys: 'Shift+J', help: 'Back to half the time played', scope: 'page',
            match: (e) => letter(e, 'j', true), run: (p) => p.halfJump(-1) },
        { keys: 'Shift+L', help: 'Forward to as far before the end as you are after the start; past the middle, halfway to the end',
            scope: 'page', match: (e) => letter(e, 'l', true), run: (p) => p.halfJump(1) },
        { keys: '0 to 9', help: 'Jump to 0% to 90% of the way through', scope: 'page',
            match: (e) => /^[0-9]$/.test(e.key), run: (p, e) => p.seekFraction(Number(e.key) / 10) },
        { keys: 'Backspace', once: true, help: 'Back to where the last jump left; again to return', scope: 'page',
            match: (e) => e.key === 'Backspace', run: (p) => p.back() },
        { keys: 'PageUp, PageDown', help: (p) => `Previous or next ${p.markerNames()}`, scope: 'focus', needs: ['markers'],
            match: (e) => e.key === 'PageUp' || e.key === 'PageDown',
            run: (p, e) => p.seekMarker(e.key === 'PageUp' ? -1 : 1) },
        { keys: 'PageUp, PageDown', help: 'Back or forward 1 min', scope: 'focus', without: ['markers'],
            match: (e) => e.key === 'PageUp' || e.key === 'PageDown',
            run: (p, e) => p.skip(e.key === 'PageUp' ? -60 : 60) },
        { keys: 'Home, End', help: 'Start or end', scope: 'focus',
            match: (e) => e.key === 'Home' || e.key === 'End',
            run: (p, e) => p.seek(e.key === 'Home' ? 0 : p.duration(), e.key === 'Home' ? 'Start' : 'End',
                e.key === 'Home' ? 'start' : 'end') },
        { keys: 'Shift+P, Shift+N', once: true, help: (p) => `Previous or next ${p.items.label.toLowerCase()}`, scope: 'page', needs: ['items'],
            match: (e) => letter(e, 'pn', true), run: (p, e) => p.stepItem(e.key === 'p' ? -1 : 1) },
        { keys: ', .', help: 'Previous or next frame (pauses)', scope: 'page', needs: ['video'],
            match: (e) => e.key === ',' || e.key === '.', run: (p, e) => p.stepFrame(e.key === ',' ? -1 : 1) },
        { keys: '< >, [ ]', help: 'Slower or faster', scope: 'page',
            match: (e) => e.key.length === 1 && '<>[]'.includes(e.key),
            run: (p, e) => p.bumpSpeed(e.key === '<' || e.key === '[' ? -1 : 1) },
        { keys: 'Up, Down', help: 'Volume up or down 5%', scope: 'focus', needs: ['sound'],
            match: (e) => e.key === 'ArrowUp' || e.key === 'ArrowDown',
            run: (p, e) => p.nudgeVolume(e.key === 'ArrowUp' ? VOLUME_STEP : -VOLUME_STEP) },
        { keys: 'M', once: true, help: 'Mute or unmute', scope: 'page', needs: ['sound'],
            match: (e) => letter(e, 'm', false), run: (p) => p.toggleMute() },
        { keys: 'C', once: true, help: 'Captions on or off', scope: 'page', needs: ['video', 'captions'],
            match: (e) => letter(e, 'c', false), run: (p) => p.toggleCaptions() },
        { keys: 'S', once: true, help: 'Save this frame as a picture', scope: 'page', needs: ['video', 'frames'],
            match: (e) => letter(e, 's', false), run: (p) => p.saveFrame() },
        { keys: 'T', once: true, help: 'Wide page or normal width', scope: 'page', needs: ['wide'],
            match: (e) => letter(e, 't', false), run: (p) => p.toggleWide() },
        { keys: 'F, double-click', once: true, help: 'Full screen', scope: 'page', needs: ['video'],
            match: (e) => letter(e, 'f', false), run: (p) => p.toggleFullscreen() },
        { keys: 'I, P', once: true, help: 'Picture in picture', scope: 'page', needs: ['video', 'pip'],
            match: (e) => letter(e, 'ip', false), run: (p) => p.togglePip() },
        { keys: '?', once: true, help: 'Show or hide these keys', scope: 'page',
            match: (e) => e.key === '?', run: (p) => p.toggleHelp() },
        { keys: 'Esc', once: true, help: 'Close these keys', scope: 'page',
            match: (e, p) => e.key === 'Escape' && !p.help.hidden, run: (p) => p.toggleHelp(false) },
    ];

    /** Whether a KEYS entry applies to a player with `caps` (true or false for each of
        CAPABILITIES), with focus inside the player or not. */
    function keyApplies(entry, caps, focused) {
        if ((entry.needs || []).some((cap) => !caps[cap]) || (entry.without || []).some((cap) => caps[cap])) {
            return false;
        }
        return entry.scope === 'page' || focused;
    }

    // Keys the focused element needs for itself, so no player takes them: everything while typing,
    // Space on anything Space presses, and the arrow-like keys on widgets that move with them.
    const TYPING = 'textarea, select, [contenteditable]:not([contenteditable="false"]), input:not([type="range"])'
        + ':not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"])'
        + ':not([type="reset"]):not([type="color"]):not([type="file"]):not([type="image"])';
    const PRESSABLE = 'button, a[href], summary, input, [role="button"], [role="checkbox"], [role="switch"], '
        + '[role="tab"], [role="menuitem"], [role="option"], [role="link"]';
    const ARROW_WIDGET = 'input[type="range"], input[type="radio"], [role="slider"], [role="tablist"], '
        + '[role="radiogroup"], [role="listbox"], [role="menu"], [role="menubar"], [role="grid"], '
        + '[role="tree"], [role="treegrid"], [role="spinbutton"], [role="scrollbar"]';
    const NAV_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']);

    function ownsKey(target, key) {
        return Boolean(target.closest(TYPING)
            || (key === ' ' && target.closest(PRESSABLE))
            || (NAV_KEYS.has(key) && target.closest(ARROW_WIDGET)));
    }

    // ---------------------------------------------------------------- browser storage

    // Remembered speed, volume and positions. A browser that blocks storage still plays; the keys
    // panel says that nothing is remembered.
    let storageProblem = null;

    function storageCall(action) {
        try {
            return action(root.localStorage);
        } catch (error) {
            if (error instanceof DOMException) {
                storageProblem = storageProblem || error;
                return null;
            }
            throw error;
        }
    }

    const store = {
        get: (key) => storageCall((storage) => storage.getItem(STORE_PREFIX + key)),
        set: (key, value) => storageCall((storage) => storage.setItem(STORE_PREFIX + key, value)),
        remove: (key) => storageCall((storage) => storage.removeItem(STORE_PREFIX + key)),
    };

    // ---------------------------------------------------------------- DOM helpers

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text != null) {
            node.textContent = text;
        }
        return node;
    }

    function textButton(className, text, label) {
        const node = el('button', `mp-btn ${className}`, text);
        node.type = 'button';
        node.setAttribute('aria-label', label);
        node.title = label;
        return node;
    }

    function iconButton(className, icon, label) {
        const node = textButton(className, null, label);
        node.innerHTML = ICONS[icon];
        return node;
    }

    function setText(node, text) {
        if (node.textContent !== text) {
            node.textContent = text;
        }
    }

    function setLabel(node, label) {
        if (node.getAttribute('aria-label') !== label) {
            node.setAttribute('aria-label', label);
            node.title = label;
        }
    }

    function defaultResumeKey(media) {
        const source = media.querySelector('source[src]');
        const src = media.getAttribute('src') || (source && source.getAttribute('src'));
        if (!src) {
            return null;
        }
        const url = new URL(src, root.location.href);
        return url.protocol === 'blob:' || url.protocol === 'data:' ? null : url.pathname;
    }

    // ---------------------------------------------------------------- the player

    const byRoot = new WeakMap();
    const byMedia = new WeakMap();
    const live = new Set();
    let active = null;
    let hashOwner = null;
    let installed = false;

    class Player {
        constructor(media, options) {
            this.media = media;
            this.isElement = typeof Element !== 'undefined' && media instanceof Element;
            if (!this.isElement && !(options.stage instanceof Element)) {
                throw new TypeError('a source needs a stage: the page element that shows it');
            }
            if ((options.kind !== undefined || !this.isElement) && options.kind !== 'video' && options.kind !== 'audio') {
                throw new TypeError(`kind must be "video" or "audio", not ${JSON.stringify(options.kind)}`);
            }
            // The element the player is built around: the media element, or the stage of a source.
            this.element = this.isElement ? media : options.stage;
            this.isVideo = options.kind ? options.kind === 'video' : media.tagName === 'VIDEO';
            this.kind = this.isVideo ? 'video' : 'audio';
            this.markersLabel = options.markersLabel || 'Marker';
            this.kinds = normalizeKinds(options.markerKinds);
            this.hiddenKinds = new Set();
            this.markers = this.checkKinds(normalizeMarkers(options.markers));
            this.shown = [];
            this.shownTimes = [];
            this.markerSpans = [];
            this.currentMarker = -1;
            this.title = options.title || '';
            this.durationHint = Number(options.duration) > 0 ? Number(options.duration) : 0;
            this.frameSeconds = Number(options.fps) > 0 ? 1 / Number(options.fps) : 0;
            this.measuredFrame = 0;
            this.sound = options.sound !== false;
            // The page's own level, before any remembered volume: unmuting a silent player returns to it.
            this.pageVolume = media.volume || 1;
            this.wide = Boolean(options.wide);
            this.halfButtons = Boolean(options.halfJumps);
            this.skipSeconds = skipAmounts(options.skips);
            this.frameSaver = typeof options.saveFrame === 'function' ? options.saveFrame : null;
            this.items = null;
            // A key the page chose stays; one taken from the file's address follows the file.
            this.ownResumeKey = !options.resumeKey && options.resume !== false && this.isElement;
            this.resumeKey = options.resume === false ? null
                : (options.resumeKey || (this.isElement ? defaultResumeKey(media) : null));
            this.resumeAt = 0;
            this.started = false;
            this.dragging = false;
            this.dragTime = 0;
            this.dragFrom = 0;
            this.jumpFrom = null;
            this.settled = media.currentTime;
            this.trackWidth = 0;
            this.scaleKey = '';
            this.frame = 0;
            if (!hashOwner) {
                hashOwner = this;
            }
            this.build();
            this.restoreSettings();
            this.bind();
            this.renderMarkers();
            this.paintAll();
            if (media.readyState >= 1) {
                this.afterMetadata();
            }
            // A file that failed before this script ran sent its error event to nobody.
            if (media.error || (this.isElement && media.networkState === 3 && media.querySelector('source'))) {
                this.showError();
            }
        }

        /** The markers, after checking each names one of the kinds (or none, when there are no kinds). */
        checkKinds(markers) {
            const known = new Set(this.kinds.map((entry) => entry.kind));
            markers.forEach(function (marker) {
                if (known.size ? !known.has(marker.kind) : marker.kind) {
                    throw new TypeError(`the marker at ${marker.t} s is of kind "${marker.kind}", `
                        + `which is not among the marker kinds (${Array.from(known).join(', ') || 'none given'})`);
                }
            });
            return markers;
        }

        // ------------------------------------------------------------ building

        build() {
            const media = this.media;
            const rootEl = el('div', `mp mp-${this.kind}`);
            rootEl.tabIndex = -1;
            rootEl.setAttribute('role', 'group');
            rootEl.setAttribute('aria-label', this.title || (this.isVideo ? 'Video player' : 'Audio player'));
            this.element.before(rootEl);
            if (this.isElement) {
                media.controls = false;
            } else {
                this.element.classList.add('mp-source');
            }
            this.root = rootEl;

            this.flashEl = el('div', this.isVideo ? 'mp-flash' : 'mp-flash mp-grow');
            this.flashEl.setAttribute('aria-live', 'polite');
            this.errorEl = el('div', 'mp-error');
            this.errorEl.setAttribute('role', 'alert');
            this.errorEl.hidden = true;

            if (this.isVideo) {
                this.stage = el('div', 'mp-stage');
                this.bigPlay = el('button', 'mp-bigplay');
                this.bigPlay.type = 'button';
                this.bigPlay.innerHTML = ICONS.play;
                this.bigPlay.setAttribute('aria-label', 'Play');
                this.stage.append(this.element, this.bigPlay, this.flashEl);
                rootEl.append(this.stage);
            } else {
                rootEl.append(this.element);
            }
            const bar = el('div', 'mp-bar');
            bar.append(this.buildScale(), this.buildControls(), this.buildHelp());
            rootEl.append(this.errorEl, bar);
        }

        buildScale() {
            const scale = el('div', 'mp-scale');
            this.elapsedEl = el('output', 'mp-time mp-elapsed', '0:00');
            this.elapsedEl.title = 'Played';
            const body = el('div', 'mp-scale-body');
            this.track = el('div', 'mp-track');
            this.track.tabIndex = 0;
            this.track.setAttribute('role', 'slider');
            this.track.setAttribute('aria-label', 'Position');
            this.track.setAttribute('aria-valuemin', '0');
            this.bufferedEl = el('div', 'mp-buffered');
            this.fill = el('div', 'mp-fill');
            this.markersEl = el('div', 'mp-markers');
            this.thumb = el('div', 'mp-thumb');
            this.tip = el('div', 'mp-tip');
            this.tip.hidden = true;
            this.track.append(this.bufferedEl, this.fill, this.markersEl, this.thumb, this.tip);
            this.ticks = el('div', 'mp-ticks');
            this.ticks.setAttribute('aria-hidden', 'true');
            body.append(this.track, this.ticks);
            this.remainingEl = el('output', 'mp-time mp-remaining', '-0:00');
            this.remainingEl.title = 'Left';
            scale.append(this.elapsedEl, body, this.remainingEl);
            return scale;
        }

        buildControls() {
            const controls = el('div', 'mp-controls');
            this.playBtn = iconButton('mp-play', 'play', 'Play (Space)');
            this.prevItemBtn = iconButton('mp-item', 'previous', 'Previous (Shift+P)');
            this.nextItemBtn = iconButton('mp-item', 'next', 'Next (Shift+N)');
            this.prevItemBtn.hidden = true;
            this.nextItemBtn.hidden = true;

            const skips = el('span', 'mp-group mp-skips');
            const amounts = this.skipSeconds;
            // Narrow players keep only the smallest pair (see the narrow players CSS).
            [...amounts].reverse().map((n) => -n).concat(amounts).forEach((seconds) => {
                const far = Math.abs(seconds) !== amounts[0] ? ' mp-skip-far' : '';
                const label = seconds < 0 ? `Back ${-seconds} seconds` : `Forward ${seconds} seconds`;
                const button = textButton(`mp-skip${far}`, (seconds < 0 ? '-' : '+') + Math.abs(seconds), label);
                button.addEventListener('click', () => this.skip(seconds));
                skips.append(button);
            });

            const half = el('span', 'mp-group mp-half');
            this.halfBackBtn = textButton('mp-half-back', 'Half back', 'Back to half the time played (Shift+J)');
            this.halfForwardBtn = textButton('mp-half-forward', 'Half fwd',
                'Forward to as far before the end as you are after the start; past the middle, halfway to the end (Shift+L)');
            half.append(this.halfBackBtn, this.halfForwardBtn);
            half.hidden = !this.halfButtons;

            const speed = el('span', 'mp-group mp-speed');
            this.slowerBtn = textButton('mp-step', '-', 'Slower (<)');
            this.speedEl = el('output', 'mp-value', '1x');
            this.speedEl.title = 'Speed';
            this.fasterBtn = textButton('mp-step', '+', 'Faster (>)');
            speed.append(el('span', 'mp-label', 'Speed'), this.slowerBtn, this.speedEl, this.fasterBtn);

            const volume = el('span', 'mp-group mp-volume');
            this.muteBtn = iconButton('mp-mute', 'volume', 'Mute (M)');
            const range = el('span', 'mp-range');
            this.volumeInput = el('input');
            this.volumeInput.type = 'range';
            this.volumeInput.min = '0';
            this.volumeInput.max = '100';
            this.volumeInput.step = '1';
            this.volumeInput.setAttribute('aria-label', 'Volume');
            const notches = el('span', 'mp-range-scale');
            notches.setAttribute('aria-hidden', 'true');
            for (let value = 0; value <= 100; value += 10) {
                const notch = el('i', value % 50 === 0 ? 'is-major' : '');
                notch.style.left = `${value}%`;
                notches.append(notch);
                if (value % 50 === 0) {
                    const label = el('b', value === 0 ? 'is-start' : value === 100 ? 'is-end' : '', String(value));
                    label.style.left = `${value}%`;
                    notches.append(label);
                }
            }
            range.append(this.volumeInput, notches);
            this.volumeEl = el('output', 'mp-value', '100');
            this.volumeEl.title = 'Volume';
            this.unitEl = el('span', 'mp-unit', '%');
            volume.append(this.muteBtn, range, this.volumeEl, this.unitEl);
            volume.hidden = !this.sound;

            // One toggle per kind of marker on the scale; filled by renderLegend().
            this.legend = el('span', 'mp-legend');
            this.legend.hidden = true;

            this.resumeBtn = textButton('mp-resume', '', 'Resume where you left off');
            this.resumeBtn.hidden = true;
            this.ccBtn = textButton('mp-cc', 'CC', 'Captions (C)');
            this.ccBtn.hidden = true;
            this.frameBtn = iconButton('mp-frame', 'camera', 'Save this frame as a picture (S)');
            this.frameBtn.hidden = !this.isVideo || !(this.isElement || this.frameSaver);
            this.wideBtn = iconButton('mp-wide', 'wide', 'Wide page (T)');
            this.wideBtn.hidden = !this.wide;
            this.pipBtn = iconButton('mp-pip', 'pip', 'Picture in picture (I)');
            this.pipBtn.hidden = true;
            this.fullBtn = iconButton('mp-full', 'fullscreen', 'Full screen (F)');
            this.fullBtn.hidden = !this.isVideo;
            this.helpBtn = textButton('mp-keys', '?', 'Keyboard keys (?)');
            this.helpBtn.setAttribute('aria-expanded', 'false');

            this.grow = this.isVideo ? el('span', 'mp-grow') : this.flashEl;
            controls.append(this.prevItemBtn, this.playBtn, this.nextItemBtn, skips, half, speed, volume, this.legend,
                this.grow, this.resumeBtn, this.ccBtn, this.frameBtn, this.wideBtn, this.pipBtn, this.fullBtn, this.helpBtn);
            return controls;
        }

        buildHelp() {
            this.help = el('div', 'mp-help');
            this.help.hidden = true;
            this.help.setAttribute('role', 'dialog');
            this.help.setAttribute('aria-label', 'Keyboard keys');
            const head = el('div', 'mp-help-head');
            this.helpClose = textButton('mp-help-close', 'Close', 'Close (Esc)');
            head.append(el('strong', 'mp-help-title', 'Keyboard'), this.helpClose);
            this.helpList = el('div', 'mp-help-list');
            this.helpLegend = el('div', 'mp-help-legend');
            this.helpNote = el('p', 'mp-help-note');
            this.help.append(head, this.helpList, this.helpLegend, this.helpNote);
            return this.help;
        }

        fillHelp() {
            const rows = KEYS.filter((entry) => this.allows(entry, true)).map((entry) => {
                const help = typeof entry.help === 'function' ? entry.help(this) : entry.help;
                const text = entry.scope === 'focus' ? `${help} *` : help;
                const row = el('div', 'mp-help-row');
                const description = el('span', 'mp-help-text', text);
                description.title = text;
                row.append(el('span', 'mp-help-keys', entry.keys), description);
                return row;
            });
            this.helpList.replaceChildren(...rows);
            const swatch = (className, label, color) => {
                const item = el('span', 'mp-help-swatch-item');
                const sample = el('span', `mp-help-swatch ${className}`);
                if (color) {
                    sample.style.setProperty('--mp-marker-color', color);
                }
                item.append(sample, label);
                return item;
            };
            this.helpLegend.replaceChildren(
                swatch('is-played', 'Played'),
                swatch('is-buffered', 'Downloaded'),
                ...this.presentKinds().map((entry) => swatch('is-marker', entry.label, entry.color)),
                swatch('is-loading', 'Waiting for data'),
            );
            const notes = ['* after clicking the player.'];
            if (storageProblem) {
                notes.push(`Speed, volume and positions are not remembered: ${storageProblem.message}`);
            }
            this.helpNote.textContent = notes.join(' ');
        }

        // ------------------------------------------------------------ wiring

        restoreSettings() {
            const speed = Number(store.get(`speed:${this.kind}`));
            if (SPEEDS.includes(speed)) {
                this.setSpeed(speed);
            }
            const volume = store.get('volume');
            if (this.sound && volume !== null && Number.isFinite(Number(volume))) {
                this.media.volume = clamp(Number(volume), 0, 1);
            }
            const track = this.isVideo ? this.captionTrack() : null;
            if (track && store.get('captions') === '1') {
                track.mode = 'showing';
                this.notify('captions', { showing: true });
            }
            if (this.wide && store.get('wide') === '1') {
                document.documentElement.classList.add('mp-wide');
            }
            this.paintWide();
        }

        bind() {
            const media = this.media;
            const on = (target, type, handler) => target.addEventListener(type, handler);

            on(media, 'play', () => {
                this.started = true;
                active = this;
                this.paintPlayState();
                this.loop();
                this.claimSession();
                this.watchFrames();
                this.poke();
            });
            on(media, 'pause', () => {
                this.paintPlayState();
                this.paint();
                this.savePosition();
                this.poke();
            });
            on(media, 'ended', () => {
                this.paintPlayState();
                this.savePosition();
            });
            on(media, 'timeupdate', () => {
                if (!media.seeking) {
                    this.settled = media.currentTime;
                }
                if (media.paused) {
                    this.paint();
                }
            });
            on(media, 'seeking', () => this.noteSeek());
            on(media, 'seeked', () => {
                this.paint();
                this.paintBuffered();
                this.sessionPosition();
            });
            ['loadedmetadata', 'durationchange'].forEach((type) => on(media, type, () => {
                this.renderScale();
                this.renderMarkers();
                this.paintAll();
            }));
            on(media, 'loadedmetadata', () => this.afterMetadata());
            on(media, 'progress', () => this.paintBuffered());
            on(media, 'ratechange', () => this.paintSpeed());
            on(media, 'volumechange', () => this.paintVolume());
            on(media, 'waiting', () => this.root.classList.add('is-waiting'));
            ['playing', 'canplay', 'pause', 'error'].forEach((type) => on(media, type, () => this.root.classList.remove('is-waiting')));
            on(media, 'error', () => this.showError());
            on(media, 'loadstart', () => this.clearError());
            // The page gave the element another file: what was known about the last one is gone.
            on(media, 'emptied', () => {
                this.started = false;
                this.jumpFrom = null;
                this.settled = 0;
                this.resumeBtn.hidden = true;
                if (this.ownResumeKey) {
                    this.resumeKey = defaultResumeKey(media);
                }
                this.renderMarkers();
                this.paintAll();
            });
            const sources = this.isElement ? media.querySelectorAll('source') : [];
            if (sources.length) {
                // With <source> children the element reports nothing; the last source's error means none loaded.
                on(sources[sources.length - 1], 'error', () => this.showError());
            }

            on(this.track, 'pointerdown', (event) => this.pointerDown(event));
            on(this.track, 'pointermove', (event) => this.pointerMove(event));
            on(this.track, 'pointerup', (event) => this.pointerUp(event));
            on(this.track, 'pointercancel', (event) => this.pointerUp(event));
            on(this.track, 'pointerleave', () => {
                if (!this.dragging) {
                    this.tip.hidden = true;
                }
            });

            on(this.playBtn, 'click', () => this.toggle());
            on(this.slowerBtn, 'click', () => this.bumpSpeed(-1));
            on(this.fasterBtn, 'click', () => this.bumpSpeed(1));
            on(this.muteBtn, 'click', () => this.toggleMute());
            on(this.volumeInput, 'input', () => {
                media.volume = Number(this.volumeInput.value) / 100;
                media.muted = media.volume === 0;
            });
            on(this.resumeBtn, 'click', () => this.resume());
            on(this.prevItemBtn, 'click', () => this.stepItem(-1));
            on(this.nextItemBtn, 'click', () => this.stepItem(1));
            on(this.halfBackBtn, 'click', () => this.halfJump(-1));
            on(this.halfForwardBtn, 'click', () => this.halfJump(1));
            on(this.ccBtn, 'click', () => this.toggleCaptions());
            on(this.frameBtn, 'click', () => this.saveFrame());
            on(this.wideBtn, 'click', () => this.toggleWide());
            on(this.pipBtn, 'click', () => this.togglePip());
            on(this.fullBtn, 'click', () => this.toggleFullscreen());
            on(this.helpBtn, 'click', () => this.toggleHelp());
            on(this.helpClose, 'click', () => this.toggleHelp(false));
            if (this.isVideo) {
                on(this.bigPlay, 'click', () => this.play());
                // A source's stage has clicks of its own (a camera to enlarge); only a plain video
                // plays and pauses on a click. A double-click is two clicks first: play toggles twice
                // and ends as it was.
                if (this.isElement) {
                    on(media, 'click', () => this.toggle());
                }
                on(this.element, 'dblclick', () => this.toggleFullscreen());
            }

            // After a mouse click on a button, focus goes to the player itself, so Space next plays
            // or pauses instead of pressing that button again. A keyboard press (detail 0) stays.
            on(this.root, 'click', (event) => {
                if (event.detail > 0 && event.target.closest('button')) {
                    this.root.focus({ preventScroll: true });
                }
            });
            on(this.root, 'pointerdown', () => {
                active = this;
                this.poke();
            });
            on(this.root, 'pointermove', () => this.poke());
            on(this.root, 'focusin', () => {
                active = this;
            });
            on(document, 'fullscreenchange', () => this.paintFullscreen());
            if (document.fonts) {
                // Notch labels measured in a fallback font are too narrow once the page's font arrives.
                on(document.fonts, 'loadingdone', () => {
                    this.scaleKey = '';
                    this.renderScale();
                });
            }
            on(root, 'pagehide', () => this.savePosition());
            setInterval(() => {
                if (!media.paused) {
                    this.savePosition();
                }
            }, RESUME_SAVE_MS);
            new ResizeObserver(() => this.renderScale()).observe(this.ticks);
        }

        // ------------------------------------------------------------ actions

        duration() {
            const d = this.media.duration;
            return Number.isFinite(d) && d > 0 ? d : this.durationHint;
        }

        format(t) {
            const d = this.duration();
            return formatTime(t, d >= 3600, d > 0 && d < 10);
        }

        play() {
            const promise = this.media.play();
            if (promise) {
                promise.catch((error) => {
                    // A pause before playback began rejects play() with AbortError; that is no failure.
                    if (error.name !== 'AbortError') {
                        this.showError(`Cannot play: ${error.message}`);
                    }
                });
            }
        }

        pause() {
            this.media.pause();
        }

        toggle() {
            if (this.media.paused || this.media.ended) {
                this.play();
            } else {
                this.pause();
            }
        }

        /** Moves the playhead to `t` seconds (kept within the media) and shows `label` if given.
            With a `cause` (what the player did: 'skip', 'marker', 'scale' and so on), the seek is
            announced as a media-player:seek event, so the page can follow the player's jumps. */
        seek(t, label, cause) {
            const d = this.duration();
            const from = this.media.currentTime;
            const target = d > 0 ? clamp(t, 0, d) : Math.max(0, t);
            this.media.currentTime = target;
            if (label) {
                this.flash(label);
            }
            this.paint();
            if (cause) {
                this.notify('seek', { from, to: target, cause });
            }
            return target;
        }

        skip(seconds, cause) {
            this.seek(this.media.currentTime + seconds, formatSkip(seconds), cause || 'skip');
        }

        seekFraction(fraction) {
            const d = this.duration();
            if (d > 0) {
                this.seek(d * fraction, `${Math.round(fraction * 100)}%`, 'percent');
            }
        }

        seekMarker(direction) {
            const marker = adjacentMarker(this.shown, this.media.currentTime, direction);
            if (marker) {
                this.seek(marker.t, marker.label || this.kindLabel(marker.kind), 'marker');
            } else if (direction < 0) {
                this.seek(0, 'Start', 'start');
            } else {
                this.flash(`No ${this.markerNames()} after this`);
            }
        }

        halfJump(direction) {
            const d = this.duration();
            if (!(d > 0)) {
                return;
            }
            const target = halfJumpTarget(this.media.currentTime, d, direction);
            this.seek(target, `${direction < 0 ? 'Half back' : 'Half forward'} to ${this.format(target)}`,
                direction < 0 ? 'half-back' : 'half-forward');
        }

        back() {
            if (this.jumpFrom === null) {
                this.flash('No jump to go back from');
                return;
            }
            this.seek(this.jumpFrom, `Back to ${this.format(this.jumpFrom)}`, 'jump-back');
        }

        // Any seek of JUMP_SECONDS or more, whoever made it (keys, a click on the scale, a page
        // script setting currentTime), records where it left from; Backspace returns there.
        noteSeek() {
            if (this.dragging) {
                return;
            }
            const to = this.media.currentTime;
            if (Math.abs(to - this.settled) >= JUMP_SECONDS) {
                this.jumpFrom = this.settled;
            }
            this.settled = to;
        }

        stepFrame(direction) {
            this.media.pause();
            const frame = this.frameSeconds || this.measuredFrame || 1 / FALLBACK_FPS;
            const target = this.seek(this.media.currentTime + direction * frame, null, 'frame');
            this.flash(`${direction < 0 ? 'Previous' : 'Next'} frame ${formatPrecise(target, this.duration() >= 3600)}`);
        }

        /** Saves the picture showing now: the page's saveFrame option gets it (a source's page draws
            the picture itself, so it gets none), or else the browser downloads it as a PNG file. */
        saveFrame() {
            const time = this.media.currentTime;
            const long = this.duration() >= 3600;
            const capture = this.isElement ? this.captureFrame() : Promise.resolve(null);
            capture.then((blob) => {
                if (this.frameSaver) {
                    return this.frameSaver({ time, blob, player: this });
                }
                const name = frameFileName(this.title, time, long);
                const link = el('a');
                link.href = URL.createObjectURL(blob);
                link.download = name;
                document.body.append(link);
                link.click();
                link.remove();
                setTimeout(() => URL.revokeObjectURL(link.href), 0);
                return `Saved ${name}`;
            }).then(
                (message) => this.flash(typeof message === 'string' ? message : `Frame at ${formatPrecise(time, long)} saved`),
                (error) => this.flash(`Frame not saved: ${error.message}`),
            );
        }

        captureFrame() {
            const video = this.media;
            if (!video.videoWidth) {
                return Promise.reject(new Error('no picture is showing yet'));
            }
            return new Promise(function (resolve, reject) {
                const canvas = el('canvas');
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                canvas.getContext('2d').drawImage(video, 0, 0);
                // A video from another site without CORS taints the canvas, and toBlob throws.
                canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('the browser made no picture'))), 'image/png');
            });
        }

        // The frame duration, from the times of frames shown while playing: the smallest gap, since
        // a dropped frame only makes a gap longer.
        watchFrames() {
            const media = this.media;
            if (!this.isVideo || this.frameSeconds || typeof media.requestVideoFrameCallback !== 'function') {
                return;
            }
            let last = null;
            const onFrame = (now, metadata) => {
                if (last !== null) {
                    const gap = metadata.mediaTime - last;
                    if (gap > 0.001 && (!this.measuredFrame || gap < this.measuredFrame)) {
                        this.measuredFrame = gap;
                    }
                }
                last = metadata.mediaTime;
                if (!media.paused) {
                    media.requestVideoFrameCallback(onFrame);
                }
            };
            media.requestVideoFrameCallback(onFrame);
        }

        setSpeed(rate) {
            this.media.defaultPlaybackRate = rate;
            this.media.playbackRate = rate;
        }

        bumpSpeed(direction) {
            this.setSpeed(stepSpeed(this.media.playbackRate, direction));
            this.flash(formatSpeed(this.media.playbackRate));
        }

        toggleMute() {
            const media = this.media;
            if (media.muted || media.volume === 0) {
                media.muted = false;
                if (media.volume === 0) {
                    media.volume = this.pageVolume;
                }
            } else {
                media.muted = true;
            }
            this.flash(media.muted ? 'Muted' : `Volume ${Math.round(media.volume * 100)}%`);
        }

        nudgeVolume(delta) {
            const media = this.media;
            media.muted = false;
            media.volume = clamp(Math.round((media.volume + delta) * 100) / 100, 0, 1);
            this.flash(`Volume ${Math.round(media.volume * 100)}%`);
        }

        captionTrack() {
            return Array.from(this.media.textTracks || []).find((track) => track.kind === 'captions' || track.kind === 'subtitles') || null;
        }

        toggleCaptions() {
            const track = this.captionTrack();
            if (!track) {
                return;
            }
            track.mode = track.mode === 'showing' ? 'hidden' : 'showing';
            const showing = track.mode === 'showing';
            store.set('captions', showing ? '1' : '0');
            this.paintCaptions();
            this.flash(showing ? 'Captions on' : 'Captions off');
            this.notify('captions', { showing });
        }

        pipSupported() {
            return this.isVideo && this.isElement && Boolean(document.pictureInPictureEnabled)
                && !this.media.disablePictureInPicture;
        }

        /** Wide page or normal: the <html> element gets the class mp-wide, and the page's styles say
            what wide means. Only for players made with the wide option. */
        toggleWide(on) {
            if (!this.wide) {
                return;
            }
            const wide = on === undefined ? !document.documentElement.classList.contains('mp-wide') : on;
            document.documentElement.classList.toggle('mp-wide', wide);
            store.set('wide', wide ? '1' : '0');
            live.forEach((player) => player.paintWide());
            this.notify('wide', { wide });
        }

        /** Previous and next items (the parts of a book, the files of a list): `previous` and `next`
            are functions that switch to them, or null where there is none; `label` names an item.
            setItems(null) removes them. */
        setItems(items) {
            this.items = items && (items.previous || items.next)
                ? { previous: items.previous || null, next: items.next || null, label: items.label || 'Item' }
                : null;
            this.paintItems();
            if (active === this) {
                this.claimSession();
            }
        }

        stepItem(direction) {
            const go = this.items && (direction < 0 ? this.items.previous : this.items.next);
            if (go) {
                go();
            } else {
                this.flash(`No ${direction < 0 ? 'previous' : 'next'} ${this.items ? this.items.label.toLowerCase() : 'item'}`);
            }
        }

        /** Shows or hides one kind of marker on the scale; hidden markers are passed over by PageUp
            and PageDown too. */
        toggleKind(kind) {
            const label = this.kindLabel(kind);
            if (this.hiddenKinds.has(kind)) {
                this.hiddenKinds.delete(kind);
                this.flash(`${label}: shown`);
            } else {
                this.hiddenKinds.add(kind);
                this.flash(`${label}: hidden`);
            }
            this.renderMarkers();
            if (!this.help.hidden) {
                this.fillHelp();
            }
        }

        /** The marker kinds in order of importance; one unnamed kind when the page gave none. */
        kindList() {
            return this.kinds.length ? this.kinds : [{ kind: '', label: this.markersLabel, color: '' }];
        }

        /** The kinds that have markers. */
        presentKinds() {
            return this.kindList().filter((entry) => this.markers.some((marker) => marker.kind === entry.kind));
        }

        kindLabel(kind) {
            const entry = this.kindList().find((candidate) => candidate.kind === kind);
            return entry ? entry.label : this.markersLabel;
        }

        /** "chapter", or "todo, note or speaker change": the kinds PageUp and PageDown visit. */
        markerNames() {
            const names = this.presentKinds().filter((entry) => !this.hiddenKinds.has(entry.kind))
                .map((entry) => entry.label.toLowerCase());
            if (!names.length) {
                return this.markersLabel.toLowerCase();
            }
            return names.length > 1 ? `${names.slice(0, -1).join(', ')} or ${names[names.length - 1]}` : names[0];
        }

        /** Sends media-player:<name> from the player's root; it bubbles, so a page can listen anywhere above. */
        notify(name, detail) {
            this.root.dispatchEvent(new CustomEvent(`media-player:${name}`, {
                bubbles: true,
                detail: Object.assign({ player: this }, detail),
            }));
        }

        togglePip() {
            if (!this.pipSupported()) {
                return;
            }
            const action = document.pictureInPictureElement ? document.exitPictureInPicture() : this.media.requestPictureInPicture();
            action.catch((error) => this.flash(`Picture in picture refused: ${error.message}`));
        }

        toggleFullscreen() {
            if (!this.isVideo) {
                return;
            }
            if (document.fullscreenElement === this.root) {
                document.exitFullscreen();
                return;
            }
            this.root.requestFullscreen().catch((error) => this.flash(`Full screen refused: ${error.message}`));
        }

        toggleHelp(show) {
            const open = show === undefined ? this.help.hidden : show;
            if (open) {
                this.fillHelp();
            }
            this.help.hidden = !open;
            this.helpBtn.setAttribute('aria-expanded', String(open));
        }

        resume() {
            this.resumeBtn.hidden = true;
            this.seek(this.resumeAt, `Resumed at ${this.format(this.resumeAt)}`, 'resume');
            this.play();
        }

        /** Gives the player the keys that otherwise scroll the page (Home, End, Up, Down, PageUp,
            PageDown) without scrolling to it, for page elements that drive it, like a list of shots. */
        focus() {
            this.track.focus({ preventScroll: true });
        }

        /** Moves the page's own elements (a clock, a checkbox) into the controls row, after the
            player's controls on the left. */
        addControls(...nodes) {
            this.grow.before(...nodes);
        }

        /** Replaces the markers on the scale. `how` is the name of one marker ("Chapter"), or
            {label, kinds}: kinds as [{kind, label, color}] when the markers are of several kinds. */
        setMarkers(list, how) {
            const options = typeof how === 'string' ? { label: how } : (how || {});
            if (options.label) {
                this.markersLabel = options.label;
            }
            if (options.kinds !== undefined) {
                this.kinds = normalizeKinds(options.kinds);
                this.hiddenKinds.clear();
            }
            this.markers = this.checkKinds(normalizeMarkers(list));
            this.renderMarkers();
        }

        flash(text) {
            this.flashEl.textContent = text;
            this.flashEl.classList.add('is-on');
            clearTimeout(this.flashTimer);
            this.flashTimer = setTimeout(() => this.flashEl.classList.remove('is-on'), FLASH_MS);
        }

        showError(text) {
            let message = text;
            if (!message) {
                const error = this.media.error;
                const reason = error ? (MEDIA_ERRORS[error.code] || `error ${error.code}`)
                    : (this.isElement ? 'none of its sources could be loaded' : 'its source failed');
                message = `Cannot play this file: ${reason}${error && error.message ? ` (${error.message})` : ''}`;
            }
            this.errorEl.textContent = message;
            this.errorEl.hidden = false;
            this.root.classList.add('has-error');
        }

        clearError() {
            this.errorEl.hidden = true;
            this.root.classList.remove('has-error');
        }

        /** What this player can do, one flag for each of CAPABILITIES. */
        capabilities() {
            return {
                video: this.isVideo,
                captions: Boolean(this.captionTrack()),
                pip: this.pipSupported(),
                markers: this.shown.length > 0,
                items: Boolean(this.items),
                wide: this.wide,
                sound: this.sound,
                frames: this.isVideo && (this.isElement || Boolean(this.frameSaver)),
            };
        }

        allows(entry, focused) {
            return keyApplies(entry, this.capabilities(), focused);
        }

        // ------------------------------------------------------------ position memory, address, session

        afterMetadata() {
            if (!this.applyHash(true)) {
                this.offerResume();
            }
        }

        applyHash(onLoad) {
            if (this !== hashOwner) {
                return false;
            }
            const t = timeFromHash(root.location.hash);
            if (t === null) {
                return false;
            }
            if (Number.isNaN(t)) {
                this.flash('Cannot read the time in the address');
                return false;
            }
            this.resumeBtn.hidden = true;
            this.seek(t, onLoad ? null : this.format(t), 'address');
            return true;
        }

        offerResume() {
            if (!this.resumeKey || this.started) {
                return;
            }
            const saved = Number(store.get(`pos:${this.resumeKey}`));
            const d = this.duration();
            if (!(saved >= RESUME_EDGE_SECONDS) || d < RESUME_MIN_SECONDS || saved > d - RESUME_EDGE_SECONDS) {
                return;
            }
            this.resumeAt = saved;
            this.resumeBtn.textContent = `Resume at ${this.format(saved)}`;
            this.resumeBtn.hidden = false;
        }

        savePosition() {
            if (!this.resumeKey || !this.started) {
                return;
            }
            const d = this.duration();
            const t = this.media.currentTime;
            if (d < RESUME_MIN_SECONDS || t < RESUME_EDGE_SECONDS || t > d - RESUME_EDGE_SECONDS) {
                store.remove(`pos:${this.resumeKey}`);
            } else {
                store.set(`pos:${this.resumeKey}`, String(Math.floor(t)));
            }
        }

        claimSession() {
            const session = root.navigator && root.navigator.mediaSession;
            if (!session) {
                return;
            }
            if (typeof root.MediaMetadata === 'function') {
                session.metadata = new root.MediaMetadata({ title: this.title || document.title });
            }
            const items = this.items || {};
            const handlers = {
                play: () => this.play(),
                pause: () => this.pause(),
                seekbackward: (details) => this.skip(-(details.seekOffset || 10), 'media-session'),
                seekforward: (details) => this.skip(details.seekOffset || 10, 'media-session'),
                seekto: (details) => this.seek(details.seekTime, null, 'media-session'),
                // null takes away a handler an earlier player or item list left behind.
                previoustrack: items.previous ? () => this.stepItem(-1) : null,
                nexttrack: items.next ? () => this.stepItem(1) : null,
            };
            Object.keys(handlers).forEach(function (action) {
                try {
                    session.setActionHandler(action, handlers[action]);
                } catch (error) {
                    // setActionHandler throws TypeError for an action this browser does not offer.
                    if (!(error instanceof TypeError)) {
                        throw error;
                    }
                }
            });
            this.sessionPosition();
        }

        sessionPosition() {
            const session = root.navigator && root.navigator.mediaSession;
            const d = this.duration();
            if (!session || typeof session.setPositionState !== 'function' || !(d > 0) || active !== this) {
                return;
            }
            session.setPositionState({
                duration: d,
                playbackRate: this.media.playbackRate || 1,
                position: clamp(this.media.currentTime, 0, d),
            });
        }

        // ------------------------------------------------------------ scale pointer

        timeAt(clientX) {
            const rect = this.track.getBoundingClientRect();
            return clamp((clientX - rect.left) / rect.width, 0, 1) * this.duration();
        }

        markerNear(clientX) {
            const d = this.duration();
            if (!this.shown.length || !(d > 0)) {
                return null;
            }
            const rect = this.track.getBoundingClientRect();
            let best = null;
            let bestPx = SNAP_PX + 1;
            for (const marker of this.shown) {
                const px = Math.abs(rect.left + (marker.t / d) * rect.width - clientX);
                if (px < bestPx) {
                    best = marker;
                    bestPx = px;
                }
            }
            return best;
        }

        pointerDown(event) {
            if (event.button !== 0 || !(this.duration() > 0)) {
                return;
            }
            event.preventDefault();
            this.track.focus({ preventScroll: true });
            this.track.setPointerCapture(event.pointerId);
            this.dragging = true;
            this.dragFrom = this.media.currentTime;
            const marker = this.markerNear(event.clientX);
            this.scrub(marker ? marker.t : this.timeAt(event.clientX));
            this.showTip(event.clientX);
        }

        pointerMove(event) {
            this.showTip(event.clientX);
            if (this.dragging) {
                this.scrub(this.timeAt(event.clientX));
            }
        }

        pointerUp(event) {
            if (!this.dragging) {
                return;
            }
            this.dragging = false;
            // fastSeek lands on keyframes while dragging; the release goes to the exact spot.
            this.media.currentTime = this.dragTime;
            if (Math.abs(this.dragTime - this.dragFrom) >= JUMP_SECONDS) {
                this.jumpFrom = this.dragFrom;
            }
            this.settled = this.dragTime;
            if (event.type === 'pointercancel') {
                this.tip.hidden = true;
            }
            this.paint();
            this.notify('seek', { from: this.dragFrom, to: this.dragTime, cause: 'scale' });
        }

        scrub(t) {
            this.dragTime = t;
            if (this.isVideo && typeof this.media.fastSeek === 'function') {
                this.media.fastSeek(t);
            } else {
                this.media.currentTime = t;
            }
            this.paint();
        }

        showTip(clientX) {
            const d = this.duration();
            if (!(d > 0)) {
                return;
            }
            const rect = this.track.getBoundingClientRect();
            const marker = this.markerNear(clientX);
            const x = marker ? (marker.t / d) * rect.width : clamp(clientX - rect.left, 0, rect.width);
            this.tip.replaceChildren(el('strong', '', formatTime((x / rect.width) * d, d >= 3600, d < 60)));
            const name = marker && (marker.label || (this.kinds.length ? this.kindLabel(marker.kind) : ''));
            if (name) {
                this.tip.append(el('span', '', name));
            }
            this.tip.hidden = false;
            const half = this.tip.offsetWidth / 2;
            this.tip.style.left = `${clamp(x, half, Math.max(half, rect.width - half))}px`;
        }

        // ------------------------------------------------------------ drawing

        loop() {
            if (this.frame) {
                return;
            }
            const tick = () => {
                this.paint();
                this.frame = this.media.paused ? 0 : requestAnimationFrame(tick);
            };
            this.frame = requestAnimationFrame(tick);
        }

        poke() {
            this.root.classList.remove('is-idle');
            clearTimeout(this.idleTimer);
            if (document.fullscreenElement === this.root && !this.media.paused) {
                this.idleTimer = setTimeout(() => this.root.classList.add('is-idle'), IDLE_MS);
            }
        }

        paintAll() {
            this.paint();
            this.paintBuffered();
            this.paintPlayState();
            this.paintSpeed();
            this.paintVolume();
            this.paintCaptions();
            this.paintItems();
            this.pipBtn.hidden = !this.pipSupported();
        }

        paint() {
            const d = this.duration();
            const t = this.dragging ? this.dragTime : this.media.currentTime;
            this.paintCurrentMarker(t);
            const ratio = d > 0 ? clamp(t / d, 0, 1) : 0;
            this.fill.style.transform = `scaleX(${ratio})`;
            this.thumb.style.transform = `translateX(${ratio * this.trackWidth}px)`;
            const long = d >= 3600;
            const tenths = d > 0 && d < 10;
            // Whole length minus whole elapsed, so the two always add up to the length on the scale.
            const unit = tenths ? 10 : 1;
            const left = Math.max(0, Math.floor(d * unit + 1e-6) - Math.floor(t * unit + 1e-6)) / unit;
            setText(this.elapsedEl, formatTime(t, long, tenths));
            setText(this.remainingEl, `-${formatTime(left, long, tenths)}`);
            const whole = Math.floor(t);
            if (whole !== this.ariaSecond || d !== this.ariaDuration) {
                this.ariaSecond = whole;
                this.ariaDuration = d;
                this.track.setAttribute('aria-valuemax', String(Math.round(d)));
                this.track.setAttribute('aria-valuenow', String(whole));
                this.track.setAttribute('aria-valuetext', `${this.format(t)} of ${this.format(d)}`);
            }
        }

        paintBuffered() {
            const d = this.duration();
            const ranges = this.media.buffered;
            const parts = [];
            if (d > 0 && ranges) {
                for (let i = 0; i < ranges.length; i += 1) {
                    parts.push([ranges.start(i) / d, ranges.end(i) / d]);
                }
            }
            while (this.bufferedEl.children.length < parts.length) {
                this.bufferedEl.append(el('span'));
            }
            Array.from(this.bufferedEl.children).forEach(function (span, index) {
                const part = parts[index];
                span.hidden = !part;
                if (part) {
                    span.style.left = `${clamp(part[0], 0, 1) * 100}%`;
                    span.style.width = `${clamp(part[1] - part[0], 0, 1) * 100}%`;
                }
            });
        }

        paintPlayState() {
            const playing = !this.media.paused && !this.media.ended;
            this.root.classList.toggle('is-playing', playing);
            if (this.playIcon !== playing) {
                this.playIcon = playing;
                this.playBtn.innerHTML = ICONS[playing ? 'pause' : 'play'];
                setLabel(this.playBtn, playing ? 'Pause (Space)' : 'Play (Space)');
            }
            if (this.bigPlay) {
                this.bigPlay.hidden = this.started || playing;
            }
            if (playing) {
                this.resumeBtn.hidden = true;
            }
        }

        paintSpeed() {
            const rate = this.media.playbackRate;
            setText(this.speedEl, formatSpeed(rate));
            this.slowerBtn.disabled = rate <= SPEEDS[0];
            this.fasterBtn.disabled = rate >= SPEEDS[SPEEDS.length - 1];
            store.set(`speed:${this.kind}`, String(rate));
            this.sessionPosition();
        }

        paintVolume() {
            if (!this.sound) {
                return;
            }
            const media = this.media;
            const percent = Math.round(media.volume * 100);
            const silent = media.muted || percent === 0;
            this.volumeInput.value = String(percent);
            this.volumeInput.style.setProperty('--mp-volume', `${media.muted ? 0 : percent}%`);
            setText(this.volumeEl, media.muted ? 'Muted' : String(percent));
            this.unitEl.hidden = media.muted;
            if (this.volumeIcon !== silent) {
                this.volumeIcon = silent;
                this.muteBtn.innerHTML = ICONS[silent ? 'muted' : 'volume'];
            }
            setLabel(this.muteBtn, media.muted ? 'Unmute (M)' : 'Mute (M)');
            this.muteBtn.setAttribute('aria-pressed', String(media.muted));
            store.set('volume', String(media.volume));
        }

        paintCaptions() {
            const track = this.isVideo ? this.captionTrack() : null;
            this.ccBtn.hidden = !track;
            if (track) {
                this.ccBtn.setAttribute('aria-pressed', String(track.mode === 'showing'));
            }
        }

        paintItems() {
            const label = this.items ? this.items.label.toLowerCase() : 'item';
            this.prevItemBtn.hidden = !this.items;
            this.nextItemBtn.hidden = !this.items;
            this.prevItemBtn.disabled = !(this.items && this.items.previous);
            this.nextItemBtn.disabled = !(this.items && this.items.next);
            setLabel(this.prevItemBtn, `Previous ${label} (Shift+P)`);
            setLabel(this.nextItemBtn, `Next ${label} (Shift+N)`);
        }

        paintWide() {
            const wide = document.documentElement.classList.contains('mp-wide');
            this.wideBtn.setAttribute('aria-pressed', String(wide));
            setLabel(this.wideBtn, wide ? 'Normal width (T)' : 'Wide page (T)');
        }

        /** Marks the marker the playhead is at or past on the scale, and tells the page when that
            changes (media-player:marker), so it can follow along: a shot strip, a chapter list. */
        paintCurrentMarker(t) {
            const index = lastAtOrBefore(this.shownTimes, t);
            if (index === this.currentMarker) {
                return;
            }
            const before = this.markerSpans[this.currentMarker];
            if (before) {
                before.classList.remove('is-current');
            }
            const now = this.markerSpans[index];
            if (now) {
                now.classList.add('is-current');
            }
            this.currentMarker = index;
            this.notify('marker', { index, marker: index >= 0 ? this.shown[index] : null });
        }

        paintFullscreen() {
            const on = document.fullscreenElement === this.root;
            this.root.classList.toggle('is-fullscreen', on);
            if (this.isVideo) {
                this.fullBtn.innerHTML = ICONS[on ? 'windowed' : 'fullscreen'];
                setLabel(this.fullBtn, on ? 'Leave full screen (F)' : 'Full screen (F)');
            }
            this.poke();
        }

        renderMarkers() {
            const d = this.duration();
            this.shown = this.markers.filter((marker) => !this.hiddenKinds.has(marker.kind));
            this.shownTimes = this.shown.map((marker) => marker.t);
            this.markerSpans = [];
            this.currentMarker = -1;
            this.markersEl.replaceChildren();
            this.renderLegend();
            if (!(d > 0)) {
                return;
            }
            // The first kind is the most important, so it is drawn over the others.
            const kinds = this.kindList();
            const byKind = new Map(kinds.map((entry) => [entry.kind, entry]));
            const layer = new Map(kinds.map((entry, index) => [entry.kind, kinds.length - index]));
            const parts = document.createDocumentFragment();
            for (const marker of this.shown) {
                const mark = el('span');
                mark.style.left = `${clamp(marker.t / d, 0, 1) * 100}%`;
                mark.style.zIndex = String(layer.get(marker.kind));
                const color = marker.color || byKind.get(marker.kind).color;
                if (color) {
                    mark.style.setProperty('--mp-marker-color', color);
                }
                parts.append(mark);
                this.markerSpans.push(mark);
            }
            this.markersEl.append(parts);
            this.paintCurrentMarker(this.media.currentTime);
        }

        /** One button per kind of marker: its color, its name and how many there are; a click hides
            or shows that kind. */
        renderLegend() {
            const kinds = this.presentKinds();
            this.legend.hidden = !kinds.length;
            this.legend.replaceChildren(...kinds.map((entry) => {
                const count = this.markers.filter((marker) => marker.kind === entry.kind).length;
                const shown = !this.hiddenKinds.has(entry.kind);
                const button = textButton('mp-legend-item', null,
                    `${entry.label}: ${count} on the scale, ${shown ? 'shown' : 'hidden'}. Click to ${shown ? 'hide' : 'show'} them`);
                button.setAttribute('aria-pressed', String(shown));
                const swatch = el('span', 'mp-legend-swatch');
                if (entry.color) {
                    swatch.style.setProperty('--mp-marker-color', entry.color);
                }
                button.append(swatch, el('span', 'mp-legend-label', entry.label), el('span', 'mp-legend-count', String(count)));
                button.addEventListener('click', () => this.toggleKind(entry.kind));
                return button;
            }));
        }

        // The notched scale under the track: labeled notches as close as their labels allow, finer
        // unlabeled notches between them, both ends always labeled. Redrawn when the length or the
        // width changes, so a wider player shows a finer scale.
        renderScale() {
            const d = this.duration();
            const width = this.ticks.clientWidth;
            this.trackWidth = this.track.clientWidth;
            const key = `${d}|${width}`;
            if (key !== this.scaleKey) {
                this.scaleKey = key;
                this.drawTicks(d, width);
            }
            this.paint();
        }

        drawTicks(d, width) {
            this.ticks.replaceChildren();
            if (!(d > 0) || !(width > 0)) {
                return;
            }
            const long = d >= 3600;
            const measure = (text) => {
                const probe = el('span', 'mp-tick-label', text);
                probe.style.visibility = 'hidden';
                this.ticks.append(probe);
                const px = probe.offsetWidth;
                probe.remove();
                return px;
            };
            let labelPx = measure(formatTime(d, long, false));
            let scale = chooseScale(d, width, labelPx);
            if (scale.major < 1) {
                labelPx = measure(formatTime(d, long, true));
                scale = chooseScale(d, width, labelPx);
            }
            const tenths = scale.major < 1;
            const fine = scale.minor || scale.major;
            const perMajor = Math.round(scale.major / fine);
            // The end labels sit inside the scale, so a centered label needs half a label more room
            // beside them than between two centered labels.
            const edge = ((1.5 * labelPx + LABEL_GAP_PX) * d) / width;
            const parts = document.createDocumentFragment();
            const add = (at, major, labeled, end) => {
                const left = `${(at / d) * 100}%`;
                const tick = el('span', major ? 'mp-tick is-major' : 'mp-tick');
                tick.style.left = left;
                parts.append(tick);
                if (labeled) {
                    const label = el('span', end ? `mp-tick-label is-${end}` : 'mp-tick-label', formatTime(at, long, tenths));
                    label.style.left = left;
                    parts.append(label);
                }
            };
            add(0, true, true, 'start');
            for (let i = 1; i * fine < d - 1e-6; i += 1) {
                const at = i * fine;
                const major = i % perMajor === 0;
                add(at, major, major && at >= edge && d - at >= edge, '');
            }
            add(d, true, true, 'end');
            this.ticks.append(parts);
        }
    }

    // ---------------------------------------------------------------- page-wide wiring

    /** In the page and not inside something hidden, such as another view of a one-page app. */
    function showing(player) {
        return player.root.isConnected && player.root.getClientRects().length > 0;
    }

    function pagePlayer() {
        if (active && showing(active)) {
            return active;
        }
        const shown = Array.from(live).filter(showing);
        return shown.length === 1 ? shown[0] : null;
    }

    function onKeydown(event) {
        if (event.defaultPrevented || event.isComposing || event.altKey || event.metaKey) {
            return;
        }
        if (event.ctrlKey && event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
            return;
        }
        const target = event.target instanceof Element ? event.target : null;
        const inside = target ? target.closest('.mp') : null;
        const player = inside ? byRoot.get(inside) : pagePlayer();
        if (!player || (target && target !== player.track && ownsKey(target, event.key))) {
            return;
        }
        const press = {
            key: event.key.length === 1 ? event.key.toLowerCase() : event.key,
            shiftKey: event.shiftKey,
            ctrlKey: event.ctrlKey,
        };
        const entry = KEYS.find((candidate) => candidate.match(press, player) && player.allows(candidate, Boolean(inside)));
        if (!entry) {
            return;
        }
        event.preventDefault();
        if (!(entry.once && event.repeat)) {
            entry.run(player, press);
        }
    }

    function install() {
        if (installed) {
            return;
        }
        installed = true;
        document.addEventListener('keydown', onKeydown);
        root.addEventListener('hashchange', function () {
            if (hashOwner) {
                hashOwner.applyHash(false);
            }
        });
        document.addEventListener('pointerdown', function (event) {
            live.forEach(function (player) {
                if (!player.help.hidden && !player.help.contains(event.target) && !player.helpBtn.contains(event.target)) {
                    player.toggleHelp(false);
                }
            });
        });
    }

    // data-wide and data-half-jumps switch a feature on by being there; data-sound="off" says the
    // media has no sound, so there are no volume controls.
    function optionsFrom(media) {
        const data = media.dataset;
        return {
            title: data.title,
            duration: data.duration,
            markers: data.markers ? JSON.parse(data.markers) : undefined,
            markersLabel: data.markersLabel,
            markerKinds: data.markerKinds ? JSON.parse(data.markerKinds) : undefined,
            resumeKey: data.resumeKey,
            resume: data.resume === 'off' ? false : undefined,
            fps: data.fps,
            wide: data.wide !== undefined ? true : undefined,
            halfJumps: data.halfJumps !== undefined ? true : undefined,
            skips: data.skips,
            sound: data.sound === 'off' ? false : undefined,
        };
    }

    function start(media, options) {
        install();
        const player = new Player(media, options);
        byMedia.set(media, player);
        byRoot.set(player.root, player);
        live.add(player);
        return player;
    }

    /** Gives `media` (a <video> or <audio> in the page) this player and returns its controller.
        `options` override the data-* attributes: title, duration, markers, markersLabel,
        markerKinds, resumeKey, resume (false: do not remember the position), fps, wide,
        halfJumps, sound (false: no volume controls), and saveFrame, a function given
        {time, blob, player} that keeps the picture S saves (it may return a promise of the
        message to show). */
    function enhance(media, options) {
        if (byMedia.has(media)) {
            return byMedia.get(media);
        }
        const merged = optionsFrom(media);
        Object.keys(options || {}).forEach(function (key) {
            if (options[key] !== undefined) {
                merged[key] = options[key];
            }
        });
        return start(media, merged);
    }

    /** Gives a source (see Source) this player and returns its controller. `options.stage` is the
        page element that shows the source, which the player is built around; `options.kind` is
        'video' or 'audio'; the rest are enhance()'s options. */
    function attach(source, options) {
        if (byMedia.has(source)) {
            return byMedia.get(source);
        }
        return start(source, Object.assign({}, options));
    }

    /** The base for a source: media that is not one <video> or <audio> element, such as camera
        videos on one clock or an embedded YouTube player. A subclass provides what the player
        reads and sets on a media element (currentTime, duration, paused, ended, seeking,
        playbackRate, defaultPlaybackRate, play() and pause(); volume and muted unless the
        player has sound: false; readyState, buffered, error and textTracks when it has them)
        and calls emit() with the media element's event names: loadedmetadata, durationchange,
        play, playing, pause, ended, timeupdate, seeking, seeked, waiting, canplay, progress,
        ratechange, volumechange, error, loadstart, emptied. Spec: SPEC.md, "Sources". */
    const Source = typeof EventTarget === 'undefined' ? null : class Source extends EventTarget {
        get buffered() {
            return null;
        }

        get error() {
            return null;
        }

        get textTracks() {
            return [];
        }

        get readyState() {
            return this.duration > 0 ? 1 : 0;
        }

        emit(type) {
            this.dispatchEvent(new Event(type));
        }
    };

    /** Enhances every <video data-media-player> and <audio data-media-player> under `scope`. A
        player that cannot start says why in the page, next to the media element. */
    function enhanceAll(scope) {
        const players = [];
        (scope || document).querySelectorAll('video[data-media-player], audio[data-media-player]').forEach(function (media) {
            try {
                players.push(enhance(media));
            } catch (error) {
                const note = el('div', 'mp-error', `The player could not start: ${error.message}`);
                note.setAttribute('role', 'alert');
                media.before(note);
                console.error(error);
            }
        });
        return players;
    }

    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => enhanceAll());
        } else {
            enhanceAll();
        }
    }

    return {
        VERSION,
        SPEEDS,
        KEYS,
        CAPABILITIES,
        formatTime,
        formatPrecise,
        parseTime,
        timeFromHash,
        chooseScale,
        normalizeMarkers,
        normalizeKinds,
        adjacentMarker,
        lastAtOrBefore,
        halfJumpTarget,
        skipAmounts,
        frameFileName,
        stepSpeed,
        keyApplies,
        Source,
        enhance,
        enhanceAll,
        attach,
        get: (media) => byMedia.get(media),
    };
}));
