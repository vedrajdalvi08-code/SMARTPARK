(() => {
  const pageNames = {
    overview: "Overview",
    analytics: "Analytics",
    slots: "Parking slots",
    maps: "Facility map",
    qr: "QR management",
    reservations: "Reservations",
    users: "Customers",
    payments: "Payments",
    settings: "Settings"
  };
  const recordPages = { qr: 1, reservations: 1, users: 1, payments: 1 };

  function activePage() {
    const page = window.location.pathname.replace(/\/+$/, "").split("/").pop();
    return pageNames[page] ? page : "overview";
  }

  function navigate(page) {
    if (!pageNames[page]) return;
    window.history.pushState({}, "", `/admin/${page}`);
    showPage(page);
  }

  function showPage(page) {
    document.querySelectorAll(".admin-page-view").forEach(section => {
      section.hidden = section.id !== `page-${page}`;
    });
    document.querySelectorAll("[data-admin-page]").forEach(link => {
      if (link.dataset.adminPage === page) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    document.getElementById("adminPageTitle").textContent = pageNames[page] || pageNames.overview;
    document.querySelector(".admin-sidebar")?.classList.remove("is-open");
    document.getElementById("adminMenuToggle")?.setAttribute("aria-expanded", "false");
    if (page === "maps") window.setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
    if (["users", "reservations", "payments", "qr"].includes(page)) loadRecords(page);
    if (page === "analytics") loadAdminAnalytics();
    if (page === "settings") loadAdminSettings();
  }

  function dateCell(value) {
    return value ? escapeHtml(formatISTDateTime(value)) : "—";
  }

  function moneyCell(value) {
    const amount = Number(value);
    return Number.isFinite(amount) ? `₹${amount.toFixed(2)}` : "—";
  }

  function buildTable(headers, rows) {
    if (!rows.length) return '<p class="admin-empty">No matching records found.</p>';
    const headerMarkup = headers.map(header => `<th scope="col">${escapeHtml(header)}</th>`).join("");
    const bodyMarkup = rows.map(row => `<tr>${row.map(value => `<td>${value ?? "—"}</td>`).join("")}</tr>`).join("");
    return `<table class="admin-data-table"><thead><tr>${headerMarkup}</tr></thead><tbody>${bodyMarkup}</tbody></table>`;
  }

  function recordRows(kind, records) {
    if (kind === "users") {
      return buildTable(["Customer", "Contact", "Reservations", "Joined"], records.map(row => [
        `<strong>${escapeHtml(row.full_name)}</strong><br><small>@${escapeHtml(row.username)}</small>`,
        `${escapeHtml(row.email || "—")}<br><small>${escapeHtml(row.phone || "")}</small>`,
        escapeHtml(String(row.reservation_count)),
        dateCell(row.created_at)
      ]));
    }
    if (kind === "reservations") {
      return buildTable(["Ticket", "Vehicle", "Customer", "Slot", "Booking (IST)", "Session", "Status"], records.map(row => [
        `<strong>${escapeHtml(row.ticket_code)}</strong>`,
        `${escapeHtml(row.vehicle_number)}<br><small>${escapeHtml(row.vehicle_type)}</small>`,
        escapeHtml(row.username || "Walk-in"),
        escapeHtml(row.slot_number || "—"),
        dateCell(row.booking_time),
        row.entry_time ? `${dateCell(row.entry_time)}${row.exit_time ? `<br><small>Exit ${dateCell(row.exit_time)}</small>` : ""}` : "Not checked in",
        `<span class="admin-pill">${escapeHtml(row.status)}</span>`
      ]));
    }
    if (kind === "payments") {
      return buildTable(["Ticket", "Vehicle", "Amount", "Method", "Payment status", "Transaction reference", "Ticket status"], records.map(row => [
        `<strong>${escapeHtml(row.ticket_code)}</strong>`,
        escapeHtml(row.vehicle_number),
        moneyCell(row.total_amount),
        escapeHtml(row.payment_method || "—"),
        `<span class="admin-pill">${escapeHtml(row.payment_status)}</span>`,
        escapeHtml(row.transaction_id || "—"),
        escapeHtml(row.status)
      ]));
    }
    return buildTable(["Ticket / QR", "Vehicle", "Slot", "QR status", "Ticket status", "Created (IST)"], records.map(row => [
      row.qr_status === "ACTIVE"
        ? `<a href="/api/tickets/${encodeURIComponent(row.ticket_code)}/qr" target="_blank" rel="noopener"><strong>${escapeHtml(row.ticket_code)}</strong><br><small>Open QR image</small></a>`
        : `<strong>${escapeHtml(row.ticket_code)}</strong>`,
      escapeHtml(row.vehicle_number),
      escapeHtml(row.slot_number || "—"),
      `<span class="admin-pill">${escapeHtml(row.qr_status)}</span>`,
      escapeHtml(row.status),
      dateCell(row.created_at)
    ]));
  }

  async function loadRecords(kind, page = 1) {
    const table = document.getElementById(`${kind}Table`);
    const pagination = document.getElementById(`${kind}Pagination`);
    if (!table || !pagination) return;
    const searchId = { users: "userSearch", reservations: "reservationSearch", payments: "paymentSearch", qr: "qrSearch" }[kind];
    const statusId = { reservations: "reservationStatus", payments: "paymentStatus", qr: "qrStatus" }[kind];
    const params = new URLSearchParams({ page: String(page), page_size: "25" });
    const search = document.getElementById(searchId)?.value.trim();
    const status = statusId ? document.getElementById(statusId)?.value : "";
    if (search) params.set("q", search);
    if (status) params.set("status", status);
    table.innerHTML = '<p class="admin-empty">Loading records…</p>';
    pagination.replaceChildren();
    const result = await fetchAPI(`/admin/records/${kind}?${params.toString()}`);
    if (!result.ok || !result.data.success) {
      table.innerHTML = `<p class="admin-error" role="alert">${escapeHtml(result.data.error || "Could not load records.")}</p>`;
      return;
    }
    table.innerHTML = recordRows(kind, result.data.records);
    const data = result.data.pagination;
    const label = document.createElement("span");
    label.textContent = `${data.total} records · page ${data.page} of ${Math.max(1, data.pages)}`;
    pagination.append(label);
    if (data.page > 1) pagination.append(makePageButton("Previous", kind, data.page - 1));
    if (data.page < data.pages) pagination.append(makePageButton("Next", kind, data.page + 1));
    recordPages[kind] = data.page;
  }

  function makePageButton(label, kind, page) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary";
    button.textContent = label;
    button.dataset.recordPage = kind;
    button.dataset.page = String(page);
    return button;
  }

  function renderBars(container, entries, valueKey, label, colorClass, maximumBars = 40) {
    if (!entries.length) {
      container.innerHTML = '<p class="admin-empty">No data for this date range.</p>';
      return;
    }
    const stride = Math.max(1, Math.ceil(entries.length / maximumBars));
    const shown = entries.filter((_, index) => index % stride === 0 || index === entries.length - 1);
    const max = Math.max(1, ...shown.map(item => Number(item[valueKey]) || 0));
    container.innerHTML = `<div class="admin-bar-chart">${shown.map(item => {
      const value = Number(item[valueKey]) || 0;
      const height = Math.max(value > 0 ? 4 : 0, (value / max) * 100);
      return `<div class="admin-bar-item" title="${escapeHtml(item.date)}: ${escapeHtml(String(value))}">
        <span class="admin-bar-value">${escapeHtml(valueKey === "revenue" ? moneyCell(value) : String(value))}</span>
        <span class="admin-bar ${colorClass}" style="height:${height}%"></span>
        <span class="admin-bar-label">${escapeHtml(item.date.slice(5))}</span>
      </div>`;
    }).join("")}</div><p class="admin-chart-caption">${escapeHtml(label)}</p>`;
  }

  function renderHours(container, values) {
    const max = Math.max(1, ...values);
    container.innerHTML = `<div class="admin-hour-chart">${values.map((value, hour) => {
      const height = Math.max(value > 0 ? 4 : 0, value / max * 100);
      return `<div class="admin-hour-item" title="${String(hour).padStart(2, "0")}:00 IST — ${value} entries">
        <span class="admin-bar admin-bar-alt" style="height:${height}%"></span>
        <span class="admin-bar-label">${hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}</span>
      </div>`;
    }).join("")}</div><p class="admin-chart-caption">Entry scans by local hour, 00:00–23:00 IST</p>`;
  }

  async function loadAdminAnalytics() {
    const message = document.getElementById("analyticsMessage");
    const period = document.getElementById("analyticsPeriod").value;
    const params = new URLSearchParams({ period });
    if (period === "custom") {
      const start = document.getElementById("analyticsStart").value;
      const end = document.getElementById("analyticsEnd").value;
      if (!start || !end) {
        message.textContent = "Choose both a start date and an end date.";
        return;
      }
      params.set("start", start);
      params.set("end", end);
    }
    message.textContent = "Loading analytics…";
    const result = await fetchAPI(`/admin/analytics?${params.toString()}`);
    if (!result.ok || !result.data.success) {
      message.textContent = result.data.error || "Could not load analytics.";
      return;
    }
    message.textContent = `${result.data.range.start} to ${result.data.range.end} · ${result.data.range.timezone}`;
    const metrics = result.data.metrics;
    document.getElementById("analyticsArrivals").textContent = metrics.arrivals;
    document.getElementById("analyticsRevenue").textContent = moneyCell(metrics.paid_revenue);
    document.getElementById("analyticsDuration").textContent = `${metrics.average_duration_minutes} min`;
    document.getElementById("analyticsOccupancy").textContent = `${metrics.current_occupancy_percent}%`;
    renderBars(document.getElementById("analyticsArrivalChart"), result.data.daily, "arrivals", "Ticket arrivals per day", "admin-bar-primary");
    renderBars(document.getElementById("analyticsRevenueChart"), result.data.daily, "revenue", "Paid revenue per day (INR)", "admin-bar-alt");
    renderHours(document.getElementById("analyticsHourChart"), result.data.entry_hours_ist);
    const vehicles = document.getElementById("analyticsVehicleTypes");
    const counts = Object.entries(result.data.vehicle_types);
    vehicles.innerHTML = counts.length
      ? counts.map(([type, count]) => `<span class="admin-chip">${escapeHtml(type)} <strong>${escapeHtml(String(count))}</strong></span>`).join("")
      : '<p class="admin-empty">No vehicle arrivals in this range.</p>';
  }

  async function loadAdminSettings() {
    const message = document.getElementById("settingsMessage");
    const grid = document.getElementById("adminSettingsGrid");
    message.textContent = "Loading settings…";
    const result = await fetchAPI("/admin/settings");
    if (!result.ok || !result.data.success) {
      message.textContent = result.data.error || "Could not load settings.";
      return;
    }
    const settings = result.data;
    const rates = Object.entries(settings.rates).map(([type, rate]) =>
      `<li><strong>${escapeHtml(type.replaceAll("_", " "))}</strong><span>Base ${moneyCell(rate.base)} · hourly ${moneyCell(rate.hourly)}</span></li>`
    ).join("");
    const location = settings.facility.latitude === null || settings.facility.longitude === null
      ? "Not configured"
      : `${escapeHtml(String(settings.facility.latitude))}, ${escapeHtml(String(settings.facility.longitude))}`;
    const peakHours = settings.peak_hours.map(range =>
      `${String(range.start).padStart(2, "0")}:00–${String(range.end).padStart(2, "0")}:00 IST`
    ).join(", ");
    grid.innerHTML = `
      <article class="sim-box admin-setting-card"><h3>Pricing</h3><p>Rates are loaded from server deployment configuration and are read-only here.</p><ul class="admin-setting-list">${rates}</ul></article>
      <article class="sim-box admin-setting-card"><h3>Facility</h3><dl><dt>Timezone</dt><dd>${escapeHtml(settings.timezone)}</dd><dt>Currency</dt><dd>${escapeHtml(settings.currency)}</dd><dt>Coordinates</dt><dd>${location}</dd><dt>Peak hours</dt><dd>${escapeHtml(peakHours)} · ×${escapeHtml(String(settings.peak_multiplier))}</dd></dl></article>
      <article class="sim-box admin-setting-card"><h3>Integrations</h3><dl><dt>Hardware</dt><dd>${escapeHtml(settings.integrations.hardware)}</dd><dt>Payments</dt><dd>${escapeHtml(settings.integrations.payments)}</dd></dl><p>Payment status in this application does not represent a real money transfer.</p></article>`;
    message.textContent = settings.editable ? "Settings loaded." : "Read-only deployment settings · update environment configuration and redeploy to change.";
  }

  document.addEventListener("click", event => {
    const pageLink = event.target.closest("[data-admin-page]");
    if (pageLink) {
      event.preventDefault();
      navigate(pageLink.dataset.adminPage);
    }
    const loadButton = event.target.closest("[data-record-load]");
    if (loadButton) loadRecords(loadButton.dataset.recordLoad, 1);
    const pageButton = event.target.closest("[data-record-page]");
    if (pageButton) loadRecords(pageButton.dataset.recordPage, Number(pageButton.dataset.page));
  });

  document.getElementById("analyticsApply").addEventListener("click", loadAdminAnalytics);
  document.getElementById("analyticsPeriod").addEventListener("change", event => {
    const custom = event.target.value === "custom";
    document.getElementById("analyticsStart").hidden = !custom;
    document.getElementById("analyticsEnd").hidden = !custom;
  });
  document.getElementById("adminThemeToggle").addEventListener("click", toggleTheme);
  document.getElementById("adminMenuToggle").addEventListener("click", event => {
    const sidebar = document.querySelector(".admin-sidebar");
    const open = sidebar.classList.toggle("is-open");
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
  window.addEventListener("popstate", () => showPage(activePage()));

  showPage(activePage());
})();
