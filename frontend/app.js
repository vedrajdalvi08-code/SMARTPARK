/**
 * SMARTPARK - Frontend Client Application
 * Handles API communication, interactive slot visualization,
 * dynamic pricing calculations, simulated payment gateway, and admin testing tools.
 */

// Use same-origin API routes in Flask/Vercel; support local HTML file testing too.
const API_BASE = window.location.protocol === "file:"
  ? "http://127.0.0.1:5000/api"
  : "/api";

const SAMPLE_DEMO_PLATES = [
  "KA-01-MJ-5021",
  "MH-12-PQ-8891",
  "DL-04-CA-1029",
  "TN-09-AK-3344",
  "UP-32-BZ-7711",
  "HR-26-DK-4590",
  "TS-07-EA-6218",
  "GJ-01-RX-9943"
];

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
    <div>${message}</div>
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

async function loadDemoModeSetting() {
  const result = await fetchAPI("/admin/settings/demo-mode");
  if (!result.ok) return;
  const toggle = document.getElementById("demoModeToggle");
  const status = document.getElementById("demoModeStatus");
  if (toggle) toggle.checked = result.data.demo_mode;
  if (status) status.innerText = result.data.demo_mode ? "Software testing mode is enabled" : "Software testing mode is disabled";
}

async function setDemoMode(enabled) {
  const result = await fetchAPI("/admin/settings/demo-mode", {
    method: "POST",
    body: JSON.stringify({ demo_mode: enabled })
  });
  const status = document.getElementById("demoModeStatus");
  if (result.ok && result.data.success) {
    if (status) status.innerText = enabled ? "Software testing mode is enabled" : "Software testing mode is disabled";
    showToast("Simulation mode updated.", "success");
  } else {
    showToast(result.data.error || "Could not update simulation mode.", "error");
    loadDemoModeSetting();
  }
}

// Close modals when clicking overlay
window.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-overlay")) {
    e.target.classList.remove("active");
  }
});

// ============================================================
// API CLIENT WRAPPER
// ============================================================

async function fetchAPI(endpoint, options = {}) {
  try {
    const url = `${API_BASE}${endpoint}`;
    const defaultHeaders = { "Content-Type": "application/json" };
    const response = await fetch(url, {
      ...options,
      credentials: "include",
      headers: { ...defaultHeaders, ...(options.headers || {}) }
    });

    const data = await response.json();
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
  }

  // 2. Fetch slot layout
  const slotsRes = await fetchAPI("/slots");
  if (slotsRes.ok && slotsRes.data.slots) {
    cachedSlots = slotsRes.data.slots;
    renderParkingGrid();
    updateExitVehicleDropdown(cachedSlots);
  }
}

function renderParkingGrid() {
  const gridContainer = document.getElementById("parkingGrid");
  if (!gridContainer) return;

  const filtered = cachedSlots.filter(s => {
    if (currentFilterZone === "ALL") return true;
    if (currentFilterZone === "EV") return s.slot_type === "EV";
    if (currentFilterZone === "FLOOR1") return s.floor_level === 1;
    return s.zone === currentFilterZone;
  });

  gridContainer.innerHTML = filtered.map(slot => {
    const statusClass = `status-${slot.status.toLowerCase()}`;
    const typeClass = slot.slot_type === "EV" ? "type-ev" : "";
    return `
      <div class="slot-card ${statusClass} ${typeClass}" onclick="handleSlotClick('${slot.slot_number}')">
        <div class="slot-number">${slot.slot_number}</div>
        <span class="slot-type-badge">${slot.slot_type}</span>
        ${slot.active_vehicle_number ? `<div class="slot-vehicle">${slot.active_vehicle_number}</div>` : ""}
        <div class="slot-distance">📍 ${slot.distance_from_entry}m from gate</div>
      </div>
    `;
  }).join("");
}

function filterSlots(zone, btnElement) {
  currentFilterZone = zone;
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  if (btnElement) btnElement.classList.add("active");
  renderParkingGrid();
}

