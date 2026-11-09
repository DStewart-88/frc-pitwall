"""The guided robot setup: robot code + one log -> a reviewed robot profile."""

from .pages import page

SETUP_CSS = """
.steps{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.steps span{font:11px Michroma,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding:6px 10px;border-radius:999px;background:#edf0f6}
.steps span.on{background:var(--navy);color:#fff}
.picked{display:flex;gap:12px;align-items:center;background:#f5f7fa;border-radius:4px;padding:10px 12px;margin:8px 0;word-break:break-all;max-width:880px}
.badge{display:inline-block;font:10px Michroma,sans-serif;letter-spacing:.08em;text-transform:uppercase;border-radius:2px;padding:3px 6px;white-space:nowrap}
.b-traced{background:#e3f4ec;color:#11633f} .b-named{background:#fef0d8;color:#8a5305} .b-unchecked{background:#edf0f6;color:#0b2a61}
.b-picked{background:#e3f4ec;color:#11633f} .b-missing,.b-not-in-log{background:#fbeaea;color:var(--red)}
table.rev input[type=text]{min-height:34px;font-size:14px;max-width:none}
table.rev select{min-height:34px;font-size:13px;max-width:360px}
table.rev td{vertical-align:middle;padding:6px 8px} tr.off td{opacity:.45}
.more{font-size:12px;color:var(--muted)} .more code{font-size:11.5px}
details summary{cursor:pointer;color:#0b4fa8;font-size:12px}
.subs{display:flex;flex-direction:column;gap:8px} .subs div{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.legendkey{display:inline-flex;gap:14px;flex-wrap:wrap;font-size:13px;margin:6px 0 10px}
"""


