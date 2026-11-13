"""HTML for the app's own pages. Reports themselves come from pitwall.report and are served as files."""

import json
from typing import Any, Dict, List, Optional

from ..report.event_html import nice_date
from ..report.html import base_css, esc

APP_CSS = """
body{min-height:100vh}
header a.nav{color:#d3dae8;text-decoration:none;margin-left:22px;font-size:14px;padding:4px 0}
header a.nav:hover{color:#fff} header a.nav.on{color:#fff;border-bottom:2px solid var(--gold)}
.appbar{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20}
.appbar .in{max-width:1200px;margin:0 auto;padding:8px 32px;display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}
.appbar a.home{font-family:Michroma,sans-serif;font-size:12px;letter-spacing:.14em;color:var(--navy);text-decoration:none}
.appbar a.home b{color:#c97c06;font-weight:400}
.appbar .title{font-weight:700;font-size:15px}
.appbar .sp{flex:1}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:13px;padding:4px 10px;border-radius:999px;background:#edf0f6;color:#0b2a61}
.pill.busy{background:#fef0d8;color:#8a5305} .pill.err{background:#fbeaea;color:var(--red)} .pill.ok{background:#e3f4ec;color:#11633f}
.pill i{width:8px;height:8px;border-radius:50%;background:currentColor;display:inline-block}
.pill.busy i{animation:pulse 1s infinite alternate} @keyframes pulse{from{opacity:.3}to{opacity:1}}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:40px;padding:0 16px;border-radius:4px;
 border:2px solid var(--navy);background:#fff;color:var(--navy);font:600 14px Arial,sans-serif;cursor:pointer;text-decoration:none}
.btn:hover{background:#f1f3f8} .btn.primary{background:var(--navy);color:#fff} .btn.primary:hover{background:#0b2a61}
.btn.danger{border-color:var(--red);color:var(--red)} .btn.small{min-height:32px;padding:0 10px;font-size:13px}
.btn[disabled]{opacity:.45;cursor:default}
.big-btn{min-height:56px;font-size:17px;padding:0 26px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px}
a.evcard{display:block;text-decoration:none;color:inherit;background:#fff;border:1px solid var(--line);border-top:4px solid var(--gold);
 border-radius:6px;padding:18px 20px;box-shadow:0 2px 6px rgba(1,25,69,.08)}
a.evcard:hover{box-shadow:0 6px 16px rgba(1,25,69,.14)}
.evcard h3{margin:0 0 2px;font-size:19px} .evcard .meta{color:var(--muted);font-size:13px;margin-bottom:12px}
.evcard .nums{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.evcard .nums b{display:block;font-size:20px;font-variant-numeric:tabular-nums} .evcard .nums span{font-size:11px;color:var(--muted)}
label.f{display:block;font-weight:700;margin:18px 0 6px} .hint{color:var(--muted);font-size:13px;margin:4px 0 0}
input[type=text],select{width:100%;max-width:560px;min-height:42px;padding:6px 10px;border:1px solid #b9c0cf;border-radius:4px;font:15px Arial,sans-serif;background:#fff}
.robot{margin-bottom:24px}.robot .rb-btns{display:flex;gap:10px;flex-wrap:wrap}
.robot .warn{color:#8a5a00;font-weight:bold}.robot .err{color:#bd1314}
.robot .rb-result{margin-top:12px;padding:10px 14px;background:#f5f7fa;border-radius:6px;font-size:14px}
.robot .rb-result ul{margin:6px 0 0;padding-left:18px}
.robot .rb-foot{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-top:14px;font-size:13px}
.picker{border:1px solid var(--line);border-radius:6px;overflow:hidden;max-width:820px}
.picker .places{display:flex;flex-wrap:wrap;gap:8px;padding:12px;background:#f5f7fa;border-bottom:1px solid var(--line)}
.picker .crumb{padding:10px 12px;font-size:13px;color:var(--muted);border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;word-break:break-all}
.picker ul{list-style:none;margin:0;padding:0;max-height:320px;overflow:auto}
.picker li button{width:100%;text-align:left;border:0;border-bottom:1px solid #f0f2f6;background:#fff;padding:10px 14px;font:14px Arial,sans-serif;cursor:pointer;color:var(--navy)}
.picker li button:hover{background:#f1f3f8}
.picker .foot{display:flex;align-items:center;gap:12px;padding:12px;background:#f5f7fa;border-top:1px solid var(--line);flex-wrap:wrap}
.chosen{display:flex;flex-direction:column;gap:6px;margin:10px 0}
.chosen div{display:flex;gap:10px;align-items:center;font-size:14px;background:#f5f7fa;border-radius:4px;padding:6px 10px;max-width:820px;word-break:break-all}
.banner{position:fixed;left:50%;transform:translateX(-50%);bottom:24px;background:var(--navy);color:#fff;padding:10px 16px;border-radius:6px;
 box-shadow:0 8px 24px rgba(1,25,69,.3);display:none;align-items:center;gap:12px;z-index:30;font-size:14px}
.banner button{background:var(--gold);border:0;border-radius:4px;padding:6px 12px;font-weight:700;cursor:pointer}
.empty{padding:40px 24px;text-align:center} .empty p{color:var(--muted)}
.msg{padding:10px 14px;border-radius:4px;margin:10px 0;font-size:14px} .msg.err{background:#fbeaea;color:var(--red)} .msg.ok{background:#e3f4ec;color:#11633f}
table.list td,table.list th{vertical-align:middle}
.toggle{display:flex;gap:10px;align-items:center;margin-top:14px;font-size:15px} .toggle input{width:20px;height:20px}
dialog{border:0;border-radius:8px;padding:22px 24px;max-width:880px;width:92vw;box-shadow:0 20px 50px rgba(1,25,69,.35)}
dialog::backdrop{background:rgba(1,25,69,.35)}
"""