function handleSlotClick(slotNumber) {
  const slot = cachedSlots.find(s => s.slot_number === slotNumber);
  if (!slot) return;

  if (slot.status === "AVAILABLE") {
    // If on booking page, populate slot
    const slotInput = document.getElementById("prefSlotNumber");
    if (slotInput) {
      slotInput.value = slot.slot_number;
      showToast(`Selected Bay ${slot.slot_number} for booking.`, "info");
      return;
    }
  } else if (slot.status === "OCCUPIED" && slot.active_vehicle_number) {
    // Quick search payment
    showToast(`Slot ${slot.slot_number} occupied by ${slot.active_vehicle_number}.`, "info");
  }
}

function updateExitVehicleDropdown(slots) {
  const exitSelect = document.getElementById("simExitVehicleSelect");
  if (!exitSelect) return;

  const occupied = slots.filter(s => s.status === "OCCUPIED" && s.active_vehicle_number);
  if (occupied.length === 0) {
    exitSelect.innerHTML = `<option value="">No vehicles currently parked</option>`;
    return;
  }

  exitSelect.innerHTML = `
    <option value="">-- Select Parked Vehicle --</option>
    ${occupied.map(s => `<option value="${s.active_vehicle_number}">${s.active_vehicle_number} (Bay ${s.slot_number})</option>`).join("")}
  `;
}

// ============================================================
// SIMULATION ENGINE (ADMIN PANEL)
// ============================================================

function getRandomDemoPlate() {
  const randomPlate = SAMPLE_DEMO_PLATES[Math.floor(Math.random() * SAMPLE_DEMO_PLATES.length)];
  const input = document.getElementById("simVehicleNumber");
  if (input) input.value = randomPlate;
  const preview = document.getElementById("simPlatePreview");
  if (preview) preview.innerText = randomPlate;
}

async function triggerSimulatedEntry() {
  const plateInput = document.getElementById("simVehicleNumber");
  const typeSelect = document.getElementById("simVehicleType");
  const bookingInput = document.getElementById("simBookingToken");
  const vehicleNumber = plateInput ? plateInput.value.trim() : "";
  const vehicleType = typeSelect ? typeSelect.value : "COMPACT";
  const bookingToken = bookingInput ? bookingInput.value.trim() : "";

  const payload = {
    vehicle_number: vehicleNumber || undefined,
    vehicle_type: vehicleType,
    booking_id: bookingToken || undefined,
    trigger_source: "MANUAL_SIM"
  };

  showToast(bookingToken ? "Validating booking for software entry..." : "Allocating a software walk-in slot...", "info");

  const res = await fetchAPI("/entry/simulate", {
    method: "POST",
    body: JSON.stringify(payload)
  });

  if (res.ok && res.data.success) {
    showToast(res.data.message, "success");
    // Show Pass Modal
    showTicketPassModal(res.data.ticket, res.data.allocated_slot, res.data.navigation);
    // Reload Grid
    loadSystemStatusAndGrid();
    loadAnalytics();
  } else {
    showToast(res.data.error || "Entry failed", "error");
  }
}

async function triggerWalkInEntry() {
  const bookingInput = document.getElementById("simBookingToken");
  if (bookingInput) bookingInput.value = "";
  await triggerSimulatedEntry();
}

function animateEntryBarrier() {
  const visualizer = document.getElementById("entryBarrierBox");
  if (visualizer) {
    visualizer.classList.add("open");
    setTimeout(() => {
      visualizer.classList.remove("open");
    }, 6000);
  }
}

function animateExitBarrier() {
  const visualizer = document.getElementById("exitBarrierBox");
  if (visualizer) {
    visualizer.classList.add("open");
    setTimeout(() => {
      visualizer.classList.remove("open");
    }, 6000);
  }
}

