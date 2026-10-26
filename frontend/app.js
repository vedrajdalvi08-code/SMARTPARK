/**
 * SMARTPARK - Frontend Client Application
 * Handles API communication, interactive slot visualization,
 * dynamic pricing calculations, customer booking, payment, and operations tools.
 */

// Use same-origin API routes in Flask/Vercel; support local HTML file testing too.
const API_BASE = window.location.protocol === "file:"
  ? "http://127.0.0.1:5000/api"
  : "/api";

function formatRegistrationInput(value) {
  const compact = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10);
  return [compact.slice(0, 2), compact.slice(2, 4), compact.slice(4, 6), compact.slice(6, 10)].filter(Boolean).join("-");
}

function validRegistrationInput(value) {
  return /^[A-Z]{2}-\d{2}-[A-Z]{2}-\d{4}$/.test(value);
}

function formatISTDateTime(value) {
  if (!value) return "—";
  let normalized = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(normalized)) {
    normalized = normalized.replace(" ", "T");
  }
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(?:\.\d+)?)?$/.test(normalized)) {
    normalized += "Z";
  }
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
    timeZoneName: "short"
  }).format(date);
}

function istDateTimeLocalValue(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date).reduce((values, part) => {
    values[part.type] = part.value;
    return values;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function istDateTimeLocalToISO(value) {
  return value ? new Date(`${value}:00+05:30`).toISOString() : "";
}

function ticketQrUrl(ticketCode) {
  return `${API_BASE}/tickets/${encodeURIComponent(ticketCode)}/qr`;
}

document.addEventListener("DOMContentLoaded", () => {
  // Keep the payment page discoverable from every authenticated customer view.
  const nav = document.querySelector(".site-nav-links");
  if (nav && !nav.querySelector('a[href="/payment"]')) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = "/payment";
    link.textContent = "Payment & Exit";
    item.append(link);
    nav.insertBefore(item, nav.querySelector(".theme-toggle")?.parentElement || null);
  }
  const dashboardHeading = document.querySelector(".dashboard-heading");
  if (dashboardHeading && !dashboardHeading.querySelector('a[href="/payment"]')) {
    const link = document.createElement("a");
    link.className = "btn btn-secondary";
    link.href = "/payment";
    link.textContent = "Payment & Exit";
    dashboardHeading.append(link);
  }
  document.querySelectorAll('#vehicleNumber, #bookVehicleNumber, #paymentSearchInput, #adminParkingVehicleNumber, input[name="vehicle_number"]')
    .forEach((field) => {
    field.addEventListener("input", () => { field.value = formatRegistrationInput(field.value); field.setCustomValidity(""); });
    field.addEventListener("blur", () => { field.value = formatRegistrationInput(field.value); field.setCustomValidity(field.value && !validRegistrationInput(field.value) ? "Invalid registration number. Use AA-00-AA-0000." : ""); });
  });
  document.querySelectorAll(".mobile-menu-toggle").forEach((button) => {
    const nav = document.getElementById(button.getAttribute("aria-controls"));
    if (!nav) return;
    button.addEventListener("click", () => {
      const open = nav.classList.toggle("is-open");
      button.setAttribute("aria-expanded", String(open));
    });
  });
});


// ============================================================
// UTILITY & TOAST NOTIFICATION HELPERS
// ============================================================

function showToast(message, type = "info") {
  let container = document.getElementById("toastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "toastContainer";
    container.className = "toast-container";
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span>${type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️"}</span>
    <div>${escapeHtml(message)}</div>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add("active");
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove("active");
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("smartpark-theme", theme);
  const icon = document.getElementById("themeIcon");
  if (icon) icon.innerText = theme === "light" ? "☾" : "☀";
}

function toggleTheme() {
  const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(nextTheme);
}

async function adminLogout() {
  await fetchAPI("/admin/logout", { method: "POST" });
  window.location.href = "/admin/login";
}


// Close modals when clicking overlay
window.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-overlay")) {
    if (e.target.id === "adminParkingEditor") {
      closeAdminParkingEditor();
    } else {
      e.target.classList.remove("active");
    }
  }
});

// ============================================================
// API CLIENT WRAPPER
// ============================================================

async function fetchAPI(endpoint, options = {}) {
  const requestOnce = async () => {
    const url = `${API_BASE}${endpoint}`;
    const defaultHeaders = { "Content-Type": "application/json" };
    return fetch(url, {
      ...options,
      credentials: "include",
      headers: {
        ...defaultHeaders,
        ...(sessionStorage.getItem("smartpark-csrf") ? { "X-CSRF-Token": sessionStorage.getItem("smartpark-csrf") } : {}),
        ...(options.headers || {})
      }
    });
  };
  try {
    let response = await requestOnce();
    // A page opened in a new tab can have a stale/missing token. Refresh it
    // from the authenticated session once, then retry the original request.
    if (response.status === 403 && options.method && options.method !== "GET") {
      const refresh = await fetch(`${API_BASE}/auth/me`, { credentials: "include" });
      const refreshedToken = refresh.headers.get("X-CSRF-Token");
      if (refreshedToken) {
        sessionStorage.setItem("smartpark-csrf", refreshedToken);
        response = await requestOnce();
      }
    }

    const csrf = response.headers.get("X-CSRF-Token");
    if (csrf) sessionStorage.setItem("smartpark-csrf", csrf);

    let data;
    const contentType = response.headers.get("content-type");
    if (contentType && contentType.includes("application/json")) {
      data = await response.json();
    } else {
      const text = await response.text();
      data = { success: response.ok, message: text };
    }

    return { ok: response.ok, status: response.status, data };
  } catch (error) {
    console.error(`API Call failed on ${endpoint}:`, error);
    return { ok: false, status: 0, data: { success: false, error: "Cannot connect to the SMARTPARK backend. Check the deployed backend/API service." } };
  }
}