def page(title: str, body: str, token: str, active: str = "", extra_head: str = "") -> str:
    nav = "".join(
        f'<a class="nav{" on" if active == key else ""}" href="{href}">{label}</a>'
        for key, href, label in (("events", "/", "Events"), ("profiles", "/profiles", "Robot profiles")))
    nav += '<a class="nav" href="#" onclick="pwQuit();return false;" title="Stop Pitwall on this computer">Quit</a>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="pitwall-token" content="{esc(token)}">
<title>{esc(title)} · Pitwall</title><link rel="icon" href="/icon.svg" type="image/svg+xml">
<style>{base_css()}{APP_CSS}</style><script>{COMMON_JS}</script>{extra_head}</head>
<body>
<header><div class="in"><span class="brand">PITWALL</span><span class="meta">{nav}</span></div></header>
<main>{body}</main>
</body></html>"""


COMMON_JS = r"""
var TOKEN=(document.querySelector('meta[name=pitwall-token]')||{}).content||'';
function api(method,url,body){
  return fetch(url,{method:method,headers:{'Content-Type':'application/json','X-Pitwall-Token':TOKEN},
    body:body===undefined?undefined:JSON.stringify(body)}).then(function(r){
      return r.json().then(function(j){if(!r.ok)throw new Error(j.error||r.statusText);return j;});});
}
function el(id){return document.getElementById(id);}
function pwQuit(){if(!confirm('Stop Pitwall? Watching for new logs stops too. Open it again from the app menu.'))return;
  api('POST','/api/quit',{}).finally(function(){document.body.innerHTML='<main><section class="card empty"><h1>Pitwall has stopped</h1>'+
    '<p>You can close this tab. Open Pitwall again from the app menu.</p></section></main>';});}