async function triggerSimulatedExit() {
  const select = document.getElementById("simExitVehicleSelect");
  const manualInput = document.getElementById("simExitManualInput");

  let vehicleOrTicket = "";
  if (select && select.value) {
    vehicleOrTicket = select.value;
  } else if (manualInput && manualInput.value.trim()) {
    vehicleOrTicket = manualInput.value.trim();
  }

  if (!vehicleOrTicket) {
    showToast("Please choose or enter a vehicle plate / ticket code to exit.", "error");
    return;
  }

  const res = await fetchAPI("/exit/simulate", {
    method: "POST",
    body: JSON.stringify({ vehicle_number: vehicleOrTicket, trigger_source: "MANUAL_SIM" })
  });

  if (res.ok && res.data.success) {
    showToast(res.data.message, "success");
    loadSystemStatusAndGrid();
    loadAnalytics();
  } else if (res.status === 402) {
    // Payment Pending
    showToast("Vehicle payment is PENDING. Redirecting to payment...", "error");
    if (confirm("Parking fee is unpaid. Would you like to settle the payment now?")) {
      window.location.href = `payment.html?ticket=${encodeURIComponent(vehicleOrTicket)}`;
    }
  } else {
    showToast(res.data.error || "Exit check failed", "error");
  }
}

async function simulateAdminPayment() {
  const input = document.getElementById("simPaymentToken");
  const token = input ? input.value.trim() : "";
  if (!token) {
    showToast("Enter a booking or session token first.", "error");
    return;
  }
  await lookupTicketForPayment(token);
  if (currentActiveTicket && currentActiveTicket.payment_status !== "PAID") {
    await executeSimulatedPayment("UPI_QR");
  }
}

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
  const entryAction = booking.entry_status === "NOT_ENTERED"
    ? `<button class="btn btn-primary" onclick="entryFromScannedQr('${escapeHtml(booking.booking_id)}')">Simulate Entry</button>`
    : "";
  const exitAction = booking.entry_status === "ENTERED" && booking.payment_status === "PAID"
    ? `<button class="btn btn-success" onclick="exitFromScannedQr('${escapeHtml(booking.booking_id)}')">Simulate Exit</button>`
    : "";
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
        ${booking.entry_time ? `<span>Entry time</span><strong>${escapeHtml(booking.entry_time)}</strong>` : ""}
      </div>
      <div class="qr-action-row">${entryAction}${paymentAction}${exitAction}</div>
    `;
  }
  showToast("QR token validated.", "success");
}

function entryFromScannedQr(token) {
  const input = document.getElementById("simBookingToken");
  if (input) input.value = token;
  triggerSimulatedEntry();
}

function openAdminPayment(token) {
  window.location.href = `payment.html?ticket=${encodeURIComponent(token)}`;
}

async function exitFromScannedQr(token) {
  const result = await fetchAPI("/exit/simulate", {
    method: "POST",
    body: JSON.stringify({ ticket_code: token, trigger_source: "MANUAL_SIM" })
  });
  if (result.ok && result.data.success) {
    showToast(result.data.message, "success");
    loadSystemStatusAndGrid();
    loadAnalytics();
    scanAdminQrToken(token);
  } else {
    showToast(result.data.error || "Exit failed", "error");
  }
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

async function resetDemoSystem() {
  if (!confirm("Are you sure you want to reset all slots and tickets to empty?")) return;

  const res = await fetchAPI("/admin/reset-demo", { method: "POST" });
  if (res.ok && res.data.success) {
    showToast(res.data.message, "success");
    loadSystemStatusAndGrid();
    loadAnalytics();
  } else {
    showToast(res.data.error || "Reset failed", "error");
  }
}

// ============================================================
// CUSTOMER BOOKING CONTROLLER (booking.html)
// ============================================================

async function handleBookingForm(e) {
  e.preventDefault();
  const vNumber = document.getElementById("bookVehicleNumber").value.trim();
  const vType = document.getElementById("bookVehicleType").value;
  const bookingTime = document.getElementById("bookingTime")?.value;
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

function showTicketPassModal(ticket, slot, nav) {
  const qrImg = document.getElementById("modalQrImage");
  const codeEl = document.getElementById("modalTicketCode");
  const plateEl = document.getElementById("modalPlateNumber");
  const slotEl = document.getElementById("modalSlotNumber");
  const timeEl = document.getElementById("modalEntryTime");
  const navContainer = document.getElementById("modalNavInstructions");

  if (qrImg) {
    // QR contains only the opaque booking/session token.
    const qrData = encodeURIComponent(`SMARTPARK:${ticket.ticket_code}`);
    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${qrData}`;
  }
  if (codeEl) codeEl.innerText = ticket.ticket_code;
  if (plateEl) plateEl.innerText = ticket.vehicle_number;
  if (slotEl) slotEl.innerText = slot ? slot.slot_number : (ticket.slot_number || "Assigned");
  if (timeEl) timeEl.innerText = ticket.booking_time || ticket.entry_time;

  if (navContainer && nav && nav.turn_by_turn) {
    navContainer.innerHTML = `
      <h4 style="font-size:0.9rem; margin-top:0.75rem; color:#60a5fa;">📍 Dijkstra Turn-by-Turn Navigation (${nav.total_distance_meters}m):</h4>
      <ol style="margin-left: 1.25rem; font-size: 0.8rem; color: #94a3b8; margin-top: 0.25rem;">
        ${nav.turn_by_turn.map(s => `<li>${s}</li>`).join("")}
      </ol>
    `;
  }

  openModal("ticketPassModal");
}

