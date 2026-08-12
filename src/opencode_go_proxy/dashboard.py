"""Zero-dependency, read-only dashboard assets."""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OpenCode Proxy</title>
  <link rel="stylesheet" href="/dashboard.css">
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">OpenCode Proxy</p>
      <h1>Go and Zen, in one quiet view.</h1>
      <p class="lede">Health, models, routing, and Desktop setup. This page never calls a model.</p>
    </header>

    <section aria-labelledby="services-title">
      <div class="section-head">
        <h2 id="services-title">Services</h2>
        <button id="refresh" type="button">Refresh</button>
      </div>
      <div id="services" class="services" aria-live="polite"><p class="muted">Checking…</p></div>
    </section>

    <section aria-labelledby="setup-title">
      <h2 id="setup-title">Desktop setup</h2>
      <div class="fields">
        <label>Service<select id="service"></select></label>
        <label>Model<select id="model"></select></label>
      </div>
      <div class="code-head"><span>~/.codex/config.toml</span><button id="copy" type="button">Copy</button></div>
      <pre><code id="config"></code></pre>
    </section>

    <section aria-labelledby="routing-title">
      <h2 id="routing-title">Capability routing and fallback</h2>
      <label class="capability-label">Requested capability
        <select id="capability">
          <option value="plain">Text or local tool</option>
          <option value="image">Vision</option>
          <option value="web_search">Web search</option>
          <option value="file_search">File search</option>
          <option value="computer_use">Computer use</option>
          <option value="code_interpreter">Code interpreter</option>
          <option value="image_generation">Image generation</option>
          <option value="mcp">MCP</option>
          <option value="tool_search">Tool search</option>
          <option value="hosted_shell">Hosted shell</option>
          <option value="skills">Skills</option>
          <option value="programmatic_tool_calling">Programmatic tool calling</option>
          <option value="multi_agent">Multi-agent</option>
          <option value="persisted_reasoning">Stateless persisted reasoning</option>
          <option value="prompt_caching">Prompt caching</option>
          <option value="pro_mode">Pro mode</option>
          <option value="background">Background mode</option>
          <option value="compaction">Compaction</option>
        </select>
      </label>
      <div class="route" aria-live="polite">
        <div class="route-node"><small>Requested model</small><strong id="route-source">—</strong></div>
        <span class="route-arrow" aria-hidden="true">→</span>
        <div class="route-node result"><small id="route-mode">Route</small><strong id="route-target">—</strong></div>
      </div>
      <p id="route-note" class="muted"></p>
      <p class="evidence"><span id="evidence-status" class="evidence-badge untested">Untested</span><span id="evidence-detail">No live evidence recorded for this capability.</span></p>
      <p class="fallback-config"><span>Fallback target</span><code id="fallback-setting">OPENCODE_CAPABILITY_MODEL=gpt-5.6-luna</code></p>
    </section>

    <footer><span id="updated">Not checked yet</span><span>No prompts. No token spend.</span></footer>
  </main>
  <script src="/dashboard.js" defer></script>