function escHtml(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function statusText(s){
  if(!s)return ['','Idle'];
  if(s.state==='error')return ['err','Problem: '+(s.error||'see the terminal')];
  if(s.state==='analyzing')return ['busy','Reading logs '+s.done+' of '+s.total];
  if(s.state==='building')return ['busy','Updating the report'];
  if(s.state==='queued')return ['busy','Update queued'];
  if(s.state==='waiting')return ['',s.detail||'Waiting for logs'];
  if(s.missing_sources&&s.missing_sources.length)return ['','Watched folder not found (USB unplugged?)'];
  if(s.watching&&s.watching.length)return ['ok','Watching for new logs'];
  return ['ok','Up to date'];
}
// Folder picker: places, folders, "use this folder"
function FolderPicker(root,onPick,opts){
  opts=opts||{};var kind=opts.kind||'logs',cur=null;
  root.innerHTML='<div class="picker"><div class="places"></div><div class="crumb"></div><ul></ul>'+
    '<div class="foot"><span class="count"></span><span style="flex:1"></span>'+
    '<button type="button" class="btn primary use">'+(opts.useLabel||'Use this folder')+'</button></div></div>';
  var places=root.querySelector('.places'),crumb=root.querySelector('.crumb'),ul=root.querySelector('ul'),
      count=root.querySelector('.count'),use=root.querySelector('.use');
  function go(path){
    api('GET','/api/browse?kind='+kind+'&path='+encodeURIComponent(path||'')).then(function(d){
      cur=d;
      places.innerHTML='';
      d.places.forEach(function(p){var b=document.createElement('button');b.type='button';b.className='btn small';
        b.textContent=p.label;b.onclick=function(){go(p.path);};places.appendChild(b);});
      crumb.innerHTML='';
      if(d.parent){var up=document.createElement('button');up.type='button';up.className='btn small';up.textContent='Up';
        up.onclick=function(){go(d.parent);};crumb.appendChild(up);}
      var t=document.createElement('span');t.textContent=d.path;crumb.appendChild(t);
      ul.innerHTML='';
      if(d.error){ul.innerHTML='<li style="padding:12px;color:#bd1314">'+escHtml(d.error)+'</li>';}
      d.folders.forEach(function(f){var li=document.createElement('li'),b=document.createElement('button');b.type='button';
        b.textContent='📁 '+f.name;b.onclick=function(){go(f.path);};li.appendChild(b);ul.appendChild(li);});
      if(!d.folders.length&&!d.error)ul.innerHTML='<li style="padding:12px;color:#5a6479">No folders inside.</li>';
      if(kind==='code'){
        count.innerHTML=d.gradle?'<b>Robot project</b> ('+d.logs_below+' Java files)':
          (d.logs_below?d.logs_below+' Java files here. Pick the folder with build.gradle if you can.':'No Java code here');
        use.disabled=!d.logs_below;
      }else{
        count.textContent=d.logs_below?(d.logs_below+' log file'+(d.logs_below===1?'':'s')+' in this folder'+(d.logs_below>d.logs?' and the folders inside it':'')):'No log files here';
        use.disabled=!d.logs_below&&!opts.any;
      }
    }).catch(function(e){crumb.textContent=e.message;});
  }
  use.onclick=function(){if(cur)onPick(cur.path,cur.logs_below,cur);};
  go(opts.start||'');
  return {go:go};
}
"""


# ---------------------------------------------------------------- home

def home(events: List[Dict[str, Any]], status: Dict[str, Dict[str, Any]], profiles: List[Dict[str, Any]],
         token: str) -> str:
    if not profiles:
        body = """<div class="card empty"><h1>Welcome to Pitwall</h1>
<p>First, tell Pitwall about your robot. It reads the robot code to find the motors and where the logs keep their data,
then you check what it found. That's saved on this computer and used for every event.</p>
<p><a class="btn primary big-btn" href="/profiles/new">Set up your robot</a></p>
<p class="hint">You'll need the robot code and a log from the robot. <a href="/profiles">Other ways to add a robot</a></p></div>"""
        return page("Pitwall", body, token, "events")
    cards = []
    for e in events:
        s = e.get("summary") or {}
        st = status.get(e["id"], {})
        when = nice_date(s.get("first_stamp") or "") or "No matches yet"
        nums = ""
        if s.get("counted"):
            nums = (f'<div class="nums"><div><b>{s["ah"]:.2f}</b><span>Ah per match</span></div>'
                    f'<div><b>{s["with_brownout"]} of {s["brownout_matches"]}</b><span>matches with a brownout</span></div>'
                    + (f'<div><b>{s["hub_shots"]:.0f}</b><span>hub shots per match</span></div>'
                       if s.get("hub_shots") is not None else
                       f'<div><b>{s["counted"]}</b><span>matches counted</span></div>')
                    + '</div>')
            if s.get("hub_shots") is not None:
                when += f' · {s["counted"]} matches counted'
        cards.append(f'<a class="evcard" href="/events/{esc(e["id"])}/"><h3>{esc(e["name"] or "New event")}</h3>'
                     f'<div class="meta">{esc(s.get("code") or "")}{" · " if s.get("code") else ""}{esc(when)}</div>{nums}'
                     f'<span class="pill" data-ev="{esc(e["id"])}"><i></i>{esc(st.get("state", ""))}</span></a>')
    grid = ('<div class="cards">' + "".join(cards) + "</div>") if cards else (
        '<div class="card empty"><h2>No events yet</h2><p>Make one, then point Pitwall at the folder or USB stick '
        'with the robot logs. New logs are picked up as they arrive.</p></div>')
    body = f"""{ROBOT_PANEL}<div class="row"><div><p class="eyebrow">Events</p><h1>Robot logs, by event</h1></div>
<a class="btn primary big-btn" href="/events/new">+ New event</a></div>
{grid}
<script>
function poll(){{api('GET','/api/status').then(function(all){{
  document.querySelectorAll('.pill[data-ev]').forEach(function(p){{var t=statusText(all[p.dataset.ev]);
    p.className='pill '+t[0];p.innerHTML='<i></i>'+escHtml(t[1]);}});}}).catch(function(){{}});}}
poll();setInterval(poll,3000);
</script>"""
    return page("Events", body, token, "events")


# ---------------------------------------------------------------- robot panel

ROBOT_PANEL = r"""<section class="card robot" id="robot"><div class="row" style="margin:0">
<div><p class="eyebrow">Robot</p><h2 id="rb-head" style="margin:0">Looking for the robot…</h2>
<div id="rb-lines" class="meta"></div></div>
<div class="rb-btns"><button class="btn primary" id="rb-get" disabled>Get logs</button>
<button class="btn" id="rb-stick">From a USB stick…</button></div></div>
<div id="rb-last"></div>
<div class="rb-foot meta"><label><input type="checkbox" id="rb-auto"> Fetch new logs by itself when the robot
is tethered (USB or Ethernet, never over the radio)</label>
<span>Archive: <span id="rb-arch"></span> <a href="#" id="rb-arch-change">Change</a></span></div>
<div id="rb-pick" style="display:none;margin-top:12px"><p id="rb-pick-what" class="hint"></p><div id="rb-picker"></div></div>
<div id="rb-msg"></div></section>
<script>
(function(){
var RB=null,picker=null,pickFor=null;
function gb(b){return b>=1e9?(b/1e9).toFixed(1)+' GB':Math.max(1,Math.round(b/1e6))+' MB';}
function ago(t){var s=Math.round(Date.now()/1000-t);return s<90?'just now':s<5400?Math.round(s/60)+' min ago':
  Math.round(s/3600)+' h ago';}
function line(t,cls){return '<div'+(cls?' class="'+cls+'"':'')+'>'+t+'</div>';}
function render(r){RB=r;var h='',L='';
  if(!r.archive){h='Where should fetched logs go?';
    L=line('Choose the team log archive folder: the one that holds the season folders (2026, 2027, …).');
    el('rb-get').disabled=true;}
  else if(r.fetching){var p=r.progress||{};h='Fetching logs…';
    L=line(p.stage==='copy'&&p.total?'Copying '+Math.min(p.done+1,p.total)+' of '+p.total+(p.name?': '+escHtml(p.name):''):
      p.stage==='file'&&p.total?'Sorting into the archive ('+p.done+' of '+p.total+')':'Looking at the stick');}
  else if(!r.sftp){h='Fetching from the robot needs one more package';
    L=line('Install it with <code>'+escHtml(r.install_hint)+'</code>, then restart Pitwall. '+
      'You can still fetch from the robot\'s stick plugged into this computer.');}
  else if(r.connected){var rb=r.robot;h='Robot connected over '+escHtml(rb.via)+' <span class="meta">('+escHtml(rb.host)+')</span>';
    var st=r.stick;
    if(st){var sp=st.free!=null?' · '+gb(st.free)+' free of '+gb(st.total):'';
      L+=line(st.files.toLocaleString()+' log files on its USB stick'+sp);
      if(st.files>=r.clear_at)L+=line('Time to clear the stick: it holds '+st.files.toLocaleString()+' files. '+
        (r.waiting===0?'Everything on it is in the archive.':'Get logs first, so everything on it is in the archive.'),'warn');}
    if(r.waiting!=null)L+=line(r.waiting?'<b>'+r.waiting+' new log file'+(r.waiting===1?'':'s')+'</b> ('+gb(r.waiting_bytes)+') not in the archive yet':
      'Every log on the robot is in the archive');}
  else{h='No robot found';L=line('Connect by USB or Ethernet (or the radio). Or plug the robot\'s USB stick into this computer.');}
  if(r.error)L+=line(escHtml(r.error),'err');
  el('rb-head').innerHTML=h;el('rb-lines').innerHTML=L;
  el('rb-get').disabled=!(r.archive&&r.sftp&&r.connected&&!r.fetching);
  el('rb-stick').disabled=!!r.fetching;
  el('rb-auto').checked=!!r.auto_fetch;
  el('rb-arch').textContent=r.archive||'not chosen';el('rb-arch-change').textContent=r.archive?'Change':'Choose';
  var last=r.last,X='';
  if(last){X='<div class="rb-result"><b>Last fetch, '+ago(last.at)+'</b> from '+escHtml(last.source)+'<ul>';
    (last.handed||[]).forEach(function(e){X+='<li>'+escHtml(e.labels.join(', '))+' added to <a href="/events/'+
      encodeURIComponent(e.id)+'/">'+escHtml(e.name)+'</a></li>';});
    Object.keys(last.no_event||{}).forEach(function(c){X+='<li>'+last.no_event[c]+' '+escHtml(c)+
      ' match log(s) are in the archive, but no event here has code '+escHtml(c)+'. <a href="/events/new">Make one</a></li>';});
    X+='<li>'+escHtml(last.lines[0])+'</li>';
    if(last.growing&&last.growing.length)X+='<li>Left for next time (still being written): '+escHtml(last.growing.join(', '))+'</li>';
    if(last.errors)X+='<li class="err">'+last.errors+' file(s) couldn\'t be copied: see below</li>';
    X+='</ul></div>';}
  el('rb-last').innerHTML=X;
}
function msg(t){el('rb-msg').innerHTML=t?'<div class="msg err">'+escHtml(t)+'</div>':'';}
function poll(){api('GET','/api/robot').then(render).catch(function(){});}
function openPicker(what){pickFor=what;el('rb-pick').style.display='';
  el('rb-pick-what').textContent=what==='archive'?'Pick the folder that holds the season folders (2026, 2027, …).':
    'Pick the robot\'s USB stick (or the folder of logs on it).';
  picker=FolderPicker(el('rb-picker'),function(path){el('rb-pick').style.display='none';
    if(pickFor==='archive')api('POST','/api/robot/settings',{archive:path}).then(function(r){msg('');render(r);}).catch(function(e){msg(e.message);});
    else api('POST','/api/robot/fetch',{folder:path}).then(function(){msg('');setTimeout(poll,300);}).catch(function(e){msg(e.message);});
  },{kind:'logs',any:what==='archive',useLabel:what==='archive'?'Use this folder':'Fetch from here',start:what==='archive'?(RB&&RB.archive)||'':''});}
el('rb-get').onclick=function(){
  if(RB&&RB.waiting>RB.auto_max&&!confirm('Copy '+RB.waiting+' log files ('+gb(RB.waiting_bytes)+') from the robot? '+
    'That can take a while. If they are already in the archive, add them to its index first (pitwall fetch --index-existing).'))return;
  api('POST','/api/robot/fetch',{}).then(function(){msg('');setTimeout(poll,300);}).catch(function(e){msg(e.message);});};
el('rb-stick').onclick=function(){if(RB&&!RB.archive)return openPicker('archive');openPicker('stick');};
el('rb-arch-change').onclick=function(e){e.preventDefault();openPicker('archive');};
el('rb-auto').onchange=function(){api('POST','/api/robot/settings',{auto_fetch:this.checked}).then(render).catch(function(e){msg(e.message);});};
poll();setInterval(poll,3000);
})();
</script>"""


# ---------------------------------------------------------------- new event

def new_event(profiles: List[Dict[str, Any]], token: str) -> str:
    opts = "".join(f'<option value="{esc(p["id"])}">{esc(p["name"])}</option>' for p in profiles if not p["error"])
    body = f"""<div><p class="eyebrow">New event</p><h1>Where are the logs?</h1></div>
<section class="card">
<p class="lead">Pick the folder the robot logs are in: the USB stick from the robot, or the folder they're copied to.
Pitwall copies the logs it finds, then keeps watching the folder and adds each new match as it arrives.</p>
<div id="pick"></div>
<div class="chosen" id="chosen"></div>
<label class="f" for="name">Event name</label>
<input type="text" id="name" placeholder="Filled in from the logs if you leave it blank">
<label class="f" for="profile">Robot</label>
<select id="profile">{opts}</select>
<label class="toggle"><input type="checkbox" id="watch" checked> Keep watching this folder for new logs</label>
<div id="err"></div>
<p style="margin-top:22px"><button class="btn primary big-btn" id="go" disabled>Create event</button></p>
</section>
<script>
var sources=[];
function show(){{el('chosen').innerHTML=sources.map(function(s,i){{return '<div>📁 <span style="flex:1">'+escHtml(s.path)+
  '</span><span class="muted">'+s.n+' logs</span><button class="btn small" onclick="sources.splice('+i+',1);show()">Remove</button></div>';}}).join('');
  el('go').disabled=!sources.length;}}
FolderPicker(el('pick'),function(path,n){{if(!sources.some(function(s){{return s.path===path;}}))sources.push({{path:path,n:n}});show();}});
el('go').onclick=function(){{el('go').disabled=true;el('go').textContent='Creating…';
  api('POST','/api/events',{{name:el('name').value,profile:el('profile').value,watch:el('watch').checked,
    sources:sources.map(function(s){{return s.path;}})}})
  .then(function(r){{location.href='/events/'+r.id+'/';}})
  .catch(function(e){{el('err').innerHTML='<div class="msg err">'+escHtml(e.message)+'</div>';el('go').disabled=false;el('go').textContent='Create event';}});}};
</script>"""
    return page("New event", body, token, "events")


# ---------------------------------------------------------------- event toolbar (injected into report pages)

def event_toolbar(ev: Dict[str, Any], st: Dict[str, Any], token: str, at_match: bool) -> str:
    back = (f'<a class="btn small" href="/events/{esc(ev["id"])}/">← Event</a>' if at_match
            else '<a class="btn small" href="/">← All events</a>')
    return f"""<div class="appbar" id="pw-bar"><div class="in">{back}
<span class="title">{esc(ev["name"] or "Event")}</span><span class="pill" id="pw-st"><i></i>…</span><span class="sp"></span>
<button class="btn small" id="pw-add">Add logs</button>
<a class="btn small" href="/events/{esc(ev["id"])}/settings">Settings</a>
<a class="btn small" href="/events/{esc(ev["id"])}/download">Download report</a></div></div>
<div class="banner" id="pw-new">New logs are in. <button id="pw-reload">Show the update</button></div>
<dialog id="pw-dlg"><h2>Add logs</h2><p class="lead">Pick a folder with more logs for this event, such as a USB stick.
Pitwall copies them and watches the folder for more.</p><div id="pw-pick"></div>
<p><button class="btn" onclick="el('pw-dlg').close()">Cancel</button></p></dialog>
<script>
(function(){{
var TOKEN0={json.dumps(token)};
var EV={json.dumps(ev["id"])},VER={int(st.get("version", 0))},picker=null;
function api0(m,u,b){{return fetch(u,{{method:m,headers:{{'Content-Type':'application/json','X-Pitwall-Token':TOKEN0}},
  body:b===undefined?undefined:JSON.stringify(b)}}).then(function(r){{return r.json();}});}}
function poll(){{api0('GET','/api/events/'+EV+'/status').then(function(s){{
  var t=statusText(s),p=document.getElementById('pw-st');p.className='pill '+t[0];p.innerHTML='<i></i>'+t[1].replace(/</g,'&lt;');
  if(s.version>VER&&s.state!=='analyzing'&&s.state!=='building'){{
    if(window.scrollY<80)location.reload();
    else document.getElementById('pw-new').style.display='flex';}}
}}).catch(function(){{}});}}
document.getElementById('pw-reload').onclick=function(){{location.reload();}};
document.getElementById('pw-add').onclick=function(){{var d=document.getElementById('pw-dlg');d.showModal();
  if(!picker&&window.FolderPicker)picker=FolderPicker(document.getElementById('pw-pick'),function(path){{
    api0('POST','/api/events/'+EV+'/sources',{{add:path}}).then(function(){{d.close();poll();}});}});}};
poll();setInterval(poll,2000);
}})();
</script>"""


def inject(report_html: str, toolbar: str, token: str) -> str:
    """Put the app toolbar (and the picker script) at the top of a report page."""
    head_add = f"<style>{APP_CSS}</style>"
    report_html = report_html.replace("</head>", head_add + "</head>", 1)
    script = f'<meta name="pitwall-token" content="{esc(token)}"><script>{COMMON_JS}</script>'
    return report_html.replace("<body>", "<body>" + script + toolbar, 1)


def waiting_page(ev: Dict[str, Any], st: Dict[str, Any], logs_dir: str, token: str) -> str:
    srcs = "".join(f"<li>{esc(s)}</li>" for s in ev["sources"]) or "<li>No folder chosen yet</li>"
    body = f"""<section class="card empty"><h2>No report yet</h2>
<p>Pitwall makes the report once it has at least one match log with motor current.</p>
<p>Watching:</p><ul class="plain" style="display:inline-block;text-align:left">{srcs}</ul>
<p class="hint">Or copy logs straight into {esc(logs_dir)}</p></section>"""
    html = page(ev["name"] or "Event", body, token, "events")
    return inject(html, event_toolbar(ev, st, token, at_match=False), token)


# ---------------------------------------------------------------- event settings

def event_settings(ev: Dict[str, Any], summary: Optional[Dict[str, Any]], profiles: List[Dict[str, Any]],
                   others: List[Dict[str, Any]], token: str) -> str:
    popts = "".join(f'<option value="{esc(p["id"])}"{" selected" if p["id"] == ev["profile"] else ""}>{esc(p["name"])}</option>'
                    for p in profiles if not p["error"])
    copts = [('auto', 'The latest earlier event for this robot'), ('none', 'Don\'t compare')]
    copts += [(o["id"], o["name"]) for o in others if o["id"] != ev["id"]]
    copt_html = "".join(f'<option value="{esc(v)}"{" selected" if v == ev["compare"] else ""}>{esc(t)}</option>' for v, t in copts)
    rows = []
    for m in (summary or {}).get("matches", []):
        auto_reason = m["why_not"] if m["why_not"] not in ("Excluded by hand",) else None
        rows.append(
            f'<tr><td>{esc(m["label"])}</td><td class="r num">{m["ah"]:.2f} Ah</td><td class="r num">{m["enabled_s"]:.0f} s</td>'
            f'<td>{esc(auto_reason or "")}</td>'
            f'<td><label class="toggle" style="margin:0"><input type="checkbox" class="cnt" data-label="{esc(m["label"])}"'
            f'{" checked" if m["counted"] else ""}> Count</label></td></tr>')
    matches = ("<div class=\"scroll\"><table class=\"list\"><thead><tr><th>Match</th><th class=\"r\">Charge</th>"
               "<th class=\"r\">Enabled</th><th>Why Pitwall would leave it out</th><th>In the averages</th></tr></thead>"
               f"<tbody>{''.join(rows)}</tbody></table></div>") if rows else "<p class=\"muted\">No matches yet.</p>"
    auto_out = {m["label"] for m in (summary or {}).get("matches", [])
                if m["why_not"] and m["why_not"] != "Excluded by hand" and not m.get("included_by_hand")}
    srcs = "".join(
        f'<div>\U0001F4C1 <span style="flex:1">{esc(s)}</span><button class="btn small" data-rm="{esc(s)}">Stop watching</button></div>'
        for s in ev["sources"]) or '<p class="muted">None.</p>'
    body = f"""<div><p class="eyebrow">Settings</p><h1>{esc(ev["name"] or "Event")}</h1></div>
<section class="card">
<label class="f" for="name">Event name</label><input type="text" id="name" value="{esc(ev["name"])}">
<label class="f" for="profile">Robot</label><select id="profile">{popts}</select>
<label class="f" for="cmp">Compare with</label><select id="cmp">{copt_html}</select>
<label class="toggle"><input type="checkbox" id="watch"{" checked" if ev["watch"] else ""}> Watch the folders below for new logs</label>
<label class="f">Watched folders</label><div class="chosen">{srcs}</div>
</section>
<section class="card"><h2>Which matches count</h2>
<p class="lead">Every match is in the report. Untick one to leave it out of the event averages, or tick one Pitwall left out.</p>
{matches}</section>
<div id="msg"></div>
<p class="row"><span><button class="btn primary big-btn" id="save">Save and update</button>
<a class="btn" href="/events/{esc(ev["id"])}/">Cancel</a></span>
<button class="btn danger" id="del">Delete this event</button></p>
<script>
var AUTO_OUT={json.dumps(sorted(auto_out))};
document.querySelectorAll('[data-rm]').forEach(function(b){{b.onclick=function(){{
  api('POST','/api/events/{esc(ev["id"])}/sources',{{remove:b.dataset.rm}}).then(function(){{location.reload();}});}};}});
el('save').onclick=function(){{
  var inc=[],exc=[];
  document.querySelectorAll('.cnt').forEach(function(c){{var l=c.dataset.label,auto=AUTO_OUT.indexOf(l)<0;
    if(c.checked&&!auto)inc.push(l); if(!c.checked&&auto)exc.push(l);}});
  api('POST','/api/events/{esc(ev["id"])}/settings',{{name:el('name').value,profile:el('profile').value,compare:el('cmp').value,
    watch:el('watch').checked,include:inc,exclude:exc}}).then(function(){{location.href='/events/{esc(ev["id"])}/';}})
  .catch(function(e){{el('msg').innerHTML='<div class="msg err">'+escHtml(e.message)+'</div>';}});}};
el('del').onclick=function(){{if(!confirm('Delete this event and its copied logs from this computer? The original logs are not touched.'))return;
  api('POST','/api/events/{esc(ev["id"])}/delete',{{}}).then(function(){{location.href='/';}});}};
</script>"""
    return page("Settings", body, token, "events")


# ---------------------------------------------------------------- profiles

def profiles_page(profiles: List[Dict[str, Any]], used: Dict[str, List[str]], builtins: List[str], token: str) -> str:
    rows = []
    for p in profiles:
        state = f'<span class="flag">problem</span> {esc(p["error"])}' if p["error"] else f'{p["motors"]} motors, {p["subsystems"]} subsystems'
        rows.append(f'<tr><td><a href="/profiles/{esc(p["id"])}">{esc(p["name"])}</a></td><td>{state}</td>'
                    f'<td>{esc(", ".join(used.get(p["id"], [])) or "—")}</td>'
                    f'<td class="r"><a class="btn small" href="/profiles/{esc(p["id"])}/download">Download</a> '
                    f'<button class="btn small danger" data-del="{esc(p["id"])}">Delete</button></td></tr>')
    table = (f'<table class="list"><thead><tr><th>Robot</th><th>Contents</th><th>Used by</th><th></th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>') if rows else '<p class="muted">No robot profiles on this computer yet.</p>'
    ex = "".join(f'<button class="btn small" data-builtin="{esc(b)}">Use the example: {esc(b)}</button> ' for b in builtins)
    body = f"""<div><p class="eyebrow">Robot profiles</p><h1>Robots on this computer</h1></div>
<section class="card">{table}</section>
<section class="card"><h2>Add a robot</h2>
<p class="lead">A profile lists the robot's motors, which group each belongs to, and the log keys with its data.
Profiles stay on this computer.</p>
<p><a class="btn primary big-btn" href="/profiles/new">Set up from the robot code</a></p>
<p class="hint">Or: <label class="btn small">Import a profile file (.toml)<input type="file" id="file" accept=".toml" hidden></label> {ex}</p>
<div id="msg"></div></section>
<script>
function done(){{location.reload();}}
function fail(e){{el('msg').innerHTML='<div class="msg err">'+escHtml(e.message)+'</div>';}}
el('file').onchange=function(){{var f=el('file').files[0];if(!f)return;f.text().then(function(t){{
  return api('POST','/api/profiles',{{toml:t,name:f.name.replace(/\\.toml$/,'')}});}}).then(done).catch(fail);}};
document.querySelectorAll('[data-builtin]').forEach(function(b){{b.onclick=function(){{
  api('POST','/api/profiles',{{builtin:b.dataset.builtin}}).then(done).catch(fail);}};}});
document.querySelectorAll('[data-del]').forEach(function(b){{b.onclick=function(){{
  if(!confirm('Delete this robot profile from this computer?'))return;
  api('POST','/api/profiles/'+b.dataset.del+'/delete',{{}}).then(done).catch(fail);}};}});
</script>"""
    return page("Robot profiles", body, token, "profiles")


def profile_view(pid: str, prof, token: str, just_made: bool = False) -> str:
    subs = {s.id: s for s in prof.subsystems}
    from ..report.html import color_for
    mrows = "".join(
        f'<tr><td><i class="sw" style="background:{color_for(subs[m.subsystem].color)}"></i>{esc(m.name)}</td>'
        f'<td>{esc(subs[m.subsystem].name)}</td><td class="r num">{esc(m.can_id if m.can_id is not None else "")}</td>'
        f'<td class="small">{esc(m.supply[0] if m.supply else "")}'
        f'{" <span class=muted>+" + str(len(m.supply) - 1) + " fallback</span>" if len(m.supply) > 1 else ""}</td></tr>'
        for m in prof.motors)
    states = "".join(f"<li>{esc(s.name)}</li>" for s in prof.states) or "<li>None</li>"
    periods = "".join(f"<li>{esc(p.name)}: {p.from_left:g}–{p.to_left:g} s left"
                      f"{'' if p.hub == 'active' else ' (hub depends on the match)'}</li>" for p in prof.periods) or "<li>Quarters of the match</li>"
    made = ('<div class="msg ok">Saved. This robot is ready: <a href="/events/new">make an event</a> with its logs.</div>'
            if just_made else "")
    body = f"""<div><p class="eyebrow">Robot profile</p><h1>{esc(prof.name)}</h1></div>{made}
<section class="card"><h2>Motors</h2><div class="scroll"><table class="list"><thead><tr><th>Motor</th><th>Subsystem</th>
<th class="r">CAN</th><th>Supply current key</th></tr></thead><tbody>{mrows}</tbody></table></div></section>
<div class="grid2"><section class="card"><h2>Robot states</h2><ul class="plain">{states}</ul></section>
<section class="card"><h2>Match periods</h2><ul class="plain">{periods}</ul></section></div>
<p><a class="btn" href="/profiles">← Robot profiles</a> <a class="btn" href="/profiles/{esc(pid)}/download">Download the .toml</a></p>"""
    return page(prof.name, body, token, "profiles")