// ============================================================
// CHECKOUT & PAYMENT CONTROLLER (payment.html)
// ============================================================

let currentActiveTicket = null;

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
  document.getElementById("billDuration").innerText = bill.duration_formatted;
  document.getElementById("billBaseRate").innerText = `₹${bill.base_rate.toFixed(2)}`;
  document.getElementById("billAdditionalHours").innerText = `${bill.additional_hours} hr(s) (+₹${bill.additional_charge.toFixed(2)})`;
  
  const peakRow = document.getElementById("billPeakRow");
  if (peakRow) {
    peakRow.style.display = bill.is_peak_hour ? "flex" : "none";
    document.getElementById("billPeakSurcharge").innerText = `₹${bill.peak_surcharge.toFixed(2)}`;
  }

  document.getElementById("billTax").innerText = `₹${bill.tax_gst.toFixed(2)}`;
  document.getElementById("billGrandTotal").innerText = `₹${bill.grand_total.toFixed(2)}`;

  // Payment status badge
  const payBtn = document.getElementById("processPaymentBtn");
  if (ticket.payment_status === "PAID") {
    if (payBtn) payBtn.disabled = true;
    showToast("This parking ticket is already PAID!", "info");
  } else {
    if (payBtn) payBtn.disabled = false;
  }

  billBox.style.display = "block";
}

async function executeSimulatedPayment(method = "UPI_QR") {
  if (!currentActiveTicket) {
    showToast("No active ticket selected for payment.", "error");
    return;
  }

  showToast(`Processing simulated ${method} payment...`, "info");

  const res = await fetchAPI("/payment/process", {
    method: "POST",
    body: JSON.stringify({
      ticket_code: currentActiveTicket.ticket_code,
      payment_method: method
    })
  });

  if (res.ok && res.data.success) {
    showToast("Payment successful! Receipt generated.", "success");
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
  if (rTime) rTime.innerText = receipt.timestamp;

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
          <span class="log-time">[${log.timestamp ? log.timestamp.split(" ")[1] : ""}]</span>
          <span class="${typeClass}">${log.gate_type}</span>
          <span><strong>${log.vehicle_number || "UNKNOWN"}</strong> (${log.trigger_source}) - ${log.notes || log.action_taken}</span>
        </div>
      `;
    }).join("");
  }
}

// ============================================================
// INITIALIZATION
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(localStorage.getItem("smartpark-theme") || "dark");

  if (document.getElementById("demoModeToggle")) {
    loadDemoModeSetting();
  }

  // Load grid and status
  loadSystemStatusAndGrid();

  // Auto-polling every 4 seconds to sync status across clients
  setInterval(() => {
    loadSystemStatusAndGrid();
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
  }

  // Booking Form listener
  const bookForm = document.getElementById("bookingForm");
  if (bookForm) {
    bookForm.addEventListener("submit", handleBookingForm);
  }
});


// Protect the admin simulator UI even when it is served as a static Vercel page.
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
