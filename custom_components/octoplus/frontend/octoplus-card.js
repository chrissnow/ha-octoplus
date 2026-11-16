/*
 * Octoplus card — offers with claim buttons, active rewards, points and spins.
 * Served by the Octoplus integration; no other cards required.
 *
 *   type: custom:octoplus-card
 *   entity: sensor.octoplus_a_1234abcd_offers   # optional, auto-detected
 *   hide_unclaimable: false                     # optional
 *   show_rewards: true                          # optional
 */
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(d) - startOfDay(new Date())) / 86400000);
  const label = d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  if (days < 0) return "ended";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days <= 6) return `${label} (${days}d)`;
  return label;
};

class OctoplusCard extends HTMLElement {
  static getStubConfig(hass) {
    const e = Object.keys(hass.states).find((id) => /^sensor\.octoplus_.*_offers$/.test(id));
    return e ? { entity: e } : {};
  }

  static getConfigElement() {
    return document.createElement("octoplus-card-editor");
  }

  setConfig(config) {
    this._config = { hide_unclaimable: false, show_rewards: true, ...config };
    this._confirming = null;
    this._busy = null;
    this._lastKey = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  set hass(hass) {
    this._hass = hass;
    const ids = this._entities();
    const key = ids ? Object.values(ids).map((id) => (id && hass.states[id] ? hass.states[id].last_updated : "")).join("|") : "none";
    if (key !== this._lastKey) {
      this._lastKey = key;
      this._render();
    }
  }

  _entities() {
    const hass = this._hass;
    if (!hass) return null;
    let offers = this._config.entity;
    if (!offers) offers = Object.keys(hass.states).find((id) => /^sensor\.octoplus_.*_offers$/.test(id));
    if (!offers || !hass.states[offers]) return null;
    // Find sibling entities on the same device via the entity registry (robust to renames),
    // falling back to the entity_id prefix.
    const reg = hass.entities || {};
    const dev = reg[offers]?.device_id;
    const byKey = {};
    if (dev) {
      for (const [id, e] of Object.entries(reg)) {
        if (e.device_id === dev && e.platform === "octoplus" && e.translation_key) byKey[e.translation_key] = id;
      }
    }
    if (byKey.offers) offers = byKey.offers;
    const prefix = offers.replace(/_offers$/, "");
    const pick = (tk, domain, suffix) => byKey[tk] || `${domain}.${prefix.split(".")[1]}_${suffix}`;
    return {
      offers,
      rewards: pick("active_rewards", "sensor", "active_rewards"),
      points: pick("octopoints", "sensor", "octopoints"),
      spins: pick("wheel_spins", "sensor", "wheel_spins"),
      scratch: pick("scratchcard", "sensor", "scratchcard"),
      scratchBtn: pick("scratch", "button", "scratch_now"),
      spinBtn: pick("spin", "button", "spin_wheel"),
    };
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  async _claim(slug) {
    if (this._confirming !== slug) {
      this._confirming = slug;
      this._render();
      clearTimeout(this._confirmTimer);
      this._confirmTimer = setTimeout(() => {
        this._confirming = null;
        this._render();
      }, 4000);
      return;
    }
    clearTimeout(this._confirmTimer);
    this._confirming = null;
    this._busy = slug;
    this._render();
    const ids = this._entities();
    const attrs = this._hass.states[ids.offers].attributes;
    try {
      const res = await this._hass.callWS({
        type: "call_service",
        domain: "octoplus",
        service: "claim_offer",
        service_data: { offer_slug: slug, config_entry_id: attrs.config_entry_id },
        return_response: true,
      });
      const r = res?.response || {};
      this._toast(`Claimed ${r.offer || slug}${r.codes?.length ? ` — ${r.codes.join(", ")}` : ""}`);
    } catch (err) {
      this._toast(`Claim failed: ${err?.message || err}`);
    } finally {
      this._busy = null;
      this._lastKey = null;
      this._render();
    }
  }

  _press(entity) {
    this._hass.callService("button", "press", { entity_id: entity }).catch((err) => this._toast(`${err?.message || err}`));
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const hass = this._hass;
    const ids = this._entities();
    if (!ids) {
      this.shadowRoot.innerHTML = `<ha-card header="Octoplus"><div class="empty">No Octoplus offers sensor found.</div></ha-card>`;
      return;
    }
    const st = (id) => hass.states[id];
    const offers = (st(ids.offers)?.attributes.offers || []).filter((o) => !(this._config.hide_unclaimable && !o.can_claim));
    offers.sort((a, b) => (b.can_claim - a.can_claim) || String(a.claim_by || "9").localeCompare(String(b.claim_by || "9")));
    const rewards = st(ids.rewards)?.attributes.rewards || [];
    const points = st(ids.points);
    const spins = st(ids.spins);
    const scratch = st(ids.scratch);
    const scratchBtn = st(ids.scratchBtn);
    const spinBtn = st(ids.spinBtn);

    const chip = (icon, text, action) =>
      `<div class="chip ${action ? "act" : ""}" ${action ? `data-press="${esc(action)}"` : ""}><ha-icon icon="${icon}"></ha-icon><span>${esc(text)}</span></div>`;
    const chips = [];
    if (points && points.state !== "unavailable") chips.push(chip("mdi:star-circle", `${points.state} pts`));
    if (spins && spins.state !== "unavailable") {
      const n = Number(spins.state) || 0;
      chips.push(chip("mdi:ship-wheel", n ? `Spin (${n})` : "No spins", n && spinBtn && spinBtn.state !== "unavailable" ? ids.spinBtn : null));
    }
    if (scratch) {
      const canScratch = scratchBtn && scratchBtn.state !== "unavailable";
      const label = canScratch ? "Scratch!" : hass.formatEntityState ? hass.formatEntityState(scratch) : scratch.state;
      chips.push(chip("mdi:ticket-percent", label, canScratch ? ids.scratchBtn : null));
    }

    const offerRow = (o) => {
      const state = this._busy === o.slug ? "busy" : this._confirming === o.slug ? "confirm" : o.can_claim ? "ready" : "off";
      const btn =
        state === "off"
          ? `<span class="reason">${esc(o.reason || "Unavailable")}</span>`
          : `<button class="claim ${state}" data-claim="${esc(o.slug)}" ${state === "busy" ? "disabled" : ""}>${
              state === "busy" ? "Claiming…" : state === "confirm" ? "Confirm?" : o.points_cost ? `Claim · ${o.points_cost} pts` : "Claim"
            }</button>`;
      const logo = o.logo ? `<img class="logo" src="${esc(o.logo)}" alt="" loading="lazy">` : `<div class="logo ph"><ha-icon icon="mdi:gift"></ha-icon></div>`;
      const ends = fmtDate(o.claim_by);
      return `<div class="row ${o.can_claim ? "" : "dim"}" title="${esc(o.description)}">
        ${logo}
        <div class="txt"><div class="name">${esc(o.name)}</div>
          <div class="sub">${esc(o.partner || "")}${ends ? ` · ends ${esc(ends)}` : ""}</div></div>
        ${btn}</div>`;
    };

    const rewardRow = (r) => {
      const codes = (r.codes || [])
        .map((c) =>
          /^https?:\/\//.test(c)
            ? `<a class="code link" href="${esc(c)}" target="_blank" rel="noopener">Open voucher</a>`
            : `<span class="code" data-copy="${esc(c)}" title="Copy">${esc(c)} <ha-icon icon="mdi:content-copy"></ha-icon></span>`
        )
        .join("");
      const exp = fmtDate(r.expires);
      return `<div class="row"><div class="logo ph"><ha-icon icon="mdi:ticket-confirmation"></ha-icon></div>
        <div class="txt"><div class="name">${esc(r.name)}</div>
          <div class="sub">${exp ? `expires ${esc(exp)}` : "no expiry"}${r.status && r.status !== "issued" ? ` · ${esc(r.status)}` : ""}</div></div>
        <div class="codes">${codes}</div></div>`;
    };

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 0 8px; }
        .head { display: flex; align-items: center; justify-content: space-between; padding: 0 16px 8px; gap: 8px; flex-wrap: wrap; }
        .title { font-size: 1.2em; font-weight: 500; }
        .chips { display: flex; gap: 6px; flex-wrap: wrap; }
        .chip { display: flex; align-items: center; gap: 4px; padding: 2px 10px 2px 6px; border-radius: 16px;
                background: var(--secondary-background-color); font-size: 0.85em; --mdc-icon-size: 16px; }
        .chip.act { background: var(--primary-color); color: var(--text-primary-color); cursor: pointer; }
        h3 { margin: 10px 16px 4px; font-size: 0.8em; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); font-weight: 500; }
        .row { display: flex; align-items: center; gap: 12px; padding: 6px 16px; }
        .row.dim { opacity: .55; }
        .logo { width: 36px; height: 36px; border-radius: 8px; object-fit: contain; background: #fff; flex: none; }
        .logo.ph { display: flex; align-items: center; justify-content: center; background: var(--secondary-background-color); color: var(--secondary-text-color); }
        .txt { flex: 1; min-width: 0; }
        .name { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.25; }
        .sub { font-size: 0.8em; color: var(--secondary-text-color); }
        button.claim { flex: none; border: none; border-radius: 16px; padding: 6px 14px; cursor: pointer; font: inherit; font-size: 0.85em;
                       background: var(--primary-color); color: var(--text-primary-color); }
        button.claim.confirm { background: var(--warning-color, #ff9800); }
        button.claim.busy { opacity: .6; cursor: default; }
        .reason { flex: none; font-size: 0.8em; color: var(--secondary-text-color); }
        .codes { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; flex: none; }
        .code { font-family: var(--code-font-family, monospace); font-size: 0.85em; cursor: pointer; --mdc-icon-size: 14px; }
        .code.link { font-family: inherit; color: var(--primary-color); text-decoration: none; }
        .empty { padding: 8px 16px; color: var(--secondary-text-color); font-size: 0.9em; }
      </style>
      <ha-card>
        <div class="head"><div class="title">${esc(this._config.title || "Octoplus")}</div><div class="chips">${chips.join("")}</div></div>
        <h3>Offers (${offers.filter((o) => o.can_claim).length} claimable)</h3>
        ${offers.length ? offers.map(offerRow).join("") : `<div class="empty">No offers right now.</div>`}
        ${this._config.show_rewards ? `<h3>Your rewards</h3>${rewards.length ? rewards.map(rewardRow).join("") : `<div class="empty">No active rewards.</div>`}` : ""}
      </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-claim]").forEach((b) => b.addEventListener("click", () => this._claim(b.dataset.claim)));
    this.shadowRoot.querySelectorAll("[data-press]").forEach((b) => b.addEventListener("click", () => this._press(b.dataset.press)));
    this.shadowRoot.querySelectorAll("[data-copy]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(b.dataset.copy);
          this._toast("Code copied");
        } catch (_) {
          this._toast(b.dataset.copy);
        }
      })
    );
  }
}

class OctoplusCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) =>
        ({ entity: "Account (its Offers sensor)", title: "Title", hide_unclaimable: "Hide offers you can't claim", show_rewards: "Show your rewards" })[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...ev.detail.value };
        for (const k of Object.keys(config)) if (config[k] === "" || config[k] === undefined) delete config[k];
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "entity", selector: { entity: { filter: { integration: "octoplus", domain: "sensor" } } } },
      { name: "title", selector: { text: {} } },
      { name: "hide_unclaimable", selector: { boolean: {} } },
      { name: "show_rewards", selector: { boolean: {} } },
    ];
    this._form.data = { hide_unclaimable: false, show_rewards: true, ...this._config };
  }
}

if (!customElements.get("octoplus-card-editor")) customElements.define("octoplus-card-editor", OctoplusCardEditor);

if (!customElements.get("octoplus-card")) {
  customElements.define("octoplus-card", OctoplusCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "octoplus-card",
    name: "Octoplus",
    description: "Octoplus offers with claim buttons, rewards, points and spins",
    preview: false,
  });
}
