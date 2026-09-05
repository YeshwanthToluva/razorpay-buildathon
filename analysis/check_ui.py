"""Load the built page's script the way a browser would, and fail if it throws.

The page is one <script>. A single ReferenceError at the top level therefore
does not degrade one component -- it stops every line after it, so the risk
dropdown, the replays, the safety counters and the API health check all go
blank at once and the page still looks like it rendered. That is exactly what
happened when a chapter was deleted and one line that read its dropdown was
left behind, and nothing caught it because the HTML was still valid.

This runs the script against a stub DOM under node. It does not check that the
page looks right; it checks that the script survives to the end.

    python analysis/check_ui.py
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ("ui/index.html", "ui/rulebook.html", "ui/agent.html")

STUB = r"""
const fs = require('fs');
function mkEl(tag){
  const el = {
    tagName:(tag||'div').toUpperCase(), children:[], childNodes:[], style:{}, dataset:{},
    classList:{add(){},remove(){},toggle(){},contains(){return false}},
    textContent:'', innerHTML:'', value:'', checked:false, hidden:false, options:[],
    appendChild(c){this.children.push(c); return c},
    append(){}, prepend(){}, remove(){}, insertAdjacentHTML(){},
    setAttribute(){}, getAttribute(){return null}, removeAttribute(){},
    addEventListener(){}, removeEventListener(){}, click(){}, focus(){},
    querySelector(){return mkEl('div')}, querySelectorAll(){return []},
    closest(){return mkEl('div')}, scrollIntoView(){},
    getBoundingClientRect(){return {top:0,left:0,width:0,height:0,bottom:0,right:0}},
    animate(){return {finished:Promise.resolve(), cancel(){}}},
  };
  return el;
}
const DATA = fs.readFileSync(process.argv[2], 'utf8');
global.document = {
  createElement:mkEl, createElementNS:mkEl, createTextNode:()=>mkEl('text'),
  getElementById:()=>{ const e = mkEl('div'); e.textContent = DATA; e.innerHTML = DATA; return e; },
  querySelector:()=>mkEl('div'), querySelectorAll:()=>[],
  addEventListener(){}, body:mkEl('body'), documentElement:mkEl('html'),
  head:mkEl('head'), readyState:'complete',
};
global.window = {
  addEventListener(){}, location:{hash:'', href:'http://localhost:3000/',
  origin:'http://localhost:3000', protocol:'http:', hostname:'localhost', port:'3000'},
  matchMedia:()=>({matches:false, addEventListener(){}}),
  requestAnimationFrame:(f)=>setTimeout(f,0),
  getComputedStyle:()=>({getPropertyValue:()=>''}), innerWidth:1440, innerHeight:900,
};
global.IntersectionObserver = function(){ this.observe=()=>{}; this.disconnect=()=>{}; };
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.setTimeout = setTimeout; global.clearTimeout = clearTimeout;
global.getComputedStyle = () => ({getPropertyValue:()=>''});
global.requestAnimationFrame = window.requestAnimationFrame;
global.matchMedia = window.matchMedia;
global.location = window.location;
global.navigator = {userAgent:'node'};
global.Option = function(text, value){ const e = mkEl('option'); e.text=text; e.value=value; return e; };
global.CustomEvent = function(){}; global.Event = function(){};
global.fetch = async () => ({ ok:true, json: async()=>({}), text: async()=>'' });
global.localStorage = { getItem(){return null}, setItem(){}, removeItem(){} };
global.EventSource = function(){ this.addEventListener=()=>{}; this.close=()=>{}; };
require(process.argv[3]);
"""


def node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    versions = sorted(pathlib.Path.home().glob(".nvm/versions/node/*/bin/node"))
    return str(versions[-1]) if versions else None


def check(page: pathlib.Path, exe: str, tmp: pathlib.Path) -> list[str]:
    html = page.read_text()
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    # The data payload is JSON; the rest is the page's behaviour.
    data, scripts = "{}", []
    for b in blocks:
        stripped = b.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                json.loads(stripped)
                data = stripped
                continue
            except ValueError:
                pass
        scripts.append(b)

    failures = []
    (tmp / "stub.js").write_text(STUB)
    (tmp / "data.json").write_text(data)
    for i, src in enumerate(scripts):
        js = tmp / f"{page.stem}_{i}.js"
        js.write_text(src)
        proc = subprocess.run(
            [exe, str(tmp / "stub.js"), str(tmp / "data.json"), str(js)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            first = [
                ln for ln in proc.stderr.splitlines()
                if "Error" in ln or "not defined" in ln
            ]
            failures.append(f"{page.name} script #{i}: {first[0] if first else 'threw'}")
    return failures


def main() -> int:
    exe = node()
    if exe is None:
        print("node not found; skipping (this check needs a JS runtime)")
        return 0
    failures = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for rel in PAGES:
            page = ROOT / rel
            if page.exists():
                failures.extend(check(page, exe, tmp))
    for f in failures:
        print("FAIL", f)
    if failures:
        return 1
    print(f"ok — every script in {', '.join(PAGES)} ran to completion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
