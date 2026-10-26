"use strict";

(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // localStorage can throw (private mode, blocked storage); the app works without it.
  const store = {
    get(key, fallback) {
      try {
        const value = JSON.parse(localStorage.getItem(key));
        return value ?? fallback;
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {}
    },
  };

  const FAVS = "nb:favourites";
  const RECENTS = "nb:recents";
  const MAX_RECENTS = 6;
  const REFRESH_MS = 20000;

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "style") node.style.cssText = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child != null) node.append(child);
    }
    return node;
  }

  const hex = (c) => (/^[0-9a-f]{6}$/i.test(c || "") ? `#${c}` : "");

  function routeChip(route) {
    const bg = hex(route.color);
    const fg = hex(route.text_color) || "#fff";
    return el("span", { class: "mini-route", style: bg ? `--rc:${bg};--rt:${fg}` : null }, route.label);
  }

  function stopItem(stop, extra) {
    const sub = [stop.code ? `Stop ${stop.code}` : null, extra].filter(Boolean).join(" · ");
    return el(
      "li",
      { role: "option", id: `opt-${stop.stop_id}` },
      el(
        "a",
        { href: `/stop/${encodeURIComponent(stop.stop_id)}` },
        el("span", { class: "name" }, stop.name),
        sub ? el("span", { class: "sub" }, sub) : null,
        el("span", { class: "mini-routes" }, (stop.routes || []).slice(0, 8).map(routeChip)),
      ),
    );
  }

  function fillList(list, stops, extra = () => null) {
    list.replaceChildren(...stops.map((s) => stopItem(s, extra(s))));
    list.hidden = stops.length === 0;
  }

  /* Theme */

  function setupTheme() {
    const btn = $("#theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const root = document.documentElement;
      const current =
        root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      // Stored as a raw string because the head script reads it before this file loads.
      try {
        localStorage.setItem("nb:theme", next);
      } catch {}
    });
  }

  /* Home page */

  function setupSearch() {
    const input = $("#search");
    const list = $("#results");
    const form = $("#search-form");
    if (!input || !list) return;

    let controller;
    let timer;
    let active = -1;

    const options = () => $$("li", list);
    function highlight(index) {
      const items = options();
      active = Math.max(-1, Math.min(index, items.length - 1));
      items.forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
      const current = items[active];
      input.setAttribute("aria-activedescendant", current ? current.id : "");
      current?.scrollIntoView({ block: "nearest" });
    }

    async function search() {
      const q = input.value.trim();
      controller?.abort();
      if (!q) {
        fillList(list, []);
        input.setAttribute("aria-expanded", "false");
        return;
      }
      controller = new AbortController();
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal: controller.signal });
        if (!res.ok) throw new Error(res.statusText);
        const stops = await res.json();
        fillList(list, stops);
        if (!stops.length) {
          list.replaceChildren(el("li", { class: "status-msg" }, `No stops match “${q}”.`));
          list.hidden = false;
        }
        input.setAttribute("aria-expanded", String(stops.length > 0));
        highlight(stops.length ? 0 : -1);
      } catch (err) {
        if (err.name !== "AbortError") {
          list.replaceChildren(el("li", { class: "status-msg" }, "Search isn't available right now."));
          list.hidden = false;
        }
      }
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(search, 120);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlight(active + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlight(active - 1);
      } else if (e.key === "Escape") {
        input.value = "";
        search();
      }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const link = $("a", options()[Math.max(active, 0)] || document.createElement("li"));
      if (link) location.href = link.href;
    });

    const initial = new URLSearchParams(location.search).get("q");
    if (initial) {
      input.value = initial;
      search();
    }
  }

  function setupNearby() {
    const btn = $("#nearby-btn");
    const list = $("#nearby");
    const status = $("#nearby-status");
    if (!btn) return;
    if (!("geolocation" in navigator)) {
      btn.hidden = true;
      return;
    }
    const say = (msg) => {
      status.textContent = msg;
      status.hidden = !msg;
    };
    btn.addEventListener("click", () => {
      say("Finding your location…");
      navigator.geolocation.getCurrentPosition(
        async ({ coords }) => {
          try {
            const res = await fetch(`/api/nearby?lat=${coords.latitude}&lon=${coords.longitude}`);
            if (!res.ok) throw new Error(res.statusText);
            const stops = await res.json();
            say(stops.length ? "" : "No stops within 800 m of you.");
            const fmt = (m) => (m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`);
            fillList(list, stops, (s) => fmt(s.distance_m));
          } catch {
            say("Couldn't load nearby stops. Try again.");
          }
        },
        (err) => {
          say(err.code === err.PERMISSION_DENIED ? "Location permission was denied." : "Couldn't get your location.");
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 },
      );
    });
  }

  function setupSaved() {
    const favs = store.get(FAVS, []);
    const favIds = new Set(favs.map((s) => s.stop_id));
    const recents = store.get(RECENTS, []).filter((s) => !favIds.has(s.stop_id));
    const show = (section, list, stops) => {
      if (!section) return;
      fillList(list, stops);
      section.hidden = stops.length === 0;
    };
    show($("#favourites-section"), $("#favourites"), favs);
    show($("#recents-section"), $("#recents"), recents);
    $("#clear-recents")?.addEventListener("click", () => {
      store.set(RECENTS, []);
      $("#recents-section").hidden = true;
    });
  }

  /* Stop page */

  function setupStop() {
    const head = $("#stop");
    if (!head) return;
    const stop = JSON.parse(head.dataset.stop);
    const board = $("#board");
    const offline = $("#offline-msg");
    const tz = head.dataset.tz || undefined;

    // Recents, newest first.
    const recents = store.get(RECENTS, []).filter((s) => s.stop_id !== stop.stop_id);
    store.set(RECENTS, [stop, ...recents].slice(0, MAX_RECENTS));

    // Favourite toggle.
    const favBtn = $("#fav-btn");
    const isFav = () => store.get(FAVS, []).some((s) => s.stop_id === stop.stop_id);
    const paintFav = () => {
      const on = isFav();
      favBtn.setAttribute("aria-pressed", String(on));
      favBtn.setAttribute("aria-label", on ? "Remove from favourites" : "Save to favourites");
    };
    favBtn?.addEventListener("click", () => {
      const favs = store.get(FAVS, []).filter((s) => s.stop_id !== stop.stop_id);
      store.set(FAVS, isFav() ? favs : [...favs, stop]);
      paintFav();
    });
    if (favBtn) paintFav();

    // Route filter, kept in the URL so it survives reloads and can be shared.
    const filter = $("#route-filter");
    const params = new URLSearchParams(location.search);
    let selected = new Set((params.get("route") || "").split(",").filter(Boolean));
    function applyFilter() {
      if (filter) {
        for (const chip of $$(".chip", filter)) {
          const id = chip.dataset.route;
          chip.setAttribute("aria-pressed", String(id ? selected.has(id) : selected.size === 0));
        }
      }
      // Hide right away; the refresh that follows brings in more departures.
      for (const row of $$(".dep", board)) {
        row.style.display = selected.size === 0 || selected.has(row.dataset.route) ? "" : "none";
      }
    }
    filter?.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      const id = chip.dataset.route;
      if (!id) selected.clear();
      else if (selected.has(id)) selected.delete(id);
      else selected.add(id);
      const url = new URL(location.href);
      if (selected.size) url.searchParams.set("route", [...selected].join(","));
      else url.searchParams.delete("route");
      history.replaceState(null, "", url);
      applyFilter();
      refresh();
    });

    // Countdowns tick locally between refreshes, on the server's clock.
    let offset = 0;
    function syncClock() {
      const serverNow = Number($(".board", board)?.dataset.now);
      if (serverNow) offset = serverNow * 1000 - Date.now();
    }
    const kioskClock = $("#kiosk-clock");
    const clockFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: tz });
    function tick() {
      const now = (Date.now() + offset) / 1000;
      for (const row of $$(".dep", board)) {
        const cd = $("[data-countdown]", row);
        if (!cd) continue;
        const delta = Number(row.dataset.time) - now;
        if (delta < -30) {
          row.hidden = true;
          continue;
        }
        const clockLine = $("[data-clock-line]", row);
        if (delta < 60) cd.textContent = "Now";
        else if (delta < 3600) cd.textContent = `${Math.floor(delta / 60)} min`;
        else cd.textContent = row.dataset.clock;
        if (clockLine) clockLine.hidden = delta >= 3600;
      }
      if (kioskClock) kioskClock.textContent = clockFmt.format(new Date(Date.now() + offset));
    }

    let pending = null;
    async function refresh() {
      if (document.hidden) return;
      // A newer request (e.g. after changing the filter) supersedes an older one.
      pending?.abort();
      const controller = (pending = new AbortController());
      try {
        const query = selected.size ? `?route=${encodeURIComponent([...selected].join(","))}` : "";
        const url = `/stop/${encodeURIComponent(stop.stop_id)}/board${query}`;
        const res = await fetch(url, { cache: "no-store", signal: controller.signal });
        if (!res.ok) throw new Error(res.statusText);
        const html = await res.text();
        const open = new Set($$("details[open]", board).map((d) => d.dataset.alert));
        board.innerHTML = html;
        for (const d of $$("details[data-alert]", board)) {
          if (open.has(d.dataset.alert)) d.open = true;
        }
        offline.hidden = true;
        syncClock();
        applyFilter();
        tick();
      } catch (err) {
        if (err.name !== "AbortError") offline.hidden = false;
      }
    }

    syncClock();
    applyFilter();
    tick();
    setInterval(tick, 1000);
    setInterval(refresh, REFRESH_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refresh();
    });
    addEventListener("online", refresh);

    // Kiosks run for days; reload occasionally to pick up new deploys.
    if (document.body.classList.contains("kiosk")) {
      setTimeout(() => location.reload(), 6 * 3600 * 1000);
    }
  }

  function setupServiceWorker() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupTheme();
    setupSearch();
    setupNearby();
    setupSaved();
    setupStop();
    setupServiceWorker();
  });
})();
