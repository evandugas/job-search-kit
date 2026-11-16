// Adds a "Fill with Job Search" button to application pages. On click: find this job in the apply
// queue (or let the user pick it), upload its tailored resume + cover letter, fill standard fields
// from answers.toml, and list what still needs the user. It never submits anything.
(() => {
  if (window.__jsFillLoaded) return;
  window.__jsFillLoaded = true;
  // In frames, only act inside embedded Greenhouse application forms.
  if (window !== window.top && !/greenhouse\.io$/.test(location.hostname)) return;

  const call = (msg) =>
    new Promise((resolve, reject) =>
      chrome.runtime.sendMessage(msg, (r) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : "no response"));
      })
    );

  // ------------------------------------------------------------------ UI (shadow DOM, page CSS can't touch it)
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      * { font-family: Segoe UI, Arial, sans-serif; font-size: 13px; box-sizing: border-box; }
      button { background:#0a66c2; color:#fff; border:0; border-radius:20px; padding:9px 16px; font-weight:600;
               cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.25); }
      button.secondary { background:#eee; color:#222; box-shadow:none; border-radius:6px; padding:6px 10px; }
      #panel { display:none; width:340px; max-height:60vh; overflow:auto; background:#fff; color:#111; border:1px solid #ccc;
               border-radius:10px; padding:12px; margin-bottom:8px; box-shadow:0 4px 16px rgba(0,0,0,.2); }
      h4 { margin:0 0 6px; font-size:14px; } ul { margin:4px 0 8px; padding-left:18px; } li { margin:2px 0; }
      .ok { color:#1a7f37; } .warn { color:#9a6700; } .err { color:#b42318; }
      select { width:100%; margin:6px 0; padding:4px; }
      .row { display:flex; gap:6px; justify-content:flex-end; margin-top:6px; }
    </style>
    <div id="panel"></div>
    <div style="text-align:right"><button id="go">Fill with Job Search</button></div>`;
  document.documentElement.appendChild(host);
  const panel = root.getElementById("panel");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const show = (html) => { panel.style.display = "block"; panel.innerHTML = html; };

  root.getElementById("go").addEventListener("click", async () => {
    show("Looking up this job…");
    try {
      const m = await call({ type: "match", url: location.href });
      if (m.match) return fillWith(m.match, m.answers);
      if (!m.jobs.length) return show("<span class='warn'>Nothing waiting in your apply queue.</span>");
      show(`<h4>Which job is this?</h4><div>This page didn't match a queued job automatically.</div>
        <select id="pick">${m.jobs.map((j) => `<option value="${esc(j.id)}">[${j.score ?? "-"}] ${esc(j.company)}: ${esc(j.title)}</option>`).join("")}</select>
        <div class="row"><button class="secondary" id="cancel">Cancel</button><button id="use">Fill</button></div>`);
      root.getElementById("cancel").onclick = () => (panel.style.display = "none");
      root.getElementById("use").onclick = () => fillWith(root.getElementById("pick").value, m.answers);
    } catch (e) {
      show(`<span class="err">${esc(e.message)}</span>`);
    }
  });

  async function fillWith(id, answers) {
    show("Filling…");
    try {
      const job = await call({ type: "job", id });
      const r = fillForm(job, answers || {});
      const list = (items, cls) => (items.length ? `<ul class="${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "");
      show(`<h4>${esc(job.company)}: ${esc(job.title)}</h4>
        ${r.filled.length ? "<b>Filled</b>" + list(r.filled, "ok") : "<div class='warn'>Nothing to fill on this page.</div>"}
        ${r.todo.length ? "<b>Still needs you</b>" + list(r.todo, "warn") : ""}
        <div style="color:#555">Review everything, then submit yourself. On multi-page forms (Workday), click Fill again on each page.</div>
        <div class="row"><button class="secondary" id="close">Close</button><button class="secondary" id="done">I submitted it ✓</button></div>`);
      root.getElementById("close").onclick = () => (panel.style.display = "none");
      root.getElementById("done").onclick = async () => {
        await call({ type: "done", id });
        show(`<span class="ok">Marked as applied: ${esc(job.company)}: ${esc(job.title)}</span>`);
      };
    } catch (e) {
      show(`<span class="err">${esc(e.message)}</span>`);
    }
  }

  // ------------------------------------------------------------------ form filling
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  function labelText(el) {
    const bits = [];
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) bits.push(l.textContent);
    }
    const wrap = el.closest("label");
    if (wrap) bits.push(wrap.textContent);
    (el.getAttribute("aria-labelledby") || "").split(/\s+/).forEach((i) => {
      const n = i && document.getElementById(i);
      if (n) bits.push(n.textContent);
    });
    bits.push(el.getAttribute("aria-label") || "", el.placeholder || "");
    const fs = el.closest("fieldset");
    if (fs && fs.querySelector("legend")) bits.push(fs.querySelector("legend").textContent);
    const seen = new Set();
    let t = bits.map((b) => b.replace(/\s+/g, " ").trim())
      .filter((b) => b && !seen.has(b.replace(/\*$/, "")) && seen.add(b.replace(/\*$/, ""))).join(" ");
    if (t.length < 3) {  // no label: use the nearest container's text
      let p = el.parentElement;
      for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
        const tx = (p.innerText || "").replace(/\s+/g, " ").trim();
        if (tx) { t = tx.slice(0, 160); break; }
      }
    }
    return t;
  }

  const describe = (el) =>
    [labelText(el), el.name, el.id, el.getAttribute("data-automation-id"), el.autocomplete].join(" ").toLowerCase();

  function setValue(el, v) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);  // works with React-controlled inputs
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  }

  function toFile(f) {
    const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    return new File([bytes], f.name, { type: f.type });
  }

  function fillForm(job, a) {
    const C = a.contact || {}, E = a.eligibility || {}, P = a.preferences || {};
    const filled = [], todo = [];
    const short = (s) => (s.length > 60 ? s.slice(0, 57) + "…" : s);
    const NOT_ME = /company|employer|school|university|college|reference|referr|manager|supervisor|emergency|org(anization)?\b/;

    // Files: resume / cover letter uploads.
    const fileInputs = [...document.querySelectorAll("input[type=file]")];
    for (const inp of fileInputs) {
      let d = describe(inp);
      for (let p = inp.parentElement, i = 0; i < 4 && p; i++, p = p.parentElement) d += " " + (p.innerText || "").slice(0, 120).toLowerCase();
      const isCover = /cover/.test(d.slice(0, 200));
      const f = isCover ? job.cover : (/resume|cv\b|curriculum/.test(d) || fileInputs.length === 1 ? job.resume : null);
      if (!f) { todo.push(`File upload: ${short(labelText(inp) || "unlabeled")}`); continue; }
      if (inp.files && inp.files.length) continue;  // already has a file
      const dt = new DataTransfer();
      dt.items.add(toFile(f));
      inp.files = dt.files;
      for (const t of ["input", "change"]) inp.dispatchEvent(new Event(t, { bubbles: true }));
      filled.push(`${isCover ? "Cover letter" : "Resume"}: ${f.name}`);
    }

    // Text fields.
    const full = [C.first_name, C.last_name].filter(Boolean).join(" ");
    const city = [C.city, C.state].filter(Boolean).join(", ");
    const rules = [
      ["Preferred name", /preferred.?(first.?)?name|nickname/, C.preferred_name || C.first_name],
      ["First name", /first.?name|given.?name|fname/, C.first_name],
      ["Last name", /last.?name|family.?name|surname|lname/, C.last_name],
      ["Email", /e-?mail/, C.email],
      ["Phone", /phone|mobile/, C.phone],
      ["LinkedIn", /linkedin/, C.linkedin],
      ["GitHub", /github/, C.github],
      ["Website", /website|portfolio|personal.?(site|url)/, C.website],
      ["Zip code", /postal|zip/, C.zip],
      ["Address line 2", /address.?(line)?.?2|apt|suite|unit/, C.address_line2],
      ["Address", /address.?(line)?.?1|street|^address|\baddress\b/, C.address_line1],
      ["City", /\bcity\b|town/, C.city],
      ["State", /\bstate\b|province|region/, C.state],
      ["Country", /\bcountry\b/, C.country],
      ["Location", /location/, city],
      ["Full name", /\bname\b/, full],
      ["Start date", /start.?date|available.?to.?start|earliest.?start/, P.earliest_start_date],
      ["Salary", /salary|compensation|pay.?expectation/, P.desired_salary || job.salary_range],
      ["How you heard", /how.?did.?you.?hear/, P.how_did_you_hear],
    ];
    // Contact-type rules only apply to plain labels ("City"), never to questions ("…job location…?").
    const CONTACT = new Set(["Preferred name", "First name", "Last name", "Email", "Phone", "LinkedIn", "GitHub",
                             "Website", "Zip code", "Address line 2", "Address", "City", "State", "Country",
                             "Location", "Full name"]);
    const textRules = [
      ["Why interested", /why.*(interest|apply|join|want|excite)|interest(ed)? in (this|the|our)|what (excites|attracts|draws)/, job.why_interested],
      ["Cover letter (text)", /cover.?letter/, job.cover_text],
    ];
    const inputs = [...document.querySelectorAll("input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=url], textarea")]
      .filter((el) => visible(el) && !el.disabled && !el.readOnly)
      // Search boxes of custom dropdowns (react-select etc.): typing there doesn't pick an option.
      .filter((el) => el.getAttribute("role") !== "combobox" && !el.getAttribute("aria-autocomplete"));
    for (const el of inputs) {
      if (el.value && el.value.trim()) continue;
      const d = describe(el);
      const isQuestion = labelText(el).includes("?");
      const list = el instanceof HTMLTextAreaElement ? [...textRules, ...rules] : rules;
      for (const [name, re, val] of list) {
        if (!re.test(d) || (isQuestion && CONTACT.has(name))) continue;
        if (name === "Full name" && NOT_ME.test(d)) break;
        if (name === "Location" && NOT_ME.test(d)) break;
        if (name === "Phone" && /country|extension|\bext\b/.test(d)) break;  // country-code pickers, extensions
        if (val) { setValue(el, val); filled.push(name); }
        break;
      }
    }

    // Yes/no questions as native <select> or radio buttons (custom dropdowns are left to the user).
    const yesNo = (d) =>
      /sponsor/.test(d) ? (/future/.test(d) ? E.needs_sponsorship_future : E.needs_sponsorship_now || E.needs_sponsorship_future)
      : /authori[sz]ed|legally|eligib.*work|right to work/.test(d) ? E.authorized_to_work_in_us
      : /18 years|over 18|at least 18/.test(d) ? E.over_18
      : /relocat/.test(d) ? P.willing_to_relocate
      : null;
    const STATES = { NH: "New Hampshire", MA: "Massachusetts", ME: "Maine", VT: "Vermont", RI: "Rhode Island",
                     CT: "Connecticut", NY: "New York" };
    const choose = (sel, wanted, label) => {
      const w = wanted.filter(Boolean).map((x) => x.toLowerCase());
      const opt = [...sel.options].find((o) => w.includes(o.text.trim().toLowerCase()) || w.includes(o.value.toLowerCase()));
      if (opt) { setValue(sel, opt.value); filled.push(`${label}: ${opt.text.trim()}`); }
      return !!opt;
    };
    for (const sel of document.querySelectorAll("select")) {
      if (!visible(sel) || sel.disabled || (sel.value && sel.selectedIndex > 0)) continue;
      const d = describe(sel);
      if (/\bcountry\b/.test(d) && !/phone|code/.test(d) && C.country) {
        choose(sel, [C.country, "United States of America", "USA", "US"], "Country");
        continue;
      }
      if (/\bstate\b|province/.test(d) && !labelText(sel).includes("?") && C.state) {
        const abbr = Object.keys(STATES).find((k) => STATES[k].toLowerCase() === C.state.toLowerCase());
        choose(sel, [C.state, STATES[C.state.toUpperCase()], abbr], "State");
        continue;
      }
      const ans = yesNo(d);
      if (!ans) continue;
      const opt = [...sel.options].find((o) => o.text.trim().toLowerCase().startsWith(ans.toLowerCase()));
      if (opt) { setValue(sel, opt.value); filled.push(short(labelText(sel)) + `: ${opt.text.trim()}`); }
    }
    const groups = {};
    for (const r of document.querySelectorAll("input[type=radio]")) if (r.name) (groups[r.name] ||= []).push(r);
    for (const radios of Object.values(groups)) {
      if (radios.some((r) => r.checked) || !visible(radios[0])) continue;
      const q = radios[0].closest("fieldset, [role=radiogroup], [role=group]") || radios[0].parentElement.parentElement;
      const qText = labelText(q.querySelector("legend") || radios[0]) + " " + (q.innerText || "").slice(0, 200);
      const ans = yesNo(qText.toLowerCase());
      if (!ans) continue;
      const r = radios.find((x) => labelText(x).toLowerCase().startsWith(ans.toLowerCase()));
      if (r) { r.click(); filled.push(short(qText.split("\n")[0]) + `: ${ans}`); }
    }

    // Anything required that's still empty.
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (!visible(el) || el.type === "hidden" || el.type === "file") continue;
      const req = el.required || el.getAttribute("aria-required") === "true";
      const empty = el.type === "radio" || el.type === "checkbox"
        ? !document.querySelector(`input[name="${CSS.escape(el.name)}"]:checked`)
        : !(el.value && el.value.trim());
      const t = short(labelText(el) || el.name || "unlabeled field");
      if (req && empty && !todo.includes(t)) todo.push(t);
    }
    return { filled, todo };
  }
})();