def setup_page(token: str) -> str:
    body = r"""<div><p class="eyebrow">Robot setup</p><h1>Set up a robot from its code</h1></div>
<div class="steps"><span id="s1" class="on">1 · Robot code</span><span id="s2">2 · A log</span><span id="s3">3 · Check</span></div>

<section class="card" id="c1"><h2>Where is the robot code?</h2>
<p class="lead">Pick the robot project folder (the one with build.gradle). Pitwall reads the Java to find every motor,
its CAN ID, and the log keys its data is saved under. Nothing in the code is changed.</p>
<div id="codepick"></div><div id="codechosen"></div></section>

<section class="card" id="c2" hidden><h2>A log from this robot</h2>
<p class="lead">Pick a folder of logs from this robot. Pitwall checks the keys it found in the code against one log,
and finds any it couldn't trace. A match log works best.</p>
<div id="logpick"></div><div id="logchosen"></div>
<p><button class="btn primary big-btn" id="scan" disabled>Read the code</button>
<button class="btn" id="nolog">I don't have a log yet</button></p><div id="scanmsg"></div></section>

<div id="review" hidden>
<section class="card"><h2>Check the motors</h2>
<p class="lead">This is what Pitwall found. Rename anything, change which group a motor is in (type a new group name
to make one; use the same name to merge), and untick anything that isn't on the robot.</p>
<div class="legendkey"><span><span class="badge b-traced">From code</span> found in the code and in the log</span>
<span><span class="badge b-named">By name</span> matched in the log by name: check it</span>
<span><span class="badge b-missing">Missing</span> pick the key, or untick the motor</span></div>
<label class="f" for="rname">Robot name</label><input type="text" id="rname">
<div id="notes"></div>
<div class="scroll"><table class="rev"><thead><tr><th></th><th>Motor</th><th>Group</th><th class="r">CAN</th>
<th>Supply current</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
<datalist id="groups"></datalist>
<div id="unclaimed"></div>
</section>
<section class="card"><h2>Groups</h2>
<p class="lead">Each group is one bar in the reports. Tick "only needed while shooting" for mechanisms that should be
idle otherwise; Pitwall then reports any current they draw at other times as idle draw.</p>
<div class="subs" id="subs"></div></section>
<section class="card"><h2>The game and robot states</h2>
<label class="f" for="game">Match periods</label><select id="game"></select>
<label class="f" for="shoot">"Shooting" signal</label><select id="shoot"></select>
<p class="hint">A true/false log key that's on while the robot shoots. Reports split each group's charge by shooting, driving and still.</p>
</section>
<div id="savemsg"></div>
<p><button class="btn primary big-btn" id="save">Save this robot</button> <a class="btn" href="/profiles">Cancel</a></p>
</div>

<script>
var CODE=null,LOGDIR=null,LOGS=[],DRAFT=null;
FolderPicker(el('codepick'),function(path,n,d){
  CODE=path;el('codechosen').innerHTML='<div class="picked">\uD83D\uDCC1 <span style="flex:1">'+escHtml(path)+'</span><span class="muted">'+n+' Java files</span></div>'+
    (d.gradle?'':'<div class="msg err">No build.gradle in this folder. That\'s fine if the Java is here, but the project folder is usually one level up.</div>');
  el('c2').hidden=false;el('s2').className='on';el('c2').scrollIntoView({behavior:'smooth'});
},{kind:'code',useLabel:'This is the robot code'});
FolderPicker(el('logpick'),function(path){
  LOGDIR=path;api('GET','/api/setup/logs?path='+encodeURIComponent(path)).then(function(list){
    LOGS=list;var o=list.slice(0,60).map(function(l,i){return '<option value="'+i+'">'+escHtml(l.name)+' ('+(l.bytes/1048576).toFixed(0)+' MB)</option>';}).join('');
    el('logchosen').innerHTML='<label class="f" for="logsel">Log to check against</label><select id="logsel">'+o+'</select>';
    el('scan').disabled=!list.length;
  });
},{kind:'logs',useLabel:'Use logs from here'});
function doScan(withLog){
  el('scan').disabled=true;el('scanmsg').innerHTML='<p class="muted">Reading the code'+(withLog?' and the log':'')+'…</p>';
  var lp=withLog&&LOGS.length?LOGS[+el('logsel').value].path:null;
  api('POST','/api/setup/scan',{code:CODE,log:lp}).then(function(d){DRAFT=d;el('scanmsg').innerHTML='';showReview();})
  .catch(function(e){el('scanmsg').innerHTML='<div class="msg err">'+escHtml(e.message)+'</div>';el('scan').disabled=false;});
}
el('scan').onclick=function(){doScan(true);};
el('nolog').onclick=function(){if(!CODE)return;doScan(false);};

var LABEL={traced:'From code',named:'By name',unchecked:'From code',missing:'Missing','not-in-log':'Not in log',picked:'Your pick'};
function groupNames(){var g=[];DRAFT.motors.forEach(function(m){if(m.include&&g.indexOf(m.subsystem)<0)g.push(m.subsystem);});return g;}
function showReview(){
  el('review').hidden=false;el('s3').className='on';
  el('rname').value=DRAFT.robot_name;
  el('notes').innerHTML=DRAFT.notes.map(function(n){return '<div class="msg err">'+escHtml(n)+'</div>';}).join('')+
    (DRAFT.battery_key_ok?'':'<div class="msg err">The log has no /SystemStats/BatteryVoltage key, so reports can\'t show battery voltage.</div>');
  renderRows();renderUnclaimed();renderSubs();
  el('game').innerHTML=Object.keys(DRAFT.games).map(function(k){return '<option value="'+k+'"'+(k===DRAFT.game?' selected':'')+'>'+escHtml(DRAFT.games[k])+'</option>';}).join('');
  var sc=DRAFT.shooting_choices.slice();if(DRAFT.shooting_key&&sc.indexOf(DRAFT.shooting_key)<0)sc.unshift(DRAFT.shooting_key);
  el('shoot').innerHTML='<option value="">(none)</option>'+sc.map(function(k){return '<option'+(k===DRAFT.shooting_key?' selected':'')+'>'+escHtml(k)+'</option>';}).join('');
  el('review').scrollIntoView({behavior:'smooth'});
}
function keySelect(m,i){
  var opts=DRAFT.supply_keys.slice(),cur=m.keys.supply||'';
  if(cur&&opts.indexOf(cur)<0)opts.unshift(cur);
  if(!opts.length)return '<input type="text" data-i="'+i+'" class="ksel" value="'+escHtml(cur)+'" placeholder="/Subsystem/MotorSupplyCurrent">';
  return '<select data-i="'+i+'" class="ksel"><option value="">(none)</option>'+opts.map(function(k){
    return '<option'+(k===cur?' selected':'')+'>'+escHtml(k)+'</option>';}).join('')+'</select>';
}
function renderRows(){
  el('rows').innerHTML=DRAFT.motors.map(function(m,i){
    var st=m.status.supply||'missing';
    var more=Object.keys(m.keys).filter(function(k){return k!=='supply';}).map(function(k){
      return k+': <code>'+escHtml(m.keys[k])+'</code>';}).join('<br>');
    return '<tr class="'+(m.include?'':'off')+'"><td><input type="checkbox" class="inc" data-i="'+i+'"'+(m.include?' checked':'')+'></td>'+
      '<td><input type="text" class="nm" data-i="'+i+'" value="'+escHtml(m.name)+'"></td>'+
      '<td><input type="text" class="grp" list="groups" data-i="'+i+'" value="'+escHtml(m.subsystem)+'"></td>'+
      '<td class="r num">'+(m.can_id===null?'—':m.can_id)+(m.bus&&m.bus!=='rio'?'<br><span class="muted small">'+escHtml(m.bus)+'</span>':'')+'</td>'+
      '<td><span class="badge b-'+st+'">'+LABEL[st]+'</span><br>'+keySelect(m,i)+'</td>'+
      '<td class="more"><details><summary>Details</summary>'+escHtml(m.controller)+' · '+escHtml(m.source)+(more?'<br>'+more:'')+'</details></td></tr>';
  }).join('');
  el('groups').innerHTML=groupNames().map(function(g){return '<option value="'+escHtml(g)+'">';}).join('');
  document.querySelectorAll('.inc').forEach(function(c){c.onchange=function(){DRAFT.motors[c.dataset.i].include=c.checked;renderRows();renderSubs();};});
  document.querySelectorAll('.nm').forEach(function(c){c.onchange=function(){DRAFT.motors[c.dataset.i].name=c.value.trim()||DRAFT.motors[c.dataset.i].name;};});
  document.querySelectorAll('.grp').forEach(function(c){c.onchange=function(){DRAFT.motors[c.dataset.i].subsystem=c.value.trim()||'Other';renderSubs();
    el('groups').innerHTML=groupNames().map(function(g){return '<option value="'+escHtml(g)+'">';}).join('');};});
  document.querySelectorAll('.ksel').forEach(function(c){c.onchange=function(){var m=DRAFT.motors[c.dataset.i];m.keys.supply=c.value;
    m.status.supply=c.value?'picked':'missing';renderRows();renderUnclaimed();};});
}
function renderUnclaimed(){
  var used=DRAFT.motors.map(function(m){return m.keys.supply;});
  var left=DRAFT.unclaimed.filter(function(k){return used.indexOf(k)<0;});
  el('unclaimed').innerHTML=left.length?'<h3>Supply current in the log that no motor uses</h3><p class="hint">Probably a motor the code reader '+
    'didn\'t recognize. Add it, or leave it out.</p>'+left.map(function(k){return '<div class="picked"><code style="flex:1">'+escHtml(k)+
    '</code><button class="btn small" data-add="'+escHtml(k)+'">Add as a motor</button></div>';}).join(''):'';
  document.querySelectorAll('[data-add]').forEach(function(b){b.onclick=function(){var k=b.dataset.add,parts=k.split('/').filter(Boolean);
    var nm=(parts[parts.length-1]||'Motor').replace(/SupplyCurrent(Amps)?$/,'').replace(/([a-z])([A-Z])/g,'$1 $2')||parts[parts.length-2];
    DRAFT.motors.push({include:true,name:nm,subsystem:parts.length>2?parts[parts.length-2]:'Other',controller:'',can_id:null,bus:'',
      keys:{supply:k},status:{supply:'named'},source:'added from the log',key_source:{}});renderRows();renderUnclaimed();renderSubs();};});
}
function renderSubs(){
  var keep={};(DRAFT.subsystems||[]).forEach(function(s){keep[s.name]=s.needed_in||[];});
  DRAFT.subsystems=groupNames().map(function(g){return {name:g,needed_in:keep[g]||[]};});
  el('subs').innerHTML=DRAFT.subsystems.map(function(s,i){var n=DRAFT.motors.filter(function(m){return m.include&&m.subsystem===s.name;}).length;
    return '<div><b style="min-width:200px">'+escHtml(s.name)+'</b><span class="muted">'+n+' motor'+(n===1?'':'s')+'</span>'+
      '<label class="toggle" style="margin:0"><input type="checkbox" class="need" data-i="'+i+'"'+(s.needed_in.indexOf('Shooting')>=0?' checked':'')+
      '> only needed while shooting</label></div>';}).join('');
  document.querySelectorAll('.need').forEach(function(c){c.onchange=function(){DRAFT.subsystems[c.dataset.i].needed_in=c.checked?['Shooting']:[];};});
}
el('save').onclick=function(){
  DRAFT.robot_name=el('rname').value.trim()||'Robot';DRAFT.game=el('game').value;DRAFT.shooting_key=el('shoot').value;
  var miss=DRAFT.motors.filter(function(m){return m.include&&!m.keys.supply;});
  if(miss.length&&!confirm(miss.length+' motor(s) have no supply-current key and will be left out: '+miss.map(function(m){return m.name;}).join(', ')+'. Save anyway?'))return;
  el('save').disabled=true;
  api('POST','/api/setup/save',{draft:DRAFT}).then(function(r){location.href='/profiles/'+r.id+'?new=1';})
  .catch(function(e){el('savemsg').innerHTML='<div class="msg err">'+escHtml(e.message)+'</div>';el('save').disabled=false;});
};
</script>"""
    return page("Robot setup", body, token, "profiles", extra_head=f"<style>{SETUP_CSS}</style>")