// ============================================================
// LIVE PARKING GRID & STATUS CONTROLLER
// ============================================================

let currentFilterZone = "ALL";
let cachedSlots = [];

async function loadSystemStatusAndGrid() {
  // 1. Fetch system status counters
  const statusRes = await fetchAPI("/status");
  if (statusRes.ok && statusRes.data.capacity) {
    const cap = statusRes.data.capacity;
    const elTotal = document.getElementById("statTotalSlots");
    const elAvail = document.getElementById("statAvailSlots");
    const elOccup = document.getElementById("statOccupiedSlots");
    const elOccRate = document.getElementById("statOccupancyRate");

    if (elTotal) elTotal.innerText = cap.total;
    if (elAvail) elAvail.innerText = cap.available;
    if (elOccup) elOccup.innerText = cap.occupied;
    if (elOccRate) elOccRate.innerText = `${cap.occupancy_rate_percent}%`;
    initializeFacilityMap(statusRes.data.facility_location);
  }

  // 2. Fetch slot layout
  const slotsRes = await fetchAPI("/slots");
  if (slotsRes.ok && slotsRes.data.slots) {
    cachedSlots = slotsRes.data.slots;
    renderParkingGrid();
  }
}

let facilityMap;
window.addEventListener("resize", () => {
  if (facilityMap) facilityMap.invalidateSize({ pan: false });
});

