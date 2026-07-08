"""The wizard's single embedded page (vanilla JS, token-authenticated)."""

PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hermes-satellite setup</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 860px;
         margin: 1.5rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; } h2 { font-size: 1.05rem; margin-top: 2rem;
       border-bottom: 1px solid #8884; padding-bottom: .3rem; }
  section { margin-bottom: 1rem; }
  button { padding: .35rem .8rem; margin: .15rem .3rem .15rem 0; cursor: pointer; }
  input, select { padding: .3rem; margin: .15rem .3rem .15rem 0; max-width: 100%; }
  table { border-collapse: collapse; } td, th { padding: .15rem .6rem;
       text-align: left; border-bottom: 1px solid #8883; }
  .meter { height: 22px; background: #8882; border-radius: 4px;
       overflow: hidden; max-width: 420px; }
  .meter > div { height: 100%; width: 0; background: #4a9;
       transition: width .12s linear; }
  .hot > div { background: #d55; }
  pre { background: #8881; padding: .7rem; overflow-x: auto; border-radius: 4px; }
  .ok { color: #2a7; } .bad { color: #d55; } .muted { opacity: .65; }
  #exitbar { float: right; }
</style>
</head>
<body>
<div id="exitbar"><button onclick="post('/api/exit').then(()=>document.body.innerHTML='<h1>Wizard closed. No ports remain open.</h1>')">Exit wizard</button></div>
<h1>hermes-satellite setup</h1>
<p class="muted">Temporary session — this server exits when you're done.
Changes are collected below and written for your review; nothing is applied
behind your back.</p>

<h2>1 · Status</h2>
<section><table id="status"></table>
<button onclick="loadStatus()">Re-check</button></section>

<h2>2 · Audio devices</h2>
<section id="audio">
  <table id="devices"></table>
  <label>Input <select id="in_dev"></select></label>
  <label>Output <select id="out_dev"></select></label>
  <label>Channels <select id="in_ch"><option>1</option><option>2</option></select></label>
  <button onclick="selectAudio()">Use these</button>
  <button onclick="post('/api/audio/tone')">Play test tone</button>
</section>

<h2>3 · Microphone level</h2>
<section>
  <p class="muted">Speak from where you normally will. Target: p99 in the
  30–70% zone. Tune gain with <code>alsamixer</code> / the wm8960 script.</p>
  <button onclick="meterStart()">Start meter</button>
  <button onclick="meterStop()">Stop</button>
  <div class="meter" id="rmsbar"><div></div></div>
  <p>live <span id="rms">–</span>% &nbsp; p99(3s) <b id="p99">–</b>%</p>
  <h3 style="font-size:.95rem">Mixer (ALSA)</h3>
  <label>Card <select id="card" onchange="loadMixer()"></select></label>
  <button onclick="applyRecipe()">Apply WM8960 recipe</button>
  <button onclick="mixerStore()">Persist (alsactl store)</button>
  <span id="mixmsg" class="muted"></span>
  <div id="sliders"></div>
</section>

<h2>4 · Wake word</h2>
<section>
  <p class="muted">Start, then say the wake phrase a few times. Threshold
  should sit comfortably below your spoken scores and above ambient.</p>
  <button onclick="wakeStart()">Start monitor</button>
  <button onclick="post('/api/wake/stop')">Stop</button>
  <label>Threshold <input id="thr" type="number" min="0.05" max="1" step="0.05"
    style="width:5rem" onchange="post('/api/wake/config',{threshold:this.value})"></label>
  <p>last <b id="wl">–</b> · best <b id="wb">–</b> ·
     detections <b id="wd">0</b> <span id="werr" class="bad"></span></p>
</section>

<h2>5 · Voice</h2>
<section>
  <label>Voice <select id="voice"></select></label>
  <label>Speaker <input id="spk" type="number" style="width:4.5rem"
    placeholder="n/a"></label>
  <label>Pace <input id="pace" type="number" min="0.5" max="2" step="0.05"
    style="width:5rem" placeholder="1.0"></label><br>
  <input id="phrase" size="52"
    value="Good evening. All systems are operating within normal parameters.">
  <button onclick="preview()">Preview on this device</button>
  <span id="vres" class="muted"></span>
</section>

<h2>6 · Hermes</h2>
<section>
  <label>Host <input id="hh" size="14"></label>
  <label>Port <input id="hp" size="5"></label>
  <label>API key <input id="hk" size="24" type="password"
    placeholder="(from config/env)"></label>
  <button onclick="hermesTest()">Test connection + chat</button>
  <p id="hres"></p>
</section>

<h2>7 · Review &amp; save</h2>
<section>
  <pre id="pending">(no changes yet)</pre>
  <button onclick="save()">Write config for review</button>
  <pre id="saved" style="display:none"></pre>
</section>

<script>
const TOKEN = "__TOKEN__";
const get  = (p)    => fetch(p + "?token=" + TOKEN).then(r => r.json());
const post = (p, b) => fetch(p + "?token=" + TOKEN, {method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify(b || {})}).then(r => r.json());
let meterTimer = null, wakeTimer = null;

function loadStatus() {
  get("/api/status").then(s => {
    document.getElementById("status").innerHTML = Object.entries(s)
      .map(([k, v]) => `<tr><th>${k}</th><td>${Array.isArray(v)?v.join(", "):v}</td></tr>`)
      .join("");
  });
}
function loadAudio() {
  get("/api/audio/devices").then(a => {
    document.getElementById("devices").innerHTML =
      "<tr><th>#</th><th>device</th><th>in</th><th>out</th></tr>" +
      a.devices.map(d => `<tr><td>${d.index}</td><td>${d.name}</td>` +
        `<td>${d.inputs}</td><td>${d.outputs}</td></tr>`).join("");
    const mk = (sel, filter, current) => {
      const el = document.getElementById(sel);
      el.innerHTML = "<option value=''>default</option>" + a.devices
        .filter(filter).map(d =>
          `<option value="${d.index}" ${d.index===current?"selected":""}>` +
          `${d.index}: ${d.name.slice(0, 34)}</option>`).join("");
    };
    mk("in_dev",  d => d.inputs  > 0, a.input_device);
    mk("out_dev", d => d.outputs > 0, a.output_device);
    document.getElementById("in_ch").value = a.input_channels;
  });
}
function selectAudio() {
  const v = id => { const x = document.getElementById(id).value;
                    return x === "" ? null : x; };
  post("/api/audio/select", {input_device: v("in_dev"),
    output_device: v("out_dev"), input_channels: v("in_ch")}).then(loadPending);
}
function meterStart() {
  post("/api/meter/start");
  clearInterval(meterTimer);
  meterTimer = setInterval(() => get("/api/meter").then(m => {
    document.getElementById("rms").textContent = m.rms_pct;
    document.getElementById("p99").textContent = m.p99_pct;
    const bar = document.getElementById("rmsbar");
    bar.firstElementChild.style.width = Math.min(m.rms_pct * 2.2, 100) + "%";
    bar.classList.toggle("hot", m.p99_pct > 85);
  }), 250);
}
function meterStop() { post("/api/meter/stop"); clearInterval(meterTimer); }
function loadCards() {
  get("/api/mixer/cards").then(c => {
    const sel = document.getElementById("card");
    sel.innerHTML = c.cards.map(x =>
      `<option value="${x.index}" ${x.id.includes("seeed")?"selected":""}>` +
      `${x.index}: ${x.id}</option>`).join("") ||
      "<option value=''>(no ALSA cards)</option>";
    if (c.cards.length) loadMixer();
  });
}
function loadMixer() {
  const card = document.getElementById("card").value;
  if (card === "") return;
  get("/api/mixer?card=" + card).then(m => {
    const rows = [];
    for (const [name, c] of Object.entries(m.controls || {})) {
      rows.push(`<label style="display:block">` +
        `<span style="display:inline-block;width:8rem">${name}` +
        `${c.switch ? " ["+c.switch+"]" : ""}</span>` +
        `<input type="range" min="0" max="${c.max}" value="${c.value}" ` +
        `style="width:260px;vertical-align:middle" ` +
        `onchange="setMixer('${name}', this.value)">` +
        ` <span>${c.value}/${c.max}</span></label>`);
    }
    document.getElementById("sliders").innerHTML = rows.join("") ||
      "<p class='muted'>no adjustable controls found on this card</p>";
  });
}
function setMixer(control, value) {
  const card = document.getElementById("card").value;
  post("/api/mixer/set", {card: card, control: control, value: value})
    .then(r => { document.getElementById("mixmsg").textContent =
      r.error ? ("failed: " + r.error) : ""; loadMixer(); });
}
function applyRecipe() {
  const card = document.getElementById("card").value;
  document.getElementById("mixmsg").textContent = "applying…";
  post("/api/mixer/recipe", {card: card}).then(r => {
    document.getElementById("mixmsg").textContent =
      `applied ${r.applied.length} controls` +
      (r.failed.length ? `; failed: ${r.failed.join(", ")}` : "");
    loadMixer();
  });
}
function mixerStore() {
  post("/api/mixer/store").then(r => {
    document.getElementById("mixmsg").textContent =
      r.ok ? "persisted ✓" : (r.hint || r.error || "failed");
  });
}
function wakeStart() {
  post("/api/wake/start");
  clearInterval(wakeTimer);
  wakeTimer = setInterval(() => get("/api/wake").then(w => {
    document.getElementById("wl").textContent = w.last.toFixed(3);
    document.getElementById("wb").textContent = w.best.toFixed(3);
    document.getElementById("wd").textContent = w.detections;
    document.getElementById("werr").textContent = w.error || "";
    if (!document.getElementById("thr").value)
      document.getElementById("thr").value = w.threshold;
  }), 300);
}
function loadVoices() {
  get("/api/voices").then(v => {
    const sel = document.getElementById("voice");
    const opts = [];
    for (const name of v.downloaded)
      opts.push(`<option ${name===v.current?"selected":""}>${name}</option>`);
    for (const [name, speakers] of Object.entries(v.catalog))
      if (!v.downloaded.includes(name))
        opts.push(`<option>${name}${speakers>1?` (${speakers} spk)`:""}</option>`);
    sel.innerHTML = opts.join("");
    if (v.speaker_id !== null) document.getElementById("spk").value = v.speaker_id;
    if (v.length_scale !== null) document.getElementById("pace").value = v.length_scale;
  });
}
function preview() {
  const name = document.getElementById("voice").value.split(" ")[0];
  document.getElementById("vres").textContent = "downloading/synthesizing…";
  post("/api/voices/preview", {name: name,
    speaker_id: document.getElementById("spk").value,
    length_scale: document.getElementById("pace").value,
    text: document.getElementById("phrase").value})
  .then(r => {
    document.getElementById("vres").textContent =
      r.error ? ("failed: " + r.error) : ("played @ " + r.sample_rate + " Hz");
    loadPending();
  });
}
function hermesTest() {
  document.getElementById("hres").textContent = "testing…";
  post("/api/hermes/test", {host: document.getElementById("hh").value,
    port: document.getElementById("hp").value,
    api_key: document.getElementById("hk").value})
  .then(r => {
    document.getElementById("hres").innerHTML =
      `health: <b>${r.health || "?"}</b> · chat: <b>${r.chat || "—"}</b>`;
    loadPending();
  });
}
function loadPending() {
  get("/api/pending").then(p => {
    const keys = Object.keys(p);
    document.getElementById("pending").textContent = keys.length
      ? keys.map(k => `${k}: ${JSON.stringify(p[k])}`).join("\\n")
      : "(no changes yet)";
  });
}
function save() {
  post("/api/save").then(r => {
    const el = document.getElementById("saved");
    el.style.display = "block";
    el.textContent = `written: ${r.written}\\n\\n${r.note}\\n  ${r.command}`;
  });
}
loadStatus(); loadAudio(); loadVoices(); loadPending(); loadCards();
</script>
</body>
</html>
"""
