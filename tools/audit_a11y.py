#!/usr/bin/env python3
"""Phase 7 gate: every page, at 360px, checked rather than eyeballed.

    cd projects/itc-app
    <a-venv-with-playwright>/bin/python tools/audit_a11y.py
    tools/audit_a11y.py --width 320          # narrower, for the stubborn ones

Needs the service running on 9797 and an organiser account to see the admin
pages (set ITC_AUDIT_EMAIL / ITC_AUDIT_PASSWORD, or it checks the public pages
only). Exits non-zero when it finds something, so it can gate a deploy.

Checks per page:

  overflow     an element whose box runs past the viewport — ignoring anything
               inside a deliberate horizontal scroller, which is allowed to
               extend past its own container
  targets      interactive things under 40px tall (the skip link is exempt:
               it is 1px until it takes focus, which is the whole point)
  names        a form control with no accessible name
  headings     exactly one h1, and no skipped levels
  landmarks    header / main / footer / nav present
  contrast     computed text colour against its *composited* background, to
               AA. Alpha is composited rather than assumed opaque — a nav link
               with rgba(255,255,255,.08) over navy is not white, and treating
               it as white invents failures while hiding real ones. Text over a
               gradient is skipped and left for the eye.
  keyboard     the first tab stop is the skip link, it reaches <main>, and
               every stop keeps a visible focus ring

Written during Phase 7 after the first run turned up 65 findings, most of them
one of six root causes repeated across twelve pages.
"""
import argparse
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright is not installed in this interpreter")

BASE = os.environ.get("ITC_AUDIT_BASE", "http://127.0.0.1:9797")
EMAIL = os.environ.get("ITC_AUDIT_EMAIL", "")
PASSWORD = os.environ.get("ITC_AUDIT_PASSWORD", "")

AUDIT_JS = r"""
() => {
  const vw = window.innerWidth;
  const out = {overflow: [], targets: [], names: [], headings: [],
               landmarks: [], contrast: [], links: []};

  const id = el => {
    const cls = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    const txt = (el.innerText || el.value || '').trim().slice(0, 28);
    return el.tagName.toLowerCase() + cls + (txt ? ` "${txt}"` : '');
  };

  // A child of a horizontal scroller is meant to extend past its container.
  const inScroller = el => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
    return false;
  };

  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (getComputedStyle(el).position === 'fixed') continue;
    if ((r.right > vw + 1 || r.left < -1) && !inScroller(el)) {
      out.overflow.push(id(el) + ` right=${Math.round(r.right)}`);
    }
  }
  if (document.documentElement.scrollWidth > vw) out.overflow.push('the page itself scrolls sideways');

  for (const el of document.querySelectorAll('a, button, input, select, textarea, summary, [role=button]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (el.type === 'hidden' || el.classList.contains('sr-only')) continue;
    const inProse = el.tagName === 'A' && el.closest('p, li, dd, .waiver, .hint, .enter-note');
    if (r.height < 40 && !inProse) out.targets.push(id(el) + ` h=${Math.round(r.height)}`);
  }

  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (el.type === 'hidden') continue;
    const named = (el.labels && el.labels.length) || el.closest('label')
      || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')
      || el.getAttribute('title');
    if (!named) out.names.push(id(el) + ` name=${el.name}`);
  }

  const hs = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')];
  const h1s = hs.filter(h => h.tagName === 'H1');
  if (h1s.length !== 1) out.headings.push(`${h1s.length} h1 elements`);
  let prev = 0;
  for (const h of hs) {
    const lvl = +h.tagName[1];
    if (prev && lvl > prev + 1) out.headings.push(`${h.tagName} after H${prev}: "${h.innerText.trim().slice(0,30)}"`);
    prev = lvl;
  }

  for (const sel of ['header', 'main', 'footer', 'nav']) {
    if (!document.querySelector(sel)) out.landmarks.push(`no <${sel}>`);
  }

  const lum = c => {
    const v = c.map(x => { x /= 255; return x <= 0.03928 ? x/12.92 : Math.pow((x+0.055)/1.055, 2.4); });
    return 0.2126*v[0] + 0.7152*v[1] + 0.0722*v[2];
  };
  const parse = s => (s.match(/[\d.]+/g) || []).slice(0,3).map(Number);
  const bgOf = el => {
    const layers = [];
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const st = getComputedStyle(n);
      if (st.backgroundImage && st.backgroundImage !== 'none') return null;
      const nums = (st.backgroundColor.match(/[\d.]+/g) || []).map(Number);
      const a = nums.length > 3 ? nums[3] : 1;
      if (nums.length >= 3 && a > 0) { layers.push([nums.slice(0,3), a]); if (a >= 1) break; }
    }
    if (!layers.length) return [255,255,255];
    let base = layers[layers.length-1][1] >= 1 ? layers.pop()[0] : [255,255,255];
    for (let i = layers.length - 1; i >= 0; i--) {
      const [c, a] = layers[i];
      base = base.map((b, j) => Math.round(c[j]*a + b*(1-a)));
    }
    return base;
  };
  const seen = new Set();
  for (const el of document.querySelectorAll('p, span, a, li, dd, dt, label, small, h1, h2, h3, strong, button, legend, td, th')) {
    const txt = (el.innerText || '').trim();
    if (!txt || el.children.length > 0) continue;
    const st = getComputedStyle(el);
    const bg = bgOf(el);
    if (bg === null) continue;
    const l1 = lum(parse(st.color)), l2 = lum(bg);
    const ratio = (Math.max(l1,l2) + 0.05) / (Math.min(l1,l2) + 0.05);
    const size = parseFloat(st.fontSize);
    const large = size >= 24 || (size >= 18.66 && parseInt(st.fontWeight,10) >= 700);
    const need = large ? 3.0 : 4.5;
    if (ratio < need) {
      const key = st.color + '|' + bg.join(',') + '|' + Math.round(size);
      if (!seen.has(key)) {
        seen.add(key);
        out.contrast.push(`${ratio.toFixed(2)}:1 (needs ${need}) ${st.color} on rgb(${bg}) ${Math.round(size)}px — "${txt.slice(0,30)}"`);
      }
    }
  }

  for (const a of document.querySelectorAll('a')) {
    if (a.getBoundingClientRect().width === 0) continue;
    if (!((a.innerText || '').trim() || a.getAttribute('aria-label') || a.title)) {
      out.links.push(`link to ${a.getAttribute('href')} has no text`);
    }
  }
  return out;
}
"""