function initializeFacilityMap(location) {
  const container = document.getElementById("facilityMap");
  if (!container || !location) {
    if (container) container.innerText = "Facility map coordinates are not configured yet.";
    return;
  }
  const latitude = Number(location.latitude);
  const longitude = Number(location.longitude);
  if (
    location.latitude === null || location.latitude === undefined ||
    location.longitude === null || location.longitude === undefined ||
    !Number.isFinite(latitude) || !Number.isFinite(longitude) ||
    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
  ) {
    container.innerText = "Facility map coordinates are not configured yet.";
    return;
  }
  const locateLink = document.getElementById("locateFacility");
  if (locateLink) {
    locateLink.href = `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${latitude},${longitude}`)}`;
    locateLink.hidden = false;
  }
  if (typeof L === "undefined") {
    container.innerText = "Map service is unavailable. Use the indoor slot navigation below.";
    return;
  }
  if (!facilityMap) {
    facilityMap = L.map(container).setView([latitude, longitude], 17);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(facilityMap);
    L.marker([latitude, longitude]).addTo(facilityMap).bindPopup("SMARTPARK facility");
  }
}

function renderParkingGrid() {
  const gridContainer = document.getElementById("parkingGrid");
  if (!gridContainer) return;

  const filtered = cachedSlots.filter(s => {
    if (currentFilterZone === "ALL") return true;
    if (currentFilterZone === "EV") return s.slot_type === "EV";
    return s.zone === currentFilterZone;
  });

  gridContainer.innerHTML = filtered.map(slot => {
    const statusClass = `status-${slot.status.toLowerCase()}`;
    const typeClass = slot.slot_type === "EV" ? "type-ev" : "";
    const adminDashboard = document.getElementById("adminGateLogFeed") !== null;
    const editableSlot = adminDashboard && ["OCCUPIED", "RESERVED"].includes(slot.status);
    const selectableSlot = Boolean(document.getElementById("prefSlotNumber")) && slot.status === "AVAILABLE";
    const interactive = editableSlot || selectableSlot;
    const vehicleNumber = adminDashboard && slot.active_vehicle_number
      ? `<div class="slot-vehicle">${escapeHtml(slot.active_vehicle_number)}</div>`
      : "";
    return `
      <div class="slot-card ${statusClass} ${typeClass}" data-slot-number="${escapeHtml(slot.slot_number)}" ${interactive ? 'role="button" tabindex="0"' : ""}>
        <div class="slot-number">${escapeHtml(slot.slot_number)}</div>
        <span class="slot-type-badge">${escapeHtml(slot.slot_type)}</span>
        ${vehicleNumber}
        <div class="slot-distance">📍 ${escapeHtml(slot.distance_from_entry)}m from gate</div>
        ${editableSlot ? `<button class="admin-edit-parking" type="button" data-edit-slot-id="${Number(slot.id)}" onclick="openAdminParkingEditorById(${Number(slot.id)}, event)">Edit parking</button>` : ""}
      </div>
    `;
  }).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  const grid = document.getElementById("parkingGrid");
  if (!grid) return;
  grid.addEventListener("click", event => {
    const editButton = event.target.closest("[data-edit-slot-id]");
    if (editButton) {
      event.stopPropagation();
      return;
    }
    const card = event.target.closest("[data-slot-number]");
    if (card) handleSlotClick(card.dataset.slotNumber);
  });
  grid.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest('[data-slot-number][role="button"]');
    if (!card) return;
    event.preventDefault();
    handleSlotClick(card.dataset.slotNumber);
  });
});

function filterSlots(zone, btnElement) {
  currentFilterZone = zone;
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  if (btnElement) btnElement.classList.add("active");
  renderParkingGrid();
}

function handleSlotClick(slotNumber) {
  const slot = cachedSlots.find(s => s.slot_number === slotNumber);
  if (!slot) return;

  if (document.getElementById("adminGateLogFeed") && ["OCCUPIED", "RESERVED"].includes(slot.status)) {
    openAdminParkingEditor(slot);
    return;
  }

  if (slot.status === "AVAILABLE") {
    // If on booking page, populate slot
    const slotInput = document.getElementById("prefSlotNumber");
    if (slotInput) {
      slotInput.value = slot.slot_number;
      showToast(`Selected Bay ${slot.slot_number} for booking.`, "info");
      return;
    }
  } else if (slot.status === "OCCUPIED") {
    showToast(`Slot ${slot.slot_number} is occupied.`, "info");
  }
}

let adminParkingEditorSlotId = null;
let adminParkingEditorPreviousFocus = null;

function openAdminParkingEditorById(slotId, event) {
  if (event) event.stopPropagation();
  const slot = cachedSlots.find(candidate => candidate.id === Number(slotId));
  if (!slot || !["OCCUPIED", "RESERVED"].includes(slot.status)) {
    showToast("This slot is no longer reserved or occupied. Refreshing the live parking grid.", "error");
    loadSystemStatusAndGrid();
    return;
  }
  openAdminParkingEditor(slot);
}

function openAdminParkingEditor(slot) {
  const dialog = document.getElementById("adminParkingEditor");
  if (!dialog) return;
  adminParkingEditorSlotId = slot.id;
  adminParkingEditorPreviousFocus = document.activeElement;
  const isReserved = slot.status === "RESERVED";
  document.getElementById("adminParkingEditorTitle").innerText =
    isReserved ? "Edit reserved parking" : "Edit occupied parking";
  document.getElementById("adminParkingEditorSlot").innerText =
    `${slot.slot_number} · ${slot.active_vehicle_number || "Vehicle number unavailable"} · ${slot.status}`;
  document.getElementById("adminParkingVehicleNumber").value = slot.active_vehicle_number || "";
  document.getElementById("adminParkingSlotType").value = slot.slot_type;
  document.getElementById("adminParkingEditorMessage").innerText = "";
  const removeButton = document.getElementById("adminForceRemoveParking");
  removeButton.disabled = false;
  removeButton.innerText = isReserved ? "Cancel reservation / release slot" : "Delete / force remove";
  removeButton.onclick = forceRemoveAdminParking;
  document.getElementById("adminParkingEditorNote").innerText = isReserved
    ? "This cancels the reservation, revokes its QR pass, and releases the slot. The reservation stays in history; no payment is recorded."
    : "Force remove closes the active session as unpaid and preserves it in history. It does not record a payment.";
  dialog.setAttribute("aria-hidden", "false");
  dialog.classList.add("active");
  document.getElementById("adminParkingVehicleNumber").focus();
}

function closeAdminParkingEditor() {
  const dialog = document.getElementById("adminParkingEditor");
  if (!dialog) return;
  dialog.classList.remove("active");
  dialog.setAttribute("aria-hidden", "true");
  adminParkingEditorSlotId = null;
  if (adminParkingEditorPreviousFocus instanceof HTMLElement) {
    adminParkingEditorPreviousFocus.focus();
  }
}

async function forceRemoveAdminParking() {
  if (adminParkingEditorSlotId === null) return;
  const slotId = adminParkingEditorSlotId;
  const slot = cachedSlots.find(candidate => candidate.id === slotId);
  if (!slot) {
    showToast("This parking slot is no longer in the live grid.", "error");
    closeAdminParkingEditor();
    return;
  }
  const confirmed = window.confirm(
    slot.status === "RESERVED"
      ? `Cancel the reservation at ${slot.slot_number}${slot.active_vehicle_number ? ` for ${slot.active_vehicle_number}` : ""} and release the slot? Its history will be retained.`
      : `Delete / force remove parking at ${slot.slot_number}${slot.active_vehicle_number ? ` for ${slot.active_vehicle_number}` : ""}? This closes the session as unpaid and releases the slot without recording payment.`
  );
  if (!confirmed) return;
  const button = document.getElementById("adminForceRemoveParking");
  button.disabled = true;
  const result = await fetchAPI(`/admin/slots/${encodeURIComponent(slotId)}/clear`, { method: "POST" });
  if (!result.ok || !result.data.success) {
    document.getElementById("adminParkingEditorMessage").innerText =
      result.data.error || "Unable to remove this parking session.";
    button.disabled = false;
    return;
  }
  closeAdminParkingEditor();
  showToast(result.data.message, "success");
  await loadSystemStatusAndGrid();
  await loadAnalytics();
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("adminParkingEditorForm");
  if (!form) return;
  const dialog = document.getElementById("adminParkingEditor");
  dialog.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      closeAdminParkingEditor();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled)")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (adminParkingEditorSlotId === null) return;
    const slotId = adminParkingEditorSlotId;
    const saveButton = form.querySelector('button[type="submit"]');
    const message = document.getElementById("adminParkingEditorMessage");
    saveButton.disabled = true;
    const result = await fetchAPI(`/admin/slots/${encodeURIComponent(slotId)}/edit`, {
      method: "PATCH",
      body: JSON.stringify({
        vehicle_number: document.getElementById("adminParkingVehicleNumber").value,
        slot_type: document.getElementById("adminParkingSlotType").value
      })
    });
    saveButton.disabled = false;
    if (!result.ok || !result.data.success) {
      message.innerText = result.data.error || "Unable to update this parking session.";
      return;
    }
    closeAdminParkingEditor();
    showToast(result.data.message, "success");
    await loadSystemStatusAndGrid();
  });
});

let adminQrStream = null;
let adminQrScanTimer = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[character]));
}

async function scanAdminQrToken(token) {
  const cleanedToken = String(token || "").trim();
  const resultBox = document.getElementById("qrScanResult");
  if (!cleanedToken) {
    showToast("Enter or scan a booking QR token.", "error");
    return;
  }

  const result = await fetchAPI("/admin/qr/scan", {
    method: "POST",
    body: JSON.stringify({ token: cleanedToken })
  });
  if (!result.ok || !result.data.success) {
    if (resultBox) resultBox.innerHTML = `<div class="qr-scan-error">${escapeHtml(result.data.error || "QR validation failed")}</div>`;
    return;
  }

  const booking = result.data.booking;
  const paymentAction = booking.payment_status === "PENDING"
    ? `<button class="btn btn-secondary" onclick="openAdminPayment('${escapeHtml(booking.booking_id)}')">Open Payment</button>`
    : "";
  const entryAction = "";
  const exitAction = "";
  const userName = booking.user ? (booking.user.full_name || booking.user.username) : "Guest booking";
  if (resultBox) {
    resultBox.innerHTML = `
      <div class="qr-scan-success">QR token validated</div>
      <div class="qr-details-grid">
        <span>Booking ID</span><strong>${escapeHtml(booking.booking_id)}</strong>
        <span>Vehicle</span><strong>${escapeHtml(booking.vehicle_number)}</strong>
        <span>Type</span><strong>${escapeHtml(booking.vehicle_type)}</strong>
        <span>User</span><strong>${escapeHtml(userName)}</strong>
        <span>Slot</span><strong>${escapeHtml(booking.slot?.slot_number || "Unassigned")}</strong>
        <span>Booking status</span><strong>${escapeHtml(booking.booking_status)}</strong>
        <span>Entry status</span><strong>${escapeHtml(booking.entry_status)}</strong>
        <span>Payment status</span><strong>${escapeHtml(booking.payment_status)}</strong>
        ${booking.entry_time ? `<span>Entry time</span><strong>${escapeHtml(formatISTDateTime(booking.entry_time))}</strong>` : ""}
      </div>
      <div class="qr-action-row">${entryAction}${paymentAction}${exitAction}</div>
    `;
  }
  showToast("QR token validated.", "success");
}

function openAdminPayment(token) {
  window.location.href = `payment.html?ticket=${encodeURIComponent(token)}`;
}

async function startAdminQrScanner() {
  const video = document.getElementById("adminQrVideo");
  if (!video) return;
  if (!window.isSecureContext && window.location.hostname !== "localhost") {
    showToast("Camera scanning requires HTTPS. Use manual token entry on this connection.", "error");
    return;
  }
  if (!("BarcodeDetector" in window)) {
    showToast("This browser does not provide QR detection. Use manual token entry.", "info");
    return;
  }
  try {
    adminQrStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    video.srcObject = adminQrStream;
    await video.play();
    const detector = new BarcodeDetector({ formats: ["qr_code"] });
    const scanFrame = async () => {
      if (!adminQrStream) return;
      try {
        const codes = await detector.detect(video);
        if (codes.length && codes[0].rawValue) {
          stopAdminQrScanner();
          const manualInput = document.getElementById("adminQrTokenInput");
          if (manualInput) manualInput.value = codes[0].rawValue;
          await scanAdminQrToken(codes[0].rawValue);
          return;
        }
      } catch (error) {
        console.debug("QR frame scan skipped", error);
      }
      adminQrScanTimer = window.setTimeout(scanFrame, 250);
    };
    scanFrame();
  } catch (error) {
    showToast("Camera permission was unavailable. Use manual token entry.", "error");
  }
}

function stopAdminQrScanner() {
  if (adminQrScanTimer) window.clearTimeout(adminQrScanTimer);
  adminQrScanTimer = null;
  if (adminQrStream) adminQrStream.getTracks().forEach(track => track.stop());
  adminQrStream = null;
  const video = document.getElementById("adminQrVideo");
  if (video) video.srcObject = null;
}

// ============================================================
// CUSTOMER BOOKING CONTROLLER (booking.html)
// ============================================================

async function handleBookingForm(e) {
  e.preventDefault();
  const vNumber = document.getElementById("bookVehicleNumber").value.trim();
  const vType = document.getElementById("bookVehicleType").value;
  const bookingTime = istDateTimeLocalToISO(document.getElementById("bookingTime")?.value);
  const prefSlot = document.getElementById("prefSlotNumber")?.value.trim();

  if (!vNumber) {
    showToast("Please enter your vehicle registration number.", "error");
    return;
  }

  if (!bookingTime) {
    showToast("Please select your parking date and time.", "error");
    return;
  }

  const res = await fetchAPI("/slots/reserve", {
    method: "POST",
    body: JSON.stringify({
      vehicle_number: vNumber,
      vehicle_type: vType,
      booking_time: bookingTime,
      slot_number: prefSlot || undefined
    })
  });

  if (res.ok && res.data.success) {
    showToast("Slot booked successfully!", "success");
    showTicketPassModal(res.data.ticket, { slot_number: res.data.ticket.slot_number }, res.data.navigation);
    loadSystemStatusAndGrid();
  } else {
    showToast(res.data.error || "Booking failed", "error");
  }
}

async function handleWalkIn() {
  const vNumber = document.getElementById("bookVehicleNumber").value.trim();
  const vType = document.getElementById("bookVehicleType").value;
  if (!vNumber) { showToast("Enter your vehicle registration number first.", "error"); return; }
  const res = await fetchAPI("/customer/walk-in", { method: "POST", body: JSON.stringify({ vehicle_number: vNumber, vehicle_type: vType }) });
  if (res.ok && res.data.success) {
    showToast("Walk-in QR pass created. Scan it at the entrance.", "success");
    showTicketPassModal(res.data.ticket, { slot_number: res.data.ticket.slot_number }, null);
    loadSystemStatusAndGrid();
  } else showToast(res.data.error || "Walk-in entry is unavailable.", "error");
}

function showTicketPassModal(ticket, slot, nav) {
  const qrImg = document.getElementById("modalQrImage");
  const qrEnlargeLink = document.getElementById("modalQrEnlarge");
  const codeEl = document.getElementById("modalTicketCode");
  const plateEl = document.getElementById("modalPlateNumber");
  const slotEl = document.getElementById("modalSlotNumber");
  const timeEl = document.getElementById("modalEntryTime");
  const navContainer = document.getElementById("modalNavInstructions");

  if (qrImg) {
    qrImg.src = ticketQrUrl(ticket.ticket_code);
  }
  if (qrEnlargeLink) {
    qrEnlargeLink.href = ticketQrUrl(ticket.ticket_code);
  }
  if (codeEl) codeEl.innerText = ticket.ticket_code;
  if (plateEl) plateEl.innerText = ticket.vehicle_number;
  if (slotEl) slotEl.innerText = slot ? slot.slot_number : (ticket.slot_number || "Assigned");
  if (timeEl) timeEl.innerText = formatISTDateTime(ticket.booking_time || ticket.entry_time);

  if (navContainer && nav && nav.turn_by_turn) {
    navContainer.innerHTML = `
      <h4 style="font-size:0.9rem; margin-top:0.75rem; color:#60a5fa;">📍 Dijkstra Turn-by-Turn Navigation (${nav.total_distance_meters}m):</h4>
      <ol style="margin-left: 1.25rem; font-size: 0.8rem; color: #94a3b8; margin-top: 0.25rem;">
        ${nav.turn_by_turn.map(s => `<li>${escapeHtml(s)}</li>`).join("")}
      </ol>
    `;
  }

  openModal("ticketPassModal");
}

// ============================================================
// CHECKOUT & PAYMENT CONTROLLER (payment.html)
// ============================================================

let currentActiveTicket = null;

async function loadCustomerPaymentVehicles(preferredTicketCode = null) {
  const section = document.getElementById("paymentCustomerVehicles");
  const list = document.getElementById("paymentVehicleList");
  const message = document.getElementById("paymentVehiclesMessage");
  if (!section || !list || !message) return;

  const auth = await fetchAPI("/auth/me");
  if (!auth.ok || !auth.data.authenticated) return;
  section.hidden = false;

  const history = await fetchAPI("/customer/history");
  if (!history.ok || !history.data.success) {
    message.textContent = history.data.error || "Could not load your vehicles.";
    return;
  }

  const vehicles = (history.data.bookings || []).filter(ticket => ticket.status === "ACTIVE");
  if (!vehicles.length) {
    message.textContent = "You do not have an active vehicle or upcoming reservation.";
    return;
  }

  message.textContent = "Select one of your vehicles to view its session and payment details.";
  list.replaceChildren();
  vehicles.forEach(ticket => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "payment-vehicle-card";
    button.setAttribute("aria-label", `Show vehicle ${ticket.vehicle_number}, ${ticket.entry_time ? "checked in" : "upcoming reservation"}`);

    const vehicle = document.createElement("strong");
    vehicle.textContent = ticket.vehicle_number;
    const status = document.createElement("span");
    status.textContent = ticket.entry_time ? "Active parking session" : "Upcoming reservation";
    const slot = document.createElement("span");
    slot.textContent = ticket.slot_number ? `Slot ${ticket.slot_number}` : "Slot not assigned";
    button.append(vehicle, status, slot);
    button.addEventListener("click", () => lookupTicketForPayment(ticket.ticket_code));
    list.append(button);
  });

  const selected = vehicles.find(ticket => ticket.ticket_code === preferredTicketCode)
    || vehicles.find(ticket => ticket.entry_time)
    || vehicles[0];
  if (selected) await lookupTicketForPayment(selected.ticket_code);
}

async function lookupTicketForPayment(ticketOrPlate) {
  const searchInput = document.getElementById("paymentSearchInput");
  const query = ticketOrPlate || (searchInput ? searchInput.value.trim() : "");

  if (!query) {
    showToast("Please enter a vehicle number or ticket code.", "error");
    return;
  }

  const res = await fetchAPI(`/tickets/${encodeURIComponent(query)}`);
  if (res.ok && res.data.success) {
    currentActiveTicket = res.data.ticket;
    renderBillingDetails(res.data.ticket);
  } else {
    showToast(res.data.error || "No active ticket found", "error");
    const billBox = document.getElementById("billingDetailsBox");
    if (billBox) billBox.style.display = "none";
  }
}

function renderBillingDetails(ticket) {
  const billBox = document.getElementById("billingDetailsBox");
  if (!billBox) return;

  const bill = ticket.billing;
  document.getElementById("billTicketCode").innerText = ticket.ticket_code;
  document.getElementById("billVehiclePlate").innerText = ticket.vehicle_number;
  document.getElementById("billReservationStatus").innerText = ticket.reservation_status || (ticket.entry_time ? "CHECKED_IN" : "UPCOMING / NOT CHECKED IN");
  document.getElementById("billSlot").innerText = ticket.slot_number
    ? `Zone ${ticket.zone || "A"}, Slot ${ticket.slot_number}`
    : "Not assigned";
  document.getElementById("billScheduledTime").innerText = ticket.booking_time
    ? `${formatISTDateTime(ticket.booking_time)} → ${ticket.scheduled_end_time ? formatISTDateTime(ticket.scheduled_end_time) : "—"}`
    : "Walk-in / no scheduled time";
  const actualEntryRow = document.getElementById("billActualEntryRow");
  if (actualEntryRow) actualEntryRow.hidden = !ticket.entry_time;
  const actualTimes = document.getElementById("billActualTimes");
  if (actualTimes) {
    actualTimes.innerText = `${ticket.entry_time ? formatISTDateTime(ticket.entry_time) : "—"} → ${ticket.exit_time ? formatISTDateTime(ticket.exit_time) : "In progress"}`;
  }
  document.getElementById("billDuration").innerText = bill ? bill.duration_formatted : "Not started";
  document.getElementById("billBaseRate").innerText = bill ? `₹${bill.base_rate.toFixed(2)}` : "—";
  document.getElementById("billAdditionalHours").innerText = bill ? `${bill.additional_hours} hr(s) (+₹${bill.additional_charge.toFixed(2)})` : "—";
  
  const peakRow = document.getElementById("billPeakRow");
  if (peakRow) {
    peakRow.style.display = bill && bill.is_peak_hour ? "flex" : "none";
    document.getElementById("billPeakSurcharge").innerText = bill ? `₹${bill.peak_surcharge.toFixed(2)}` : "—";
  }

  document.getElementById("billTax").innerText = bill ? `₹${bill.tax_gst.toFixed(2)}` : "—";
  document.getElementById("billGrandTotal").innerText = bill ? `₹${bill.grand_total.toFixed(2)}` : "—";
  const actions = document.getElementById("paymentActions");
  const estimateNote = document.getElementById("estimateNote");
  const checkedIn = Boolean(ticket.entry_time);
  const completed = ticket.status === "COMPLETED";
  const secondScanComplete = Boolean(ticket.payment_scan_ready);
  const cashPending = ticket.payment_method === "CASH" && ticket.payment_status === "PENDING";
  const canPay = checkedIn && secondScanComplete && !completed && !cashPending && ticket.payment_status !== "PAID";
  if (actions) actions.hidden = !canPay;
  if (estimateNote) estimateNote.hidden = checkedIn || completed;
  const totalLabel = document.getElementById("billTotalLabel");
  if (totalLabel) {
    totalLabel.innerText = completed
      ? "Final total:"
      : checkedIn ? "Current estimated total:" : "Expected total:";
  }
  const paymentStatus = document.getElementById("billPaymentStatus");
  if (paymentStatus) paymentStatus.innerText = ticket.payment_status || "PENDING";
  const scanNote = document.getElementById("paymentScanNote");
  if (scanNote) {
    scanNote.hidden = !checkedIn || completed || (!cashPending && secondScanComplete);
    scanNote.textContent = cashPending
      ? "Cash payment is awaiting administrator confirmation. The slot remains occupied until cash is confirmed."
      : "Payment is locked until the QR ticket is scanned a second time at the exit scanner.";
  }

  // Payment status badge
  const payBtn = document.getElementById("processPaymentBtn");
  if (payBtn) payBtn.disabled = !canPay;

  billBox.style.display = "block";
}

async function executePayment(method = "UPI_QR") {
  if (!currentActiveTicket || !currentActiveTicket.entry_time || !currentActiveTicket.payment_scan_ready ||
      currentActiveTicket.status !== "ACTIVE" || currentActiveTicket.payment_status === "PAID") {
    showToast("Scan the QR ticket a second time at the exit scanner before payment.", "error");
    return;
  }

  showToast(`Processing development payment via ${method}...`, "info");

  const res = await fetchAPI("/payment/process", {
    method: "POST",
    body: JSON.stringify({
      ticket_code: currentActiveTicket.ticket_code,
      payment_method: method
    })
  });

  if (res.ok && res.data.success) {
    if (res.data.cash_pending) {
      currentActiveTicket.payment_method = "CASH";
      currentActiveTicket.payment_status = "PENDING";
      renderBillingDetails(currentActiveTicket);
      showToast("Cash payment requested. An administrator must confirm receipt before the slot is released.", "info");
      return;
    }
    const ticketRes = await fetchAPI(`/tickets/${encodeURIComponent(currentActiveTicket.ticket_code)}`);
    if (ticketRes.ok && ticketRes.data.success) {
      currentActiveTicket = ticketRes.data.ticket;
      renderBillingDetails(currentActiveTicket);
    }
    showToast("Development payment recorded; session completed and slot released. No real money was transferred.", "success");
    showPaymentReceiptModal(res.data.receipt);
  } else {
    showToast(res.data.error || "Payment failed", "error");
  }
}

function showPaymentReceiptModal(receipt) {
  const rCode = document.getElementById("receiptTicketCode");
  const rPlate = document.getElementById("receiptVehiclePlate");
  const rAmount = document.getElementById("receiptAmount");
  const rMethod = document.getElementById("receiptMethod");
  const rTime = document.getElementById("receiptTimestamp");

  if (rCode) rCode.innerText = receipt.ticket_code;
  if (rPlate) rPlate.innerText = receipt.vehicle_number;
  if (rAmount) rAmount.innerText = `₹${receipt.amount_paid.toFixed(2)}`;
  if (rMethod) rMethod.innerText = receipt.payment_method;
  if (rTime) rTime.innerText = receipt.exit_time
    ? `${formatISTDateTime(receipt.entry_time)} → ${formatISTDateTime(receipt.exit_time)}`
    : formatISTDateTime(receipt.timestamp);

  openModal("paymentReceiptModal");
}

// ============================================================
// ANALYTICS & AUDIT LOGS (admin.html)
// ============================================================

async function loadAnalytics() {
  const res = await fetchAPI("/analytics");
  if (!res.ok || !res.data.success) return;

  const m = res.data.metrics;
  const elOcc = document.getElementById("adminOccupancyRate");
  const elVeh = document.getElementById("adminVehiclesToday");
  const elRev = document.getElementById("adminRevenueToday");

  if (elOcc) elOcc.innerText = `${m.occupancy_rate}%`;
  if (elVeh) elVeh.innerText = m.total_vehicles_today;
  if (elRev) elRev.innerText = `₹${m.revenue_today.toFixed(2)}`;

  // Render Gate Logs
  const logFeed = document.getElementById("adminGateLogFeed");
  if (logFeed && res.data.recent_logs) {
    logFeed.innerHTML = res.data.recent_logs.map(log => {
      const typeClass = log.gate_type === "ENTRY" ? "log-type-entry" : "log-type-exit";
      return `
        <div class="log-entry">
          <span class="log-time">[${log.timestamp ? formatISTDateTime(log.timestamp) : ""}]</span>
          <span class="${typeClass}">${log.gate_type}</span>
          <span><strong>${escapeHtml(log.vehicle_number || "UNKNOWN")}</strong> (${escapeHtml(log.trigger_source)}) - ${escapeHtml(log.notes || log.action_taken)}</span>
        </div>
      `;
    }).join("");
  }
}

async function loadCashPaymentQueue() {
  const list = document.getElementById("cashPaymentList");
  if (!list) return;

  const response = await fetchAPI("/admin/payment/cash-pending");
  if (!response.ok || !response.data.success) {
    list.textContent = response.data.error || "Could not load pending cash payments.";
    return;
  }

  list.replaceChildren();
  if (!response.data.payments.length) {
    const empty = document.createElement("p");
    empty.className = "admin-empty";
    empty.textContent = "No cash payments are awaiting confirmation.";
    list.append(empty);
    return;
  }

  response.data.payments.forEach(payment => {
    const card = document.createElement("article");
    card.className = "cash-payment-card";
    const details = document.createElement("div");
    const vehicle = document.createElement("strong");
    vehicle.textContent = payment.vehicle_number;
    const info = document.createElement("p");
    info.textContent = `Ticket ${payment.ticket_code} · Slot ${payment.slot_number || "—"} · Due ₹${Number(payment.amount_due).toFixed(2)}`;
    details.append(vehicle, info);

    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "btn btn-primary";
    confirm.textContent = "Confirm cash received & release slot";
    confirm.addEventListener("click", async () => {
      if (!window.confirm(`Confirm cash received for ${payment.vehicle_number} (₹${Number(payment.amount_due).toFixed(2)}) and release ${payment.slot_number || "its slot"}?`)) return;
      confirm.disabled = true;
      const result = await fetchAPI("/admin/payment/release-cash", {
        method: "POST",
        body: JSON.stringify({ ticket_code: payment.ticket_code })
      });
      if (!result.ok || !result.data.success) {
        confirm.disabled = false;
        showToast(result.data.error || "Cash confirmation failed.", "error");
        return;
      }
      showToast(result.data.message, "success");
      await loadCashPaymentQueue();
      await loadSystemStatusAndGrid();
      await loadAnalytics();
    });

    card.append(details, confirm);
    list.append(card);
  });
}

function scheduleDailyAdminRefresh() {
  if (!document.getElementById("adminGateLogFeed")) return;

  const now = new Date();
  const istParts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(now).reduce((values, part) => {
    values[part.type] = part.value;
    return values;
  }, {});
  const nextDate = new Date(Date.UTC(
    Number(istParts.year),
    Number(istParts.month) - 1,
    Number(istParts.day) + 1
  ));
  const nextMidnightUtc = Date.UTC(
    nextDate.getUTCFullYear(),
    nextDate.getUTCMonth(),
    nextDate.getUTCDate()
  ) - (5 * 60 + 30) * 60 * 1000;
  window.setTimeout(async () => {
    await loadAnalytics();
    scheduleDailyAdminRefresh();
  }, nextMidnightUtc - now.getTime());
}

// ============================================================
// INITIALIZATION
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
  const savedTheme = localStorage.getItem("smartpark-theme");
  const initialTheme = savedTheme || (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  applyTheme(initialTheme);

  // Load grid and status
  loadSystemStatusAndGrid();
  scheduleDailyAdminRefresh();
  loadCashPaymentQueue();

  // Auto-polling every 4 seconds to sync status across clients
  setInterval(() => {
    loadSystemStatusAndGrid();
    loadCashPaymentQueue();
    if (document.getElementById("adminGateLogFeed")) {
      loadAnalytics();
    }
  }, 4000);

  // Check URL query parameters (e.g. payment.html?ticket=SP-12345678)
  const urlParams = new URLSearchParams(window.location.search);
  const ticketParam = urlParams.get("ticket");
  if (ticketParam) {
    const input = document.getElementById("paymentSearchInput");
    if (input) input.value = ticketParam;
    lookupTicketForPayment(ticketParam);
    loadCustomerPaymentVehicles(ticketParam);
  } else if (document.getElementById("paymentCustomerVehicles")) {
    loadCustomerPaymentVehicles();
  }

  // Booking Form listener
  const bookForm = document.getElementById("bookingForm");
  if (bookForm) {
    const bookingTimeInput = document.getElementById("bookingTime");
    if (bookingTimeInput) {
      const earliest = new Date(Date.now() + 15 * 60 * 1000);
      bookingTimeInput.min = istDateTimeLocalValue(new Date());
      if (!bookingTimeInput.value) bookingTimeInput.value = istDateTimeLocalValue(earliest);
    }

    const savedVehicleNumber = sessionStorage.getItem("smartpark-booking-vehicle-number");
    const savedVehicleType = sessionStorage.getItem("smartpark-booking-vehicle-type");
    sessionStorage.removeItem("smartpark-booking-vehicle-number");
    sessionStorage.removeItem("smartpark-booking-vehicle-type");

    const vehicleNumberInput = document.getElementById("bookVehicleNumber");
    if (savedVehicleNumber && vehicleNumberInput) {
      const formattedNumber = formatRegistrationInput(savedVehicleNumber);
      if (validRegistrationInput(formattedNumber)) {
        vehicleNumberInput.value = formattedNumber;
      }
    }

    const vehicleTypeSelect = document.getElementById("bookVehicleType");
    if (savedVehicleType && vehicleTypeSelect &&
        Array.from(vehicleTypeSelect.options).some(option => option.value === savedVehicleType)) {
      vehicleTypeSelect.value = savedVehicleType;
    }

    bookForm.addEventListener("submit", handleBookingForm);
  }
  const walkInButton = document.getElementById("walkInButton");
  if (walkInButton) walkInButton.addEventListener("click", handleWalkIn);
});


// Protect the admin UI even when a static deployment serves the shell.
// The actual authorization remains enforced by Flask on every admin API route.
document.addEventListener("DOMContentLoaded", async () => {
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  if ((path === "/admin" || path === "/admin.html") && !path.endsWith("admin-login")) {
    const result = await fetchAPI("/admin/me");
    if (!result.ok || !result.data.authenticated) {
      window.location.replace("/admin/login");
    }
  }
});
