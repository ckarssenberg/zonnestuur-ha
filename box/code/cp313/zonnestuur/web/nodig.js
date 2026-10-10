// "Wat heb je nodig?": vragen aantikken, meteen zien wat al kan en wat je nog nodig hebt.
// Gebruikt in de kennismaking van de app en op zonnestuur.nl/hoe-het-werkt. Inhoud: nodig.json.
// Gebruik: zsNodig(element, { website: true|false, koppelUrl: "koppelen" })
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const CSS = `.zn{display:grid;gap:18px}.zn-q{display:grid;gap:18px}.zn-v{display:grid;gap:8px}
.zn-r:not(:empty)~.zn-leeg{display:none}.zn-v b{font-size:15px}
.zn-c{display:flex;flex-wrap:wrap;gap:8px}.zn-c button{font:inherit;font-size:14px;font-weight:600;padding:9px 14px;border-radius:999px;border:0;cursor:pointer;
background:transparent;color:inherit;box-shadow:inset 0 0 0 1.5px rgba(127,127,127,.35)}
.zn-c button[aria-pressed=true]{background:var(--zs-ink,#13202b);color:var(--zs-dag,#fff);box-shadow:none}
.zn-r{display:grid;gap:10px;margin-top:4px}.zn-r:empty{display:none}
.zn-i{display:grid;grid-template-columns:28px 1fr;gap:10px;align-items:start;padding:12px 14px;border-radius:16px;background:rgba(127,127,127,.08);font-size:14px;line-height:1.5}
.zn-i .ic{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:14px}
.zn-i.ok .ic{background:rgba(10,127,85,.16);color:#0a7f55}.zn-i.nodig .ic{background:rgba(255,178,36,.25);color:#8a5a00}.zn-i.tip .ic{background:rgba(47,107,219,.14);color:#2f6bdb}
@media(prefers-color-scheme:dark){.zn-i.nodig .ic{color:#ffcf5c}.zn-i.ok .ic{color:#3ccf7f}.zn-i.tip .ic{color:#8fb0ff}}
.zn-i p{margin:2px 0 0}.zn-i .pr{font-weight:650}.zn-k{font-size:12px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;opacity:.65;margin-top:6px}`;
  function zsNodig(el, opt = {}) {
    if (!document.getElementById("zn-css")) { const s = document.createElement("style"); s.id = "zn-css"; s.textContent = CSS; document.head.append(s); }
    const A = {};
    let D = null;
    const zicht = (v) => (!v.alleen_website || opt.website) && (!v.toon_als || Object.entries(v.toon_als).every(([k, w]) => A[k] === w));
    function teken() {
      const vragen = D.vragen.filter(zicht);
      const ok = [], nodig = [], tip = [];
      for (const v of vragen) for (const k of v.keuzes) {
        const aan = v.meer ? (A[v.id] || []).includes(k.id) : A[v.id] === k.id;
        if (!aan) continue;
        const naam = v.meer ? `<b>${esc(k.tekst)}</b><br>` : "";
        const link = k.link === "koppelen" && opt.koppelUrl ? ` <a href="${esc(opt.koppelUrl)}">Zoek je merk</a>` : "";
        if (k.kan && (!k.kan_als || Object.entries(k.kan_als).every(([x, w]) => A[x] === w))) ok.push(naam + esc(k.kan));
        if (k.nodig) nodig.push(naam + esc(k.nodig) + link + (k.prijs ? `<br><span class="pr">${esc(k.prijs)}</span>` : ""));
        if (k.tip) tip.push((v.meer ? `<b>${esc(k.tekst)}</b><br>` : "") + esc(k.tip));
      }
      if (A.panelen === "nee" && A.contract === "vast") nodig.push("Zonder zonnepanelen heb je een <b>dynamisch contract</b> nodig om te besparen. Overstappen kan meestal maandelijks.");
      const blok = (lijst, cls, ic, kop) => lijst.length ? `<div class="zn-k">${kop}</div>` + lijst.map((t) => `<div class="zn-i ${cls}"><span class="ic">${ic}</span><p>${t}</p></div>`).join("") : "";
      el.innerHTML = `<div class="zn"><div class="zn-q">${vragen.map((v) => `<div class="zn-v"><b>${esc(v.vraag)}</b>${v.meer ? '<span style="font-size:13px;opacity:.7">Kies er zoveel als je wilt</span>' : ""}
        <div class="zn-c">${v.keuzes.map((k) => `<button type="button" data-v="${v.id}" data-k="${k.id}" aria-pressed="${v.meer ? (A[v.id] || []).includes(k.id) : A[v.id] === k.id}">${esc(k.tekst)}</button>`).join("")}</div></div>`).join("")}</div>
        <div class="zn-r" aria-live="polite">${blok(ok, "ok", "✓", "Dit kan Zonnestuur voor je doen")}${blok(nodig, "nodig", "+", "Wat je ervoor nodig hebt")}${blok(tip, "tip", "i", "Goed om te weten")}</div></div>`;
    }
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]"); if (!b) return;
      const v = D.vragen.find((x) => x.id === b.dataset.v), k = b.dataset.k;
      if (v.meer) { const l = A[v.id] || []; A[v.id] = l.includes(k) ? l.filter((x) => x !== k) : [...l, k]; }
      else A[v.id] = A[v.id] === k ? undefined : k;
      teken();
    });
    fetch(opt.bron || "nodig.json").then((r) => r.json()).then((d) => { D = d; teken(); }).catch(() => { el.textContent = "Kon de lijst niet laden."; });
  }
  window.zsNodig = zsNodig;
})();