</body>
</html>
"""

DASHBOARD_CSS = """:root{color-scheme:light;--ink:#17201d;--muted:#65706c;--line:#dce2df;--paper:#f7f8f5;--white:#fff;--green:#13795b;--red:#b42318;--amber:#9a6700}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{width:min(820px,calc(100% - 36px));margin:0 auto;padding:72px 0 40px}header{padding-bottom:42px;border-bottom:1px solid var(--line)}.eyebrow{margin:0 0 10px;color:var(--green);font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}h1{max-width:680px;margin:0;font:600 clamp(38px,7vw,68px)/1.02 ui-serif,Georgia,serif;letter-spacing:-.045em}h2{margin:0;font-size:17px;letter-spacing:-.01em}.lede{max-width:560px;margin:20px 0 0;color:var(--muted);font-size:17px}section{padding:34px 0;border-bottom:1px solid var(--line)}.section-head,.code-head,footer{display:flex;align-items:center;justify-content:space-between;gap:16px}.services{margin-top:15px;border-top:1px solid var(--line)}.service-row{display:grid;grid-template-columns:minmax(110px,1fr) 2fr auto;gap:18px;align-items:center;padding:15px 0;border-bottom:1px solid var(--line)}.service-row:last-child{border-bottom:0}.service-name{font-weight:650}.service-url{overflow:hidden;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}.status{display:inline-flex;align-items:center;gap:7px;font-size:13px}.status:before{width:8px;height:8px;border-radius:50%;background:var(--red);content:""}.status.ok:before{background:var(--green)}button,select{border:1px solid #c9d1cd;border-radius:7px;background:var(--white);color:var(--ink);font:inherit}button{padding:7px 11px;cursor:pointer}button:hover{border-color:#82908a}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}label{display:grid;gap:6px;color:var(--muted);font-size:13px}select{width:100%;padding:9px}.code-head{margin-top:22px;color:var(--muted);font-size:13px}pre{overflow:auto;margin:8px 0 0;padding:18px;border:1px solid var(--line);border-radius:9px;background:var(--white);font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}.capability-label{max-width:300px;margin-top:18px}.route{display:grid;grid-template-columns:1fr auto 1fr;gap:16px;align-items:stretch;margin:18px 0}.route-node{display:grid;gap:5px;padding:17px;border:1px solid var(--line);border-radius:9px;background:var(--white)}.route-node.result{border-color:#a7c8bc;background:#f2f8f5}.route-node small{color:var(--muted)}.route-node strong{overflow-wrap:anywhere}.route-arrow{align-self:center;color:var(--green);font-size:24px}.evidence{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px}.evidence-badge{border:1px solid currentColor;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}.evidence-badge.verified{color:var(--green)}.evidence-badge.rejected{color:var(--red)}.evidence-badge.untested{color:var(--amber)}.fallback-config{display:flex;flex-wrap:wrap;gap:8px 16px;margin:18px 0 0;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}.fallback-config code{color:var(--ink)}.muted{color:var(--muted)}footer{padding-top:24px;color:var(--muted);font-size:12px}@media(max-width:620px){main{padding-top:42px}.service-row{grid-template-columns:1fr auto}.service-url{grid-column:1/-1;grid-row:2}.fields{grid-template-columns:1fr}.route{grid-template-columns:1fr}.route-arrow{justify-self:center;transform:rotate(90deg)}footer{align-items:flex-start;flex-direction:column}}"""

DASHBOARD_JS = """const state={services:[]};
const el=id=>document.getElementById(id);
const slug=value=>value.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");
function configText(){const service=state.services[Number(el("service").value)]||state.services[0];if(!service)return "";const model=el("model").value||"gpt-5.6-luna";const provider=`opencode-${slug(service.upstream)}`;const profile=`${model}-${slug(service.upstream)}`;const name=`OpenCode ${service.upstream}`;return `[model_providers.${provider}]\nname = "${name}"\nbase_url = "${service.public_url}"\nexperimental_bearer_token = "local-proxy"\nwire_api = "responses"\n\n[profiles."${profile}"]\nmodel_provider = "${provider}"\nmodel = "${model}"\napproval_policy = "untrusted"\nsandbox_mode = "workspace-write"\nfeatures = { memories = false }\n`;}
function renderRoute(){const service=state.services[Number(el("service").value)]||state.services[0];if(!service)return;const model=el("model").value||"gpt-5.6-luna";const capability=el("capability").value;const routing=service.routing||{};const fallback=routing.fallback_model||"gpt-5.6-luna";const visionBridge=routing.vision_bridge_model;const native=(routing.native_models||[]).includes(model);const nativeMetadata=["persisted_reasoning","prompt_caching","pro_mode","background"].includes(capability);const supported=(routing.capability_models?.[model]||[]).includes(capability)||(native&&nativeMetadata);let target=model,mode=native?"Native Responses":"Minimal Chat bridge",note=native?"The request is passed through unchanged.":"Only the Responses and Chat Completions shapes are translated.";if(capability!=="plain"&&!supported){if(capability==="image"&&visionBridge){target=`${visionBridge} → ${model}`;mode="Vision bridge";note="MiMo describes the current image, then the selected model continues the tool turn.";}else{target=fallback;mode="Native capability fallback";note="The original request stays in the same product and moves intact to Luna, preserving native tool items and citations.";}}const evidenceCapability=capability==="plain"?"text":capability;const evidence=routing.verification?.[target]?.[evidenceCapability]||{status:"untested",detail:"No live evidence recorded for this capability."};el("route-source").textContent=model;el("route-target").textContent=target;el("route-mode").textContent=mode;el("route-note").textContent=note;el("evidence-status").textContent=evidence.status;el("evidence-status").className=`evidence-badge ${evidence.status}`;el("evidence-detail").textContent=evidence.detail;el("fallback-setting").textContent=`OPENCODE_CAPABILITY_MODEL=${fallback}`;}
function renderConfig(){const service=state.services[Number(el("service").value)]||state.services[0];const models=service?.models||[];const previous=el("model").value;el("model").replaceChildren(...models.map(id=>new Option(id,id)));if(models.includes(previous))el("model").value=previous;else if(models.includes("gpt-5.6-luna"))el("model").value="gpt-5.6-luna";el("config").textContent=configText();renderRoute();}
function render(){const box=el("services");box.replaceChildren();state.services.forEach((service,index)=>{const row=document.createElement("div");row.className="service-row";const name=document.createElement("span");name.className="service-name";name.textContent=service.name;const url=document.createElement("span");url.className="service-url";url.textContent=`${service.public_url} · ${service.model_count} models`;const status=document.createElement("span");status.className=`status ${service.ok?"ok":""}`;status.textContent=service.ok?"Healthy":"Unavailable";row.append(name,url,status);box.append(row);});el("service").replaceChildren(...state.services.map((s,i)=>new Option(s.name,String(i))));renderConfig();el("updated").textContent=`Checked ${new Date().toLocaleTimeString()}`;}
async function load(){el("refresh").disabled=true;try{const response=await fetch("/dashboard.json",{cache:"no-store"});if(!response.ok)throw new Error("status unavailable");state.services=(await response.json()).services;render();}catch(error){el("services").innerHTML='<p class="muted">Could not load service status.</p>';}finally{el("refresh").disabled=false;}}
el("refresh").addEventListener("click",load);el("service").addEventListener("change",renderConfig);el("model").addEventListener("change",()=>{el("config").textContent=configText();renderRoute();});el("capability").addEventListener("change",renderRoute);el("copy").addEventListener("click",async()=>{await navigator.clipboard.writeText(configText());el("copy").textContent="Copied";setTimeout(()=>el("copy").textContent="Copy",1200);});load();"""
