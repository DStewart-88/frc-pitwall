"""Tabs for the report pages: Overview, then one tab per module (Power, Scoring, ...).

Every tab is in the same HTML file, so a page stays one file you can hand to someone. The
address keeps the tab after a '#' (index.html#scoring), so a link can open a page on a given
tab, the back button returns to the previous tab, and a reload stays put. With JavaScript off,
all tabs show one after another.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .html import esc

TABS_CSS = """
.tabbar{display:flex;align-items:flex-end;gap:2px;border-bottom:2px solid var(--line);flex-wrap:wrap}
.tabbar a.t{font-family:Michroma,sans-serif;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  text-decoration:none;padding:12px 18px 10px;border-bottom:3px solid transparent;margin-bottom:-2px}
.tabbar a.t:hover{color:var(--navy)}
.tabbar a.t[aria-selected=true]{color:var(--navy);border-bottom-color:var(--gold)}
.tabbar .sp{flex:1}
.tabbar .mnav{display:flex;gap:8px;align-items:center;padding:0 0 8px;font-size:13px}
.tabbar .mnav a{color:#0b4fa8;text-decoration:none;border:1px solid var(--line);border-radius:4px;padding:5px 12px;background:#fff}
.tabbar .mnav a:hover{border-color:#0b4fa8}
.tabbar .mnav .off{color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:5px 12px;opacity:.5}
.tabpanel{display:flex;flex-direction:column;gap:24px}
.js .tabpanel{display:none} .js .tabpanel.on{display:flex}
.tabtitle{margin:8px 0 0}
.js .tabtitle{display:none}
.modcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px}
.modcard .row{margin-bottom:14px}
.modcard .row a{font-size:13px;color:#0b4fa8;text-decoration:none}
.modcard .row a:hover{text-decoration:underline}
.mini{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.mini .big{font-size:24px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}
.mini .lab{font-family:Michroma,sans-serif;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-top:6px}
.mini .sub{font-size:13px;color:var(--muted)}
.obs .mod{flex:0 0 64px;font-size:12px;color:var(--muted);padding-top:2px}
@media print{.tabbar{display:none}}
"""

# In <head>: hide the inactive tabs before the page is drawn (no flash of every tab).
HEAD_JS = "<script>document.documentElement.className+=' js';</script>"

# At the end of <body>.
TABS_JS = """
(function(){
  var tabs=[].slice.call(document.querySelectorAll('.tabbar a.t'));
  if(!tabs.length)return;
  var names=tabs.map(function(a){return a.getAttribute('data-tab');});
  function current(){var h=location.hash.slice(1);return names.indexOf(h)>=0?h:names[0];}
  function show(){
    var h=current();
    tabs.forEach(function(a){a.setAttribute('aria-selected',a.getAttribute('data-tab')===h?'true':'false');});
    [].forEach.call(document.querySelectorAll('.tabpanel'),function(p){p.classList.toggle('on',p.id==='tab-'+h);});
    [].forEach.call(document.querySelectorAll('a[data-keep-tab]'),function(a){
      a.href=a.getAttribute('data-base')+(h===names[0]?'':'#'+h);});
  }
  window.addEventListener('hashchange',function(){
    show();var bar=document.querySelector('.tabbar');
    if(bar){var y=bar.getBoundingClientRect().top+window.scrollY-8;if(window.scrollY>y)window.scrollTo(0,y);}
  });
  show();
})();
"""


def tabbar(tabs: Sequence[Tuple[str, str]], right: str = "") -> str:
    """tabs = [(id, label)]; the first is the default. `right` is extra HTML at the right end."""
    links = "".join(f'<a class="t" href="#{esc(t)}" data-tab="{esc(t)}" role="tab">{esc(lab)}</a>' for t, lab in tabs)
    return f'<nav class="tabbar" role="tablist" aria-label="Sections">{links}<span class="sp"></span>{right}</nav>'


def panel(tab: str, title: str, body: str) -> str:
    """One tab's content. The title only shows with JavaScript off (where every tab is shown)."""
    return (f'<div class="tabpanel" id="tab-{esc(tab)}" role="tabpanel">'
            f'<h2 class="tabtitle">{esc(title)}</h2>{body}</div>')


def match_nav(nav: Optional[Dict[str, Any]]) -> str:
    """Previous / event / next links for a match page. They keep the tab you're on."""
    if not nav:
        return ""

    def link(item: Optional[Tuple[str, str]], arrow_left: bool) -> str:
        if not item:
            return f'<span class="off">{"← " if arrow_left else ""}{"Previous" if arrow_left else "Next"}{"" if arrow_left else " →"}</span>'
        label, href = item
        txt = f"← {label}" if arrow_left else f"{label} →"
        return f'<a href="{esc(href)}" data-keep-tab data-base="{esc(href)}">{esc(txt)}</a>'
    ev = nav.get("event")
    mid = (f'<a href="{esc(ev)}" data-keep-tab data-base="{esc(ev)}">Event</a>' if ev else "")
    return f'<span class="mnav">{link(nav.get("prev"), True)}{mid}{link(nav.get("next"), False)}</span>'


def module_card(title: str, tab: str, lead: str, stats: List[Tuple[str, str, str]]) -> str:
    """An overview card: a module's headline numbers and a link to its tab."""
    mini = "".join(f'<div><div class="big">{esc(a)}</div><div class="lab">{esc(b)}</div><div class="sub">{esc(c)}</div></div>'
                   for a, b, c in stats)
    return (f'<section class="card modcard"><div class="row"><h2>{esc(title)}</h2>'
            f'<a href="#{esc(tab)}">All {esc(title.lower())} details →</a></div>'
            f'{f"<p class=lead>{esc(lead)}</p>" if lead else ""}<div class="mini">{mini}</div></section>')


SEV_ORDER = {"act": 0, "watch": 1, "info": 2}
SEV_LABEL = {"act": "Act", "watch": "Watch", "info": "Info"}


def combined_observations(groups: List[Tuple[str, List[Dict[str, str]]]], levels=("act", "watch")) -> str:
    """Observations from several modules, most urgent first, each tagged with its module."""
    items = [(SEV_ORDER[o["severity"]], k, mod, o) for k, (mod, obs) in enumerate(groups) for o in obs
             if o["severity"] in levels]
    items.sort(key=lambda x: (x[0], x[1]))
    return "".join(f'<li><span class="sev {o["severity"]}">{SEV_LABEL[o["severity"]]}</span>'
                   f'<span class="mod">{esc(mod)}</span><span>{esc(o["text"])}</span></li>' for _, _, mod, o in items)
