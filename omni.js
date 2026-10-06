/* omni.js — the robot's workspace dock + agent client.
 *
 *  Tabs:  Workspace (full AURA renderer: graphs, 3D/2D sims, circuits, tables, math, Python)
 *         Music     (YouTube IFrame player, volume, ducking while the robot talks/listens)
 *         Browser   (live view of the agent browser: screenshots+click in "full", reader in "lite")
 *
 *  Kiosk hooks (see kiosk.html):  Omni.agentTurn(prompt), Omni.isAgentTask(prompt), Omni.onFaceMode(mode)
 */
(function () {
  'use strict';
  const BACKEND = '';
  const $ = (s, r) => (r || document).querySelector(s);
  const clamp = (n, a, b) => Math.max(a == null ? 0 : a, Math.min(b == null ? 100 : b, Math.round(+n) || 0));
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  };
  async function api(path, body, method) {
    const res = await fetch(BACKEND + path, body === undefined && !method ? {} : {
      method: method || 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body)
    });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
  }

  // The kiosk's own drink regex also fires on "make a graph" / "give me a song"; this keeps those out of the recipe path.
  const AGENT_RE = /\b(graph|plot|simulat\w*|circuit|equation|solve|derivative|integral|calculat\w*|physics|math\w*|diagram|table|chart|music|song|songs|play|pause|resume|volume|louder|quieter|mute|unmute|listen|hear|put on|playlist|album|artist|search|google|browse|website|web ?page|youtube|download|look up|news|weather|code|program(?:ming)?|script|debug(?:ging)?|implement|algorithm|explain|analy[sz]\w*|summari[sz]\w*|compare|research|formula|function|class|write|build|create|generate|design)\b/i;
  const DRINK_RE = /\b(drink|soda|beverage|pop|flavou?r|cola|lemonade|spritz|mocktail|refresh\w*|fizz\w*|thirsty)\b/i;
  const NON_DRINK_TASK_RE = /\b(code|program(?:ming)?|script|debug(?:ging)?|implement|algorithm|function|class|graph|plot|simulat\w*|circuit|equation|solve|derivative|integral|calculat\w*|physics|math\w*|diagram|table|chart|search|browse|website|youtube|download|news|weather|research|analy[sz]\w*|summari[sz]\w*|compare)\b/i;

  /* ================================================================ DOM */
  const frame = document.getElementById('frame');
  const dock = document.createElement('div');
  dock.id = 'omniDock';
  dock.innerHTML = `
    <div class="omniTabs">
      <button data-tab="workspace" class="active">Workspace</button>
      <button data-tab="music">Music</button>
      <button data-tab="browser">Browser</button>
      <span class="omniSpacer"></span>
      <span class="omniStatus" id="omniStatus"></span>
      <button class="omniIcon" id="omniMax" title="Full screen">⛶</button>
      <button class="omniIcon" id="omniClose" title="Close">✕</button>
    </div>
    <div class="omniBody">
      <section class="omniPane active" data-pane="workspace">
        <iframe id="omniAura" title="Workspace" sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups"></iframe>
        <div id="omniWorkHint">
          <strong>Ask me to show you something</strong>
          <div>Graphs, 3D &amp; 2D simulations, circuits, tables and worked math appear here.</div>
          <div class="omniChips">
            <button data-ask="Graph y = sin(x) and y = x^2 on the same axes">Graph</button>
            <button data-ask="Show me a 3D simulation of a ball bouncing on the floor">3D sim</button>
            <button data-ask="Draw a circuit with a 9V battery, a 2k ohm resistor, a switch and a bulb">Circuit</button>
            <button data-ask="Coach me through solving 2x^2 - 8x + 6 = 0">Math coach</button>
            <button data-ask="Make a table comparing the planets by diameter and distance from the sun">Table</button>
          </div>
        </div>
        <button class="omniBtn" id="omniWorkClear" style="display:none">Clear</button>
      </section>

      <section class="omniPane" data-pane="music" id="omniMusic">
        <div id="omniYTBox"><div id="omniYT"></div><div id="omniTapPlay">▶ Tap to start the music</div></div>
        <div id="omniTrack"><small>Nothing playing. Ask me to play a song!</small></div>
        <div class="omniRow">
          <button class="omniBtn" id="omniPP">Pause</button>
          <button class="omniBtn" id="omniStop">Stop</button>
          <button class="omniBtn" id="omniMute">Mute</button>
          <input type="range" id="omniVol" min="0" max="100" step="1" aria-label="Volume">
          <span id="omniVolLabel"></span>
        </div>
        <div class="omniRow">
          <input type="text" id="omniMusicQ" placeholder="Search a song or artist…" autocomplete="off">
          <button class="omniBtn" id="omniMusicGo">Play</button>
        </div>
      </section>

      <section class="omniPane" data-pane="browser" id="omniBrowser">
        <div class="omniBar">
          <button class="omniBtn" id="omniBack" title="Back">◀</button>
          <input type="text" id="omniUrl" placeholder="Search or enter an address…" autocomplete="off">
          <button class="omniBtn" id="omniGo">Go</button>
          <span class="omniBadge" id="omniEngine">…</span>
        </div>
        <div id="omniView"><div class="omniMsg">Ask me to look something up, or type an address above.</div></div>
        <div id="omniFoot">
          <input type="text" id="omniTypeIn" placeholder="Type into the page…" autocomplete="off" style="display:none">
          <button class="omniBtn" id="omniTypeGo" style="display:none">Type ⏎</button>
          <button class="omniBtn" id="omniUp" style="display:none">▲</button>
          <button class="omniBtn" id="omniDown" style="display:none">▼</button>
          <span id="omniDls"></span>
        </div>
      </section>
    </div>`;
  frame.appendChild(dock);

  const pill = document.createElement('div');
  pill.id = 'omniNow';
  pill.innerHTML = '<button id="pillDown" title="Quieter">−</button><span class="n" id="pillName"></span><span id="pillVol"></span><button id="pillUp" title="Louder">+</button><button id="pillPP" title="Pause">❚❚</button>';
  frame.appendChild(pill);

  const toggle = document.createElement('button');
  toggle.id = 'omniToggle';
  toggle.className = 'hud';
  toggle.textContent = 'TOOLS';
  toggle.style.cssText = 'background:transparent;border:1px solid #414744;color:var(--dim);padding:6px 10px;border-radius:4px;font-size:11px;cursor:pointer;top:12px;letter-spacing:.02em;';
  frame.appendChild(toggle);

  const statusEl = $('#omniStatus');
  let statusTimer = null;
  function status(text) {
    statusEl.textContent = text || '';
    clearTimeout(statusTimer);
    if (text) statusTimer = setTimeout(() => { statusEl.textContent = ''; }, 5000);
  }

  /* ================================================================ Dock */
  const Dock = {
    tab: 'workspace',
    show(tab) {
      this.tab = tab;
      dock.querySelectorAll('.omniTabs [data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      dock.querySelectorAll('.omniPane').forEach(p => p.classList.toggle('active', p.dataset.pane === tab));
      if (tab === 'workspace') Work.ensure();
      if (tab === 'browser') Browser.activated();
    },
    open(tab) {                       // visible either in the split view (drink-mode) or as a floating panel
      if (!frame.classList.contains('drink-mode')) dock.classList.add('float');
      this.show(tab || this.tab);
    },
    close() { dock.classList.remove('float', 'max'); }
  };
  dock.querySelectorAll('.omniTabs [data-tab]').forEach(b => b.addEventListener('click', () => Dock.show(b.dataset.tab)));
  $('#omniClose').addEventListener('click', () => Dock.close());
  $('#omniMax').addEventListener('click', () => dock.classList.toggle('max'));
  toggle.addEventListener('click', () => {
    if (dock.classList.contains('float')) Dock.close(); else { dock.classList.add('float'); Dock.show(Dock.tab); }
  });

  /* ================================================================ Workspace (AURA) */
  const Work = {
    frame: $('#omniAura'), ready: false, queue: [], started: false, any: false,
    ensure() {
      if (this.started) return;
      this.started = true;
      this.frame.src = '/aura?embed=1';
    },
    post(msg) {
      this.ensure();
      if (this.ready) this.frame.contentWindow.postMessage(msg, location.origin); else this.queue.push(msg);
    },
    render(user, text) {
      this.any = true;
      $('#omniWorkHint').classList.add('hidden');
      $('#omniWorkClear').style.display = '';
      this.post({ omni: 'render', user: user, text: text });
    },
    clear() {
      this.any = false;
      this.post({ omni: 'clear' });
      $('#omniWorkHint').classList.remove('hidden');
      $('#omniWorkClear').style.display = 'none';
    }
  };
  $('#omniWorkClear').addEventListener('click', () => Work.clear());
  window.addEventListener('message', e => {
    if (e.origin !== location.origin || e.source !== Work.frame.contentWindow) return;
    const m = e.data || {};
    if (m.omni === 'ready') { Work.ready = true; Work.queue.splice(0).forEach(q => Work.frame.contentWindow.postMessage(q, location.origin)); }
    else if (m.omni === 'browse' && /^https?:\/\//i.test(m.url || '')) { Dock.open('browser'); Browser.go(m.url); }
  });
  dock.querySelectorAll('[data-ask]').forEach(b => b.addEventListener('click', () => {
    if (window.KioskAPI) window.KioskAPI.ask(b.dataset.ask);
  }));

  /* ================================================================ Volume */
  const Vol = {
    level: clamp(LS.get('omni.vol', 60)), muted: false, ducked: false, device: false, sendTimer: null,
    async probe() {
      try { this.device = !!(await api('/system/volume', undefined, 'GET')).supported; } catch (e) { this.device = false; }
      this.apply(true);
    },
    set(n) { this.level = clamp(n); LS.set('omni.vol', this.level); this.apply(); this.paint(); },
    // Real device volume when the OS lets us (then the player stays at 100 and duck/mute act on the player);
    // otherwise the YouTube player's own volume is the master.
    apply(skipDevice) {
      const p = Music.player;
      if (this.device) {
        if (!skipDevice) {
          clearTimeout(this.sendTimer);
          this.sendTimer = setTimeout(() => { api('/system/volume', { level: this.level }).catch(() => {}); }, 150);
        }
        if (p && p.setVolume) p.setVolume(this.ducked ? 30 : 100);
      } else if (p && p.setVolume) {
        p.setVolume(Math.round(this.level * (this.ducked ? 0.3 : 1)));
      }
      if (p && p.mute) { this.muted ? p.mute() : p.unMute(); }
    },
    duck(on) { if (this.ducked !== on) { this.ducked = on; this.apply(true); } },
    paint() {
      $('#omniVol').value = this.level;
      $('#omniVolLabel').textContent = this.level + '%';
      $('#pillVol').textContent = this.level + '%';
      $('#omniMute').textContent = this.muted ? 'Unmute' : 'Mute';
    }
  };
  $('#omniVol').addEventListener('input', e => Vol.set(e.target.value));
  $('#omniMute').addEventListener('click', () => Music.control(Vol.muted ? 'unmute' : 'mute'));

  /* ================================================================ Music */
  let ytLoading = null;
  function loadYT() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    if (ytLoading) return ytLoading;
    ytLoading = new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('YouTube did not load - is the internet connected?')), 15000);
      window.onYouTubeIframeAPIReady = () => { clearTimeout(t); resolve(); };
      const s = document.createElement('script');
      s.src = 'https://www.youtube.com/iframe_api';
      s.onerror = () => { clearTimeout(t); reject(new Error('Could not reach YouTube.')); };
      document.head.appendChild(s);
    }).catch(err => { ytLoading = null; throw err; });
    return ytLoading;
  }

  const Music = {
    player: null, candidates: [], idx: 0, track: null, state: -1, tapTimer: null,
    get playing() { return this.state === 1 || this.state === 3; },
    async play(cands) {
      this.candidates = (cands || []).filter(c => c && /^[\w-]{11}$/.test(c.video_id));
      this.idx = 0;
      if (!this.candidates.length) { status("Couldn't find that song."); return; }
      await this.start();
    },
    async start() {
      const c = this.candidates[this.idx];
      if (!c) { this.track = null; this.paint(); status("Couldn't play that one."); return; }
      this.track = c;
      this.paint();
      try { await loadYT(); } catch (err) { status(err.message); return; }
      if (!this.player) {
        this.player = new YT.Player('omniYT', {
          width: '100%', height: '100%', videoId: c.video_id,
          playerVars: { autoplay: 1, playsinline: 1, rel: 0, origin: location.origin },
          events: {
            onReady: () => { Vol.apply(true); this.armTapHint(); },
            onStateChange: e => { this.state = e.data; if (e.data === 1) $('#omniTapPlay').classList.remove('show'); this.paint(); },
            onError: () => { this.idx += 1; this.start(); }      // embedding disabled / unavailable -> next candidate
          }
        });
      } else {
        this.player.loadVideoById(c.video_id);
        Vol.apply(true);
        this.armTapHint();
      }
    },
    // Browsers refuse to start sound without a touch/click; if that happened, show a big tap target.
    armTapHint() {
      clearTimeout(this.tapTimer);
      this.tapTimer = setTimeout(() => {
        const st = this.player && this.player.getPlayerState ? this.player.getPlayerState() : -1;
        if (this.track && st !== 1 && st !== 3) $('#omniTapPlay').classList.add('show');
      }, 2500);
    },
    control(action) {
      const p = this.player;
      if (action === 'mute') { Vol.muted = true; Vol.apply(true); }
      else if (action === 'unmute') { Vol.muted = false; Vol.apply(true); }
      else if (!p) return;
      else if (action === 'pause') p.pauseVideo();
      else if (action === 'resume') p.playVideo();
      else if (action === 'stop') { p.stopVideo(); this.track = null; this.state = -1; }
      Vol.paint(); this.paint();
    },
    paint() {
      const t = this.track;
      $('#omniTrack').innerHTML = '';
      if (t) {
        const b = document.createElement('b'); b.textContent = t.title || 'Playing';
        const s = document.createElement('small'); s.textContent = t.channel ? '  ·  ' + t.channel : '';
        $('#omniTrack').append(b, s);
      } else {
        const s = document.createElement('small'); s.textContent = 'Nothing playing. Ask me to play a song!';
        $('#omniTrack').append(s);
      }
      const playing = this.playing;
      $('#omniPP').textContent = playing ? 'Pause' : 'Play';
      $('#pillPP').textContent = playing ? '❚❚' : '▶';
      $('#pillName').textContent = t ? t.title : '';
      pill.classList.toggle('show', !!t);
      Vol.paint();
    }
  };
  $('#omniPP').addEventListener('click', () => Music.control(Music.playing ? 'pause' : 'resume'));
  $('#pillPP').addEventListener('click', () => Music.control(Music.playing ? 'pause' : 'resume'));
  $('#omniStop').addEventListener('click', () => Music.control('stop'));
  $('#pillUp').addEventListener('click', () => Vol.set(Vol.level + 10));
  $('#pillDown').addEventListener('click', () => Vol.set(Vol.level - 10));
  $('#omniTapPlay').addEventListener('click', () => { if (Music.player) Music.player.playVideo(); $('#omniTapPlay').classList.remove('show'); });
  async function manualMusic() {
    const q = $('#omniMusicQ').value.trim();
    if (!q) return;
    status('Searching…');
    try { Music.play((await api('/browse/music', { query: q })).results); status(''); } catch (err) { status(err.message); }
  }
  $('#omniMusicGo').addEventListener('click', manualMusic);
  $('#omniMusicQ').addEventListener('keydown', e => { if (e.key === 'Enter') manualMusic(); });

  /* ================================================================ Browser */
  const Browser = {
    caps: null, live: false, pollTimer: null, last: '',
    async activated() {
      if (!this.caps) await this.loadCaps();
      this.refreshDownloads();
      if (this.live) this.poll();
    },
    async loadCaps() {
      try { this.caps = await api('/browse/capabilities', undefined, 'GET'); } catch (e) { this.caps = { engine: 'lite', can_click: false }; }
      const b = $('#omniEngine');
      b.textContent = (this.caps.can_click ? 'FULL' : 'LITE') + (this.caps.remote ? ' · REMOTE' : '');
      b.classList.toggle('full', !!this.caps.can_click);
      b.title = this.caps.remote_error || this.caps.full_error || '';
      ['omniTypeIn', 'omniTypeGo', 'omniUp', 'omniDown'].forEach(id => { $('#' + id).style.display = this.caps.can_click ? '' : 'none'; });
    },
    msg(text) { const v = $('#omniView'); v.innerHTML = ''; const d = document.createElement('div'); d.className = 'omniMsg'; d.textContent = text; v.appendChild(d); },
    async go(input) {
      if (!this.caps) await this.loadCaps();
      input = String(input || '').trim();
      if (!input) return;
      const isUrl = /^https?:\/\//i.test(input) || /^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(input);
      $('#omniUrl').value = input;
      this.msg('Loading…');
      try {
        if (this.caps.can_click) {
          const url = isUrl ? input : 'https://duckduckgo.com/?q=' + encodeURIComponent(input);
          const snap = await api('/browse/open', { url: url });
          $('#omniUrl').value = snap.url;
          this.showShot();
        } else if (isUrl) {
          await this.reader(input);
        } else {
          await this.results(input);
        }
      } catch (err) { this.msg(err.message); }
    },
    showShot() {
      this.live = true;
      const v = $('#omniView');
      let img = $('#omniShot');
      if (!img) {
        v.innerHTML = '';
        img = document.createElement('img');
        img.id = 'omniShot'; img.alt = 'Live browser view';
        img.addEventListener('click', e => this.clickShot(e));
        v.appendChild(img);
      }
      this.poll(true);
    },
    poll(now) {
      clearTimeout(this.pollTimer);
      const visible = Dock.tab === 'browser' && getComputedStyle(dock).visibility === 'visible' && !document.hidden;
      const tick = async () => {
        const img = $('#omniShot');
        if (!img || !this.live) return;
        if (Dock.tab === 'browser' && getComputedStyle(dock).visibility === 'visible' && !document.hidden) {
          img.src = BACKEND + '/browse/screenshot?t=' + Date.now();
        }
        this.pollTimer = setTimeout(tick, 1400);
      };
      if (now || visible) tick(); else this.pollTimer = setTimeout(tick, 1400);
    },
    async clickShot(e) {
      const img = e.currentTarget, r = img.getBoundingClientRect();
      if (!img.naturalWidth) return;
      const x = (e.clientX - r.left) / r.width * img.naturalWidth, y = (e.clientY - r.top) / r.height * img.naturalHeight;
      try { await api('/browse/click', { x: x, y: y }); this.poll(true); } catch (err) { status(err.message); }
    },
    async results(q) {
      const res = (await api('/browse/search', { query: q })).results;
      const v = $('#omniView'); v.innerHTML = '';
      const box = document.createElement('div'); box.id = 'omniReader';
      const h = document.createElement('h3'); h.textContent = 'Results for “' + q + '”'; box.appendChild(h);
      const links = document.createElement('div'); links.className = 'links';
      res.forEach(r => {
        const b = document.createElement('button'); b.textContent = r.title + (r.snippet ? ' — ' + r.snippet : '');
        b.addEventListener('click', () => this.go(r.url));
        links.appendChild(b);
      });
      box.appendChild(links); v.appendChild(box);
    },
    async reader(url) {
      this.live = false;
      const p = await api('/browse/read', { url: url, max_chars: 12000 });
      $('#omniUrl').value = p.url;
      const v = $('#omniView'); v.innerHTML = '';
      const box = document.createElement('div'); box.id = 'omniReader';
      const h = document.createElement('h3'); h.textContent = p.title || p.url;
      const u = document.createElement('div'); u.className = 'u'; u.textContent = p.url;
      const t = document.createElement('div'); t.className = 't'; t.textContent = p.note || p.text;
      box.append(h, u, t);
      if (p.links && p.links.length) {
        const links = document.createElement('div'); links.className = 'links';
        p.links.forEach(l => {
          const b = document.createElement('button'); b.textContent = '[' + l.i + '] ' + l.text;
          b.addEventListener('click', () => this.go(l.url));
          links.appendChild(b);
        });
        box.appendChild(links);
      }
      v.appendChild(box);
    },
    async refreshDownloads() {
      try {
        const d = await api('/browse/downloads', undefined, 'GET');
        const box = $('#omniDls'); box.innerHTML = '';
        (d.downloads || []).slice(-4).forEach(f => {
          const a = document.createElement('a'); a.textContent = '⬇ ' + f.name;
          if (this.caps && !this.caps.remote) a.href = '/browse/downloads/' + encodeURIComponent(f.name);
          box.appendChild(a);
        });
      } catch (e) {}
    }
  };
  $('#omniGo').addEventListener('click', () => Browser.go($('#omniUrl').value));
  $('#omniUrl').addEventListener('keydown', e => { if (e.key === 'Enter') Browser.go($('#omniUrl').value); });
  $('#omniBack').addEventListener('click', async () => {
    try { if (Browser.caps && Browser.caps.can_click) { await api('/browse/nav', { action: 'back' }); Browser.poll(true); } } catch (e) { status(e.message); }
  });
  async function typeIntoPage() {
    const text = $('#omniTypeIn').value;
    if (!text) return;
    try { await api('/browse/type', { text: text, submit: true }); $('#omniTypeIn').value = ''; Browser.poll(true); } catch (e) { status(e.message); }
  }
  $('#omniTypeGo').addEventListener('click', typeIntoPage);
  $('#omniTypeIn').addEventListener('keydown', e => { if (e.key === 'Enter') typeIntoPage(); });
  $('#omniUp').addEventListener('click', () => api('/browse/scroll', { dy: -600 }).then(() => Browser.poll(true)).catch(e => status(e.message)));
  $('#omniDown').addEventListener('click', () => api('/browse/scroll', { dy: 600 }).then(() => Browser.poll(true)).catch(e => status(e.message)));

  /* ================================================================ Agent */
  const history = [];
  function runActions(actions) {
    (actions || []).forEach(a => {
      try {
        if (a.type === 'volume') Vol.set(a.level);
        else if (a.type === 'music.play') Music.play(a.candidates);
        else if (a.type === 'music.control') Music.control(a.action);
        else if (a.type === 'tab') { Dock.open(a.tab); if (a.tab === 'browser') { Browser.loadCaps().then(() => { if (Browser.caps.can_click) Browser.showShot(); }); } }
        else if (a.type === 'browser.reader') { Dock.open('browser'); Browser.loadCaps().then(() => Browser.reader(a.url)).catch(err => Browser.msg(err.message)); }
        else if (a.type === 'download') { status('Downloaded ' + a.name); Browser.refreshDownloads(); }
      } catch (err) { status(String(err.message || err)); }
    });
  }

  const Omni = {
    enabled() { return LS.get('omni.agent', true) !== false; },
    setEnabled(v) { LS.set('omni.agent', !!v); },
    isAgentTask(prompt) { return AGENT_RE.test(prompt) && (!DRINK_RE.test(prompt) || NON_DRINK_TASK_RE.test(prompt)); },
    state() { return { volume: Vol.level, playing: Music.playing, track: Music.track ? Music.track.title : '' }; },
    async agentTurn(prompt) {
      const k = window.KioskAPI || {};
      const res = await api('/agent/chat', {
        prompt: prompt, history: history.slice(-8), state: this.state(),
        personality: k.personality ? k.personality() : '', chat_model: k.chatModel ? k.chatModel() : undefined
      });
      history.push({ role: 'user', content: prompt });
      history.push({ role: 'assistant', content: res.speech + (res.workspace ? ' [Showed on screen: ' + res.workspace.slice(0, 300) + ']' : '') });
      if (res.workspace) { Work.render(prompt, res.workspace); Dock.open('workspace'); }
      runActions(res.actions);
      return { speech: res.speech, workspace: res.workspace, actions: res.actions };
    },
    // the kiosk's face tells us when the robot is talking or listening - lower the music so it can hear/be heard
    _duckTimer: null,
    onFaceMode(mode) {
      clearTimeout(this._duckTimer);
      if (mode === 'speaking' || mode === 'listening') Vol.duck(true);
      else this._duckTimer = setTimeout(() => Vol.duck(false), 600);
    },
    dock: Dock, music: Music, volume: Vol, browser: Browser, workspace: Work
  };
  window.Omni = Omni;

  Music.paint();
  Vol.probe();
})();