def keyboard_check(page):
    """The skip link must be the first stop and must actually reach the content."""
    problems = []
    page.keyboard.press("Tab")
    first = page.evaluate("() => { const a = document.activeElement;"
                          " return {tag: a.tagName, text: (a.innerText||'').trim(),"
                          " outline: getComputedStyle(a).outlineStyle}; }")
    if "skip" not in first["text"].lower():
        problems.append(f"first tab stop is {first['tag']} \"{first['text'][:20]}\", not the skip link")
    elif first["outline"] == "none":
        problems.append("the skip link takes focus with no visible ring")

    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    landed = page.evaluate("() => location.hash")
    if landed != "#main":
        problems.append(f"the skip link went to {landed!r}, not #main")

    # Walk a reasonable number of stops; every one needs a visible ring.
    ringless = page.evaluate("""() => {
      const out = [];
      const els = [...document.querySelectorAll(
        'a[href], button, input:not([type=hidden]), select, textarea, summary')]
        .filter(e => e.getBoundingClientRect().width > 0);
      for (const el of els.slice(0, 40)) {
        el.focus();
        const st = getComputedStyle(el);
        if (st.outlineStyle === 'none' && st.boxShadow === 'none') {
          out.push(el.tagName.toLowerCase() + ' "' + (el.innerText||el.name||'').trim().slice(0,20) + '"');
        }
      }
      return out;
    }""")
    problems += [f"no focus ring on {r}" for r in ringless]
    return problems


def audit(page, path):
    page.goto(path if path.startswith("http") else f"{BASE}{path}")
    page.wait_for_load_state()
    data = page.evaluate(AUDIT_JS)
    data["keyboard"] = keyboard_check(page)
    data["title"] = page.title()
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=360)
    args = ap.parse_args()

    findings = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu",
            "--single-process", "--no-zygote"])
        page = browser.new_page(viewport={"width": args.width, "height": 800})

        for name, path in (("home", "/"), ("login", "/login"),
                           ("register", "/register"), ("404", "/no-such-page")):
            findings[name] = audit(page, path)

        page.goto(f"{BASE}/")
        ev = page.locator("a[href^='/events/']").first
        if ev.count():
            findings["event"] = audit(page, ev.get_attribute("href"))

        if EMAIL and PASSWORD:
            page.goto(f"{BASE}/login")
            page.fill('input[name="email"]', EMAIL)
            page.fill('input[name="password"]', PASSWORD)
            page.click('main button[type="submit"]')
            page.wait_for_load_state()

            findings["account"] = audit(page, "/account")
            findings["my-races"] = audit(page, "/my-races")
            findings["admin"] = audit(page, "/admin")
            findings["admin/new-event"] = audit(page, "/admin/events/new")

            page.goto(f"{BASE}/admin")
            link = page.locator("a[href^='/admin/events/']:not([href$='/new'])").first
            if link.count():
                href = link.get_attribute("href")
                findings["admin/event"] = audit(page, href)
                findings["admin/entrants"] = audit(page, f"{href}/entrants")
                page.goto(f"{BASE}{href}")
                race = page.locator("a[href^='/admin/races/']").first
                if race.count():
                    findings["admin/race"] = audit(page, race.get_attribute("href"))
        else:
            print("· no ITC_AUDIT_EMAIL set — public pages only\n")

        browser.close()

    total = 0
    for name, f in findings.items():
        issues = {k: v for k, v in f.items() if isinstance(v, list) and v}
        if not issues:
            print(f"✓ {name}")
            continue
        print(f"\n■ {name}  ({f.get('title','')[:50]})")
        for kind, items in issues.items():
            for item in items[:6]:
                print(f"    {kind}: {item}")
            if len(items) > 6:
                print(f"    {kind}: … {len(items) - 6} more")
            total += len(items)

    print(f"\n{total} findings at {args.width}px")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
