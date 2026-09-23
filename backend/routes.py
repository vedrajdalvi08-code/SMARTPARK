"""
SMARTPARK - REST API Routes Module
Exposes endpoints for Customer Booking, Gate Automation, Dynamic Pricing,
Live Hardware Simulation, and Analytics.
"""

import uuid
import datetime
import logging
import secrets
from functools import wraps
from flask import Blueprint, request, jsonify, session
from config import Config
import database
from database import (
    ParkingSlot, Ticket, GateLog, SensorTelemetry, User, SystemConfig,
    get_db
)
from services import (
    SlotAllocationService, ANPRService, BillingService,
    IoTGatewayService, facility_graph
)

api = Blueprint("api", __name__, url_prefix="/api")
logger = logging.getLogger("smartpark.routes")


def admin_required(route_handler):
    """Require an authenticated administrator for admin-only API routes."""
    @wraps(route_handler)
    def wrapped(*args, **kwargs):
        if not session.get("admin_user"):
            return jsonify({"success": False, "error": "Administrator login required"}), 401
        return route_handler(*args, **kwargs)
    return wrapped


def read_demo_mode(session_db):
    setting = session_db.query(SystemConfig).filter_by(config_key="demo_mode").first()
    if setting is None:
        setting = session_db.query(SystemConfig).filter_by(config_key="DEMO_MODE").first()
        if setting is not None:
            setting.config_key = "demo_mode"
            session_db.commit()
    return setting is not None and setting.config_value.lower() == "true"


@api.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    session_db = database.SessionLocal()
    try:
        valid_username = secrets.compare_digest(username, Config.ADMIN_USERNAME)
        valid_password = secrets.compare_digest(password, Config.ADMIN_PASSWORD)
        if not valid_username or not valid_password:
            return jsonify({"success": False, "error": "Invalid administrator credentials"}), 401
        session.clear()
        session["admin_user"] = {"username": Config.ADMIN_USERNAME, "full_name": "System Administrator"}
        return jsonify({"success": True, "user": session["admin_user"]})
    finally:
        session_db.close()


@api.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_user", None)
    return jsonify({"success": True})


@api.route("/admin/me", methods=["GET"])
def admin_me():
    if not session.get("admin_user"):
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": session["admin_user"]})


@api.route("/admin/settings/demo-mode", methods=["GET"])
@admin_required
def admin_demo_mode():
    session_db = database.SessionLocal()
    try:
        return jsonify({"success": True, "demo_mode": read_demo_mode(session_db)})
    finally:
        session_db.close()


@api.route("/admin/settings/demo-mode", methods=["POST"])
@admin_required
def update_demo_mode():
    data = request.get_json() or {}
    enabled = data.get("demo_mode")
    if not isinstance(enabled, bool):
        return jsonify({"success": False, "error": "demo_mode must be a boolean"}), 400
    session_db = database.SessionLocal()
    try:
        setting = session_db.query(SystemConfig).filter_by(config_key="demo_mode").first()
        if setting is None:
            setting = session_db.query(SystemConfig).filter_by(config_key="DEMO_MODE").first()
            if setting is not None:
                setting.config_key = "demo_mode"
        if not setting:
            setting = SystemConfig(config_key="demo_mode", description="Enable virtual simulation without IoT hardware")
            session_db.add(setting)
        setting.config_value = "true" if enabled else "false"
        session_db.commit()
        return jsonify({"success": True, "demo_mode": enabled})
    except Exception as error:
        session_db.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        session_db.close()

# ============================================================
# SYSTEM STATUS & GENERAL
# ============================================================

@api.route("/status", methods=["GET"])
def get_system_status():
    """System health check and live capacity counters."""
    session = database.SessionLocal()
    try:
        total_slots = session.query(ParkingSlot).count()
        available_slots = session.query(ParkingSlot).filter_by(status="AVAILABLE").count()
        occupied_slots = session.query(ParkingSlot).filter_by(status="OCCUPIED").count()
        reserved_slots = session.query(ParkingSlot).filter_by(status="RESERVED").count()

        return jsonify({
            "status": "online",
            "system_name": "SMARTPARK - Smart Parking Management System",
            "demo_mode": read_demo_mode(session),
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "capacity": {
                "total": total_slots,
                "available": available_slots,
                "occupied": occupied_slots,
                "reserved": reserved_slots,
                "occupancy_rate_percent": round((occupied_slots / total_slots * 100), 1) if total_slots > 0 else 0
            },
            "hardware_state": IoTGatewayService.hardware_state
        })
    finally:
        session.close()


# ============================================================
# PARKING SLOTS
# ============================================================

@api.route("/slots", methods=["GET"])
def get_all_slots():
    """Fetches all parking slots grouped by zone and floor level."""
    session = database.SessionLocal()
    try:
        slots = session.query(ParkingSlot).order_by(
            ParkingSlot.floor_level.asc(),
            ParkingSlot.zone.asc(),
            ParkingSlot.slot_number.asc()
        ).all()
        return jsonify({
            "success": True,
            "count": len(slots),
            "slots": [s.to_dict() for s in slots]
        })
    finally:
        session.close()


@api.route("/slots/reserve", methods=["POST"])
def reserve_slot():
    """Customer pre-booking / reservation endpoint."""
    session = database.SessionLocal()
    try:
        data = request.get_json() or {}
        vehicle_type = data.get("vehicle_type", "COMPACT").upper()
        vehicle_number = ANPRService.clean_plate_number(data.get("vehicle_number", ""))
        slot_number = data.get("slot_number")
        booking_time_value = data.get("booking_time")

        if booking_time_value:
            try:
                booking_time = datetime.datetime.fromisoformat(str(booking_time_value).replace("Z", "+00:00"))
                if booking_time.tzinfo:
                    booking_time = booking_time.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            except ValueError:
                return jsonify({"success": False, "error": "Booking time must be a valid date and time"}), 400
            if booking_time < datetime.datetime.utcnow():
                return jsonify({"success": False, "error": "Booking time must be in the future"}), 400
        else:
            booking_time = datetime.datetime.utcnow()

        if not vehicle_number:
            return jsonify({"success": False, "error": "Vehicle number is required for booking"}), 400

        # Check if vehicle already has an active ticket/booking
        existing = session.query(Ticket).filter(
            Ticket.vehicle_number == vehicle_number,
            Ticket.status.in_(["ACTIVE"])
        ).first()
        if existing:
            return jsonify({"success": False, "error": f"Vehicle {vehicle_number} already has an active session!"}), 409

        if slot_number:
            slot = session.query(ParkingSlot).filter_by(slot_number=slot_number, status="AVAILABLE").first()
            if not slot:
                return jsonify({"success": False, "error": f"Slot {slot_number} is no longer available"}), 400
        else:
            # Min-Heap allocation
            slot, _ = SlotAllocationService.find_and_assign_slot(session, vehicle_type, vehicle_number)
            if not slot:
                return jsonify({"success": False, "error": "No compatible slots available for reservation"}), 400

        slot.status = "RESERVED"
        slot.active_vehicle_number = vehicle_number

        ticket_code = f"SP-{uuid.uuid4().hex[:8].upper()}"
        ticket = Ticket(
            ticket_code=ticket_code,
            vehicle_number=vehicle_number,
            vehicle_type=vehicle_type,
            slot_id=slot.id,
            booking_time=booking_time,
            entry_time=datetime.datetime.utcnow(),
            base_rate=Config.RATES.get(vehicle_type, Config.RATES["COMPACT"])["base"],
            payment_status="PENDING",
            status="ACTIVE"
        )
        session.add(ticket)
        session.commit()

        # Navigation directions
        nav = facility_graph.get_shortest_path("ENTRY_GATE", slot.slot_number)

        return jsonify({
            "success": True,
            "message": f"Slot {slot.slot_number} reserved successfully!",
            "ticket": ticket.to_dict(),
            "navigation": nav
        })
    except Exception as e:
        session.rollback()
        logger.error(f"Error in reserve_slot: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


# ============================================================
# VEHICLE ENTRY & SIMULATION
# ============================================================

def _recognize_vehicle(data):
    image_data = data.get("image_snapshot")
    provided_plate = data.get("vehicle_number")
    if provided_plate and str(provided_plate).strip():
        return {
            "plate_number": ANPRService.clean_plate_number(str(provided_plate).strip()),
            "confidence": 99.0,
            "is_synthetic": True,
            "method": "MANUAL_INPUT"
        }, image_data
    return ANPRService.recognize_from_image(image_data), image_data


def _autonomous_entry(data, session):
    vehicle_type = str(data.get("vehicle_type", "COMPACT")).upper()
    trigger_source = data.get("trigger_source", "ESP32_CAM")
    anpr_result, image_data = _recognize_vehicle(data)
    vehicle_number = anpr_result["plate_number"]

    active_ticket = session.query(Ticket).filter(
        Ticket.vehicle_number == vehicle_number,
        Ticket.status == "ACTIVE"
    ).first()
    if active_ticket:
        if active_ticket.slot.status != "RESERVED":
            return None, {
                "success": False,
                "error": f"Vehicle {vehicle_number} is already inside at slot {active_ticket.slot.slot_number}.",
                "entry_authorized": False
            }, 409
        slot = active_ticket.slot
        slot.status = "OCCUPIED"
        slot.sensor_state = "DETECTED"
        slot.last_updated = datetime.datetime.utcnow()
        ticket = active_ticket
        entry_kind = "PRE_BOOKED"
    else:
        slot, nav_info = SlotAllocationService.find_and_assign_slot(
            session, vehicle_type, vehicle_number
        )
        if not slot:
            return None, {
                "success": False,
                "error": nav_info,
                "entry_authorized": False
            }, 409
        ticket_code = f"SP-{uuid.uuid4().hex[:8].upper()}"
        rates = Config.RATES.get(vehicle_type, Config.RATES["COMPACT"])
        ticket = Ticket(
            ticket_code=ticket_code,
            vehicle_number=vehicle_number,
            vehicle_type=vehicle_type,
            slot_id=slot.id,
            entry_time=datetime.datetime.utcnow(),
            base_rate=rates["base"],
            payment_status="PENDING",
            status="ACTIVE"
        )
        session.add(ticket)
        entry_kind = "WALK_IN"

    ticket.entry_time = datetime.datetime.utcnow()
    gate_log = GateLog(
        gate_type="ENTRY",
        vehicle_number=vehicle_number,
        anpr_confidence=anpr_result.get("confidence", 95.0),
        trigger_source=trigger_source,
        action_taken="GATE_OPENED",
        image_snapshot=image_data[:200] if isinstance(image_data, str) else None,
        notes=f"{entry_kind} entry; allocated slot {slot.slot_number}"
    )
    session.add(gate_log)
    session.flush()
    barrier_status = IoTGatewayService.trigger_entry_barrier("OPEN")
    IoTGatewayService.set_ir_sensor("IR_ENTRY", True)
    nav_info = facility_graph.get_shortest_path("ENTRY_GATE", slot.slot_number)
    session.commit()
    return ticket, {
        "success": True,
        "message": f"{entry_kind.replace('_', ' ').title()} entry authorized.",
        "entry_authorized": True,
        "entry_type": entry_kind,
        "anpr": anpr_result,
        "allocated_slot": slot.to_dict(),
        "ticket": ticket.to_dict(),
        "navigation": nav_info,
        "barrier_state": barrier_status
    }, 200


@api.route("/entry/automatic", methods=["POST"])
def automatic_entry():
    """Authorize pre-booked or walk-in entry without an admin browser."""
    token = request.headers.get("X-IoT-Token")
    if token != Config.IOT_AUTH_TOKEN:
        return jsonify({"success": False, "error": "Unauthorized IoT Device"}), 401
    session_db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        ticket, response, status = _autonomous_entry(data, session_db)
        return jsonify(response), status
    except Exception as error:
        session_db.rollback()
        logger.error("Automatic entry failed: %s", error)
        return jsonify({"success": False, "error": "Entry processing failed", "entry_authorized": False}), 500
    finally:
        session_db.close()

@api.route("/entry/simulate", methods=["POST"])
@admin_required
def simulate_entry():
    """
    Simulates or processes a vehicle arrival at the Entry Gate.
    1. Runs ANPR on uploaded image or synthetic plate generator.
    2. Min-Heap Priority Queue finds nearest optimal slot.
    3. Generates QR Ticket with secure token.
    4. Triggers Entry Barrier Servo (opens to 90 degrees).
    5. Returns driving navigation instructions.
    """
    session = database.SessionLocal()
    try:
        data = request.get_json() or {}
        image_data = data.get("image_snapshot")
        provided_plate = data.get("vehicle_number")
        vehicle_type = data.get("vehicle_type", "COMPACT").upper()
        trigger_source = data.get("trigger_source", "MANUAL_SIM")

        # 1. ANPR Processing
        if provided_plate and provided_plate.strip():
            anpr_result = {
                "plate_number": ANPRService.clean_plate_number(provided_plate.strip()),
                "confidence": 99.0,
                "is_synthetic": True,
                "method": "MANUAL_INPUT"
            }
        else:
            anpr_result = ANPRService.recognize_from_image(image_data)

        vehicle_number = anpr_result["plate_number"]

        # Check if already in parking lot
        active_ticket = session.query(Ticket).filter(
            Ticket.vehicle_number == vehicle_number,
            Ticket.status == "ACTIVE"
        ).first()

        if active_ticket:
            return jsonify({
                "success": False,
                "error": f"Vehicle {vehicle_number} is already recorded inside the facility at slot {active_ticket.slot.slot_number}!",
                "ticket": active_ticket.to_dict()
            }), 400

        # 2. Min-Heap Allocation
        slot, nav_info = SlotAllocationService.find_and_assign_slot(session, vehicle_type, vehicle_number)
        if not slot:
            return jsonify({
                "success": False,
                "error": nav_info  # error message returned
            }), 400

        # 3. Create Ticket
        ticket_code = f"SP-{uuid.uuid4().hex[:8].upper()}"
        rates = Config.RATES.get(vehicle_type, Config.RATES["COMPACT"])

        ticket = Ticket(
            ticket_code=ticket_code,
            vehicle_number=vehicle_number,
            vehicle_type=vehicle_type,
            slot_id=slot.id,
            entry_time=datetime.datetime.utcnow(),
            base_rate=rates["base"],
            payment_status="PENDING",
            status="ACTIVE"
        )
        session.add(ticket)

        # 4. Trigger Entry Barrier Servo
        barrier_status = IoTGatewayService.trigger_entry_barrier("OPEN")
        IoTGatewayService.set_ir_sensor("IR_ENTRY", True)

        # 5. Gate Log Audit Record
        gate_log = GateLog(
            gate_type="ENTRY",
            vehicle_number=vehicle_number,
            anpr_confidence=anpr_result.get("confidence", 95.0),
            trigger_source=trigger_source,
            action_taken="GATE_OPENED",
            image_snapshot=image_data[:200] if image_data else None,
            notes=f"Allocated Slot {slot.slot_number} ({slot.slot_type}) via Min-Heap"
        )
        session.add(gate_log)
        session.commit()

        return jsonify({
            "success": True,
            "message": f"Vehicle {vehicle_number} entered. Gate opened!",
            "anpr": anpr_result,
            "allocated_slot": slot.to_dict(),
            "ticket": ticket.to_dict(),
            "navigation": nav_info,
            "barrier_state": barrier_status
        })

    except Exception as e:
        session.rollback()
        logger.error(f"Error in simulate_entry: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


# ============================================================
# VEHICLE EXIT & BILLING
# ============================================================

@api.route("/tickets/<identifier>", methods=["GET"])
def get_ticket_details(identifier):
    """
    Looks up active ticket by ticket_code or vehicle_number,
    and dynamically calculates live billing.
    """
    session = database.SessionLocal()
    try:
        clean_id = identifier.strip().upper()
        ticket = session.query(Ticket).filter(
            (Ticket.ticket_code == clean_id) | (Ticket.vehicle_number == clean_id),
            Ticket.status == "ACTIVE"
        ).first()

        if not ticket:
            return jsonify({"success": False, "error": f"No active parking ticket found for: {identifier}"}), 404

        bill_info = BillingService.calculate_bill(
            ticket.entry_time,
            datetime.datetime.utcnow(),
            ticket.vehicle_type
        )

        response_data = ticket.to_dict()
        response_data["billing"] = bill_info
        return jsonify({"success": True, "ticket": response_data})
    finally:
        session.close()


@api.route("/payment/process", methods=["POST"])
def process_payment():
    """Simulates payment settlement (UPI QR, Credit Card, Fastag, Cash)."""
    session = database.SessionLocal()
    try:
        data = request.get_json() or {}
        ticket_code = data.get("ticket_code")
        payment_method = data.get("payment_method", "UPI_QR").upper()

        if not ticket_code:
            return jsonify({"success": False, "error": "ticket_code is required"}), 400

        ticket = session.query(Ticket).filter_by(ticket_code=ticket_code, status="ACTIVE").first()
        if not ticket:
            return jsonify({"success": False, "error": "Active ticket not found"}), 404

        # Compute final billing
        bill = BillingService.calculate_bill(ticket.entry_time, datetime.datetime.utcnow(), ticket.vehicle_type)

        txn_id = f"TXN_{payment_method}_{uuid.uuid4().hex[:10].upper()}"
        ticket.payment_status = "PAID"
        ticket.payment_method = payment_method
        ticket.transaction_id = txn_id
        ticket.total_amount = bill["grand_total"]
        ticket.duration_minutes = bill["duration_minutes"]
        session.commit()

        return jsonify({
            "success": True,
            "message": "Payment processed successfully!",
            "transaction_id": txn_id,
            "payment_status": "PAID",
            "exit_authorized": True,
            "receipt": {
                "ticket_code": ticket.ticket_code,
                "vehicle_number": ticket.vehicle_number,
                "amount_paid": bill["grand_total"],
                "payment_method": payment_method,
                "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }
        })
    except Exception as e:
        session.rollback()
        logger.error(f"Error in process_payment: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


def _find_active_ticket(session_db, identifier):
    clean_id = str(identifier or "").strip().upper()
    if not clean_id:
        return None
    return session_db.query(Ticket).filter(
        (Ticket.ticket_code == clean_id) | (Ticket.vehicle_number == clean_id),
        Ticket.status == "ACTIVE"
    ).first()


def _authorize_exit(session_db, identifier, trigger_source="ESP32_CAM"):
    ticket = _find_active_ticket(session_db, identifier)
    if not ticket:
        return {"success": False, "error": "No active parking session found", "exit_authorized": False}, 404
    if ticket.payment_status not in ("PAID", "EXEMPT"):
        bill = BillingService.calculate_bill(
            ticket.entry_time, datetime.datetime.utcnow(), ticket.vehicle_type
        )
        return {
            "success": False,
            "error": "Payment pending before exit",
            "payment_required": True,
            "exit_authorized": False,
            "ticket": ticket.to_dict(),
            "billing": bill
        }, 402
    if IoTGatewayService.pending_exit_vehicle == ticket.vehicle_number:
        return {
            "success": True,
            "message": "Exit already authorized; waiting for pass sensor",
            "exit_authorized": True,
            "awaiting_exit_confirmation": True,
            "ticket": ticket.to_dict(),
            "barrier_state": IoTGatewayService.hardware_state["exit_barrier"]
        }, 200

    IoTGatewayService.pending_exit_vehicle = ticket.vehicle_number
    barrier_status = IoTGatewayService.trigger_exit_barrier("OPEN")
    IoTGatewayService.set_ir_sensor("IR_EXIT", True)
    session_db.add(GateLog(
        gate_type="EXIT",
        vehicle_number=ticket.vehicle_number,
        trigger_source=trigger_source,
        action_taken="GATE_OPENED",
        notes="Payment verified; waiting for IR_EXIT_PASS confirmation"
    ))
    session_db.commit()
    return {
        "success": True,
        "message": "Exit authorized; waiting for vehicle pass confirmation",
        "exit_authorized": True,
        "awaiting_exit_confirmation": True,
        "ticket": ticket.to_dict(),
        "barrier_state": barrier_status
    }, 200


def _complete_exit(session_db, identifier=None, trigger_source="IR_SENSOR"):
    vehicle_number = identifier or IoTGatewayService.pending_exit_vehicle
    ticket = _find_active_ticket(session_db, vehicle_number)
    if not ticket:
        return {"success": True, "duplicate": True, "message": "Exit already completed"}, 200
    if ticket.payment_status not in ("PAID", "EXEMPT"):
        return {"success": False, "error": "Payment pending; exit remains closed"}, 402

    exit_time = datetime.datetime.utcnow()
    ticket.exit_time = exit_time
    ticket.status = "COMPLETED"
    ticket.duration_minutes = max(1, int((exit_time - ticket.entry_time).total_seconds() // 60))
    slot = ticket.slot
    if slot:
        slot.status = "AVAILABLE"
        slot.sensor_state = "EMPTY"
        slot.active_vehicle_number = None
        slot.last_updated = exit_time
    session_db.add(GateLog(
        gate_type="EXIT",
        vehicle_number=ticket.vehicle_number,
        trigger_source=trigger_source,
        action_taken="SIMULATED_PASS",
        notes=f"IR exit pass confirmed; released slot {slot.slot_number if slot else 'N/A'}"
    ))
    session_db.commit()
    IoTGatewayService.pending_exit_vehicle = None
    IoTGatewayService.set_ir_sensor("IR_EXIT", False)
    IoTGatewayService.trigger_exit_barrier("CLOSED")
    return {
        "success": True,
        "message": f"Exit confirmed for {ticket.vehicle_number}; slot released",
        "exit_confirmed": True,
        "ticket": ticket.to_dict()
    }, 200


@api.route("/exit/automatic", methods=["POST"])
def automatic_exit():
    """Authorize exit from a paid active session without an admin browser."""
    token = request.headers.get("X-IoT-Token")
    if token != Config.IOT_AUTH_TOKEN:
        return jsonify({"success": False, "error": "Unauthorized IoT Device"}), 401
    session_db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        response, status = _authorize_exit(
            session_db,
            data.get("ticket_code") or data.get("vehicle_number"),
            data.get("trigger_source", "ESP32_CAM")
        )
        return jsonify(response), status
    except Exception as error:
        session_db.rollback()
        logger.error("Automatic exit failed: %s", error)
        return jsonify({"success": False, "error": "Exit processing failed", "exit_authorized": False}), 500
    finally:
        session_db.close()


@api.route("/exit/simulate", methods=["POST"])
@admin_required
def simulate_exit():
    """
    Simulates or processes a vehicle departure at the Exit Gate.
    Verifies payment -> opens Exit Barrier Servo -> frees parking slot.
    """
    session = database.SessionLocal()
    try:
        data = request.get_json() or {}
        identifier = data.get("ticket_code") or data.get("vehicle_number")
        trigger_source = data.get("trigger_source", "MANUAL_SIM")
        bypass_payment = data.get("bypass_payment", False)

        if not identifier:
            return jsonify({"success": False, "error": "Ticket code or vehicle number is required for exit"}), 400

        clean_id = identifier.strip().upper()
        ticket = session.query(Ticket).filter(
            (Ticket.ticket_code == clean_id) | (Ticket.vehicle_number == clean_id),
            Ticket.status == "ACTIVE"
        ).first()

        if not ticket:
            return jsonify({"success": False, "error": f"No active vehicle or ticket found for: {identifier}"}), 404

        # Verify payment status
        if ticket.payment_status != "PAID" and not bypass_payment:
            bill = BillingService.calculate_bill(ticket.entry_time, datetime.datetime.utcnow(), ticket.vehicle_type)
            return jsonify({
                "success": False,
                "error": "Payment pending! Please settle the parking fee before exit.",
                "payment_required": True,
                "billing": bill,
                "ticket": ticket.to_dict()
            }), 402

        if bypass_payment and ticket.payment_status != "PAID":
            ticket.payment_status = "EXEMPT"
            session.commit()
        response, status = _authorize_exit(session, ticket.ticket_code, trigger_source)
        return jsonify(response), status

    except Exception as e:
        session.rollback()
        logger.error(f"Error in simulate_exit: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()


# ============================================================
# ANALYTICS & LOGS
# ============================================================

@api.route("/analytics", methods=["GET"])
@admin_required
def get_analytics():
    """Dashboard analytics: occupancy, revenue, zone utilization, and logs."""
    session = database.SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        today_start = datetime.datetime(now.year, now.month, now.day)

        total_slots = session.query(ParkingSlot).count()
        occupied_slots = session.query(ParkingSlot).filter_by(status="OCCUPIED").count()

        # Tickets today
        tickets_today = session.query(Ticket).filter(Ticket.created_at >= today_start).all()
        revenue_today = sum(float(t.total_amount) for t in tickets_today if t.payment_status == "PAID")

        # Zone stats
        slots = session.query(ParkingSlot).all()
        zones = {}
        for s in slots:
            if s.zone not in zones:
                zones[s.zone] = {"total": 0, "occupied": 0, "available": 0}
            zones[s.zone]["total"] += 1
            if s.status == "OCCUPIED":
                zones[s.zone]["occupied"] += 1
            elif s.status == "AVAILABLE":
                zones[s.zone]["available"] += 1

        # Recent gate logs
        recent_logs = session.query(GateLog).order_by(GateLog.timestamp.desc()).limit(15).all()

        return jsonify({
            "success": True,
            "metrics": {
                "total_slots": total_slots,
                "occupied_slots": occupied_slots,
                "available_slots": total_slots - occupied_slots,
                "occupancy_rate": round((occupied_slots / total_slots * 100), 1) if total_slots > 0 else 0,
                "total_vehicles_today": len(tickets_today),
                "revenue_today": round(revenue_today, 2)
            },
            "zones": zones,
            "recent_logs": [log.to_dict() for log in recent_logs]
        })
    finally:
        session.close()


# ============================================================
# IOT HARDWARE WEBHOOKS (ESP32-CAM & ARDUINO)
# ============================================================

@api.route("/iot/entry-camera", methods=["POST"])
def iot_entry_camera():
    """ESP32-CAM image upload endpoint at Entry Gate."""
    token = request.headers.get("X-IoT-Token")
    if token != Config.IOT_AUTH_TOKEN:
        return jsonify({"success": False, "error": "Unauthorized IoT Device"}), 401

    data = request.get_json(silent=True) or {}
    if not data and request.data:
        data["image_snapshot"] = request.data
    data.setdefault("trigger_source", "ESP32_CAM")
    session_db = database.SessionLocal()
    try:
        _, response, status = _autonomous_entry(data, session_db)
        return jsonify(response), status
    except Exception as error:
        session_db.rollback()
        logger.error("IoT entry failed: %s", error)
        return jsonify({"success": False, "error": "Entry processing failed", "entry_authorized": False}), 500
    finally:
        session_db.close()


@api.route("/iot/telemetry", methods=["POST"])
def iot_telemetry():
    """Receives sensor telemetry from ESP32 IR sensors."""
    token = request.headers.get("X-IoT-Token")
    if token != Config.IOT_AUTH_TOKEN:
        return jsonify({"success": False, "error": "Unauthorized IoT Device"}), 401

    session = database.SessionLocal()
    try:
        data = request.get_json() or {}
        device_id = data.get("device_id", "ESP32_DEFAULT")
        sensor_type = data.get("sensor_type")
        state_value = data.get("state_value", "EMPTY")
        allowed_sensors = {
            "IR_ENTRY", "IR_ENTRY_PASS", "IR_EXIT", "IR_EXIT_PASS",
            "SERVO_BARRIER_ENTRY", "SERVO_BARRIER_EXIT", "SLOT_IR"
        }
        if sensor_type not in allowed_sensors:
            return jsonify({"success": False, "error": "Unknown sensor type"}), 400

        telemetry = SensorTelemetry(
            device_id=device_id,
            sensor_type=sensor_type,
            state_value=str(state_value),
            voltage=float(data.get("voltage", 3.3))
        )
        session.add(telemetry)
        session.commit()

        # Update virtual hardware state
        is_detected = (str(state_value).upper() in ["1", "TRUE", "DETECTED"])
        IoTGatewayService.set_ir_sensor(sensor_type, is_detected)

        if sensor_type == "IR_EXIT_PASS" and is_detected:
            response, status = _complete_exit(
                session,
                data.get("vehicle_number"),
                "IR_SENSOR"
            )
            return jsonify(response), status

        if sensor_type == "IR_ENTRY_PASS" and is_detected:
            IoTGatewayService.trigger_entry_barrier("CLOSED")

        return jsonify({"success": True, "message": "Telemetry recorded"})
    finally:
        session.close()


@api.route("/iot/exit-camera", methods=["POST"])
def iot_exit_camera():
    """ESP32-CAM exit recognition and payment-gated barrier authorization."""
    token = request.headers.get("X-IoT-Token")
    if token != Config.IOT_AUTH_TOKEN:
        return jsonify({"success": False, "error": "Unauthorized IoT Device"}), 401
    data = request.get_json(silent=True) or {}
    if not data and request.data:
        data["image_snapshot"] = request.data
    if not data.get("vehicle_number") and data.get("image_snapshot"):
        anpr_result, _ = _recognize_vehicle(data)
        data["vehicle_number"] = anpr_result["plate_number"]
    session_db = database.SessionLocal()
    try:
        response, status = _authorize_exit(
            session_db,
            data.get("ticket_code") or data.get("vehicle_number"),
            "ESP32_CAM"
        )
        return jsonify(response), status
    except Exception as error:
        session_db.rollback()
        logger.error("IoT exit failed: %s", error)
        return jsonify({"success": False, "error": "Exit processing failed", "exit_authorized": False}), 500
    finally:
        session_db.close()


@api.route("/iot/barrier-status", methods=["GET"])
def iot_barrier_status():
    """Polling endpoint for ESP32 barrier servo motors."""
    token = request.headers.get("X-IoT-Token")
    if token != Config.IOT_AUTH_TOKEN:
        return jsonify({"success": False, "error": "Unauthorized IoT Device"}), 401
    return jsonify({
        "success": True,
        "barriers": {
            "entry": IoTGatewayService.hardware_state["entry_barrier"],
            "exit": IoTGatewayService.hardware_state["exit_barrier"]
        }
    })


# ============================================================
# RESET DEMO STATE
# ============================================================

@api.route("/admin/reset-demo", methods=["POST"])
@admin_required
def reset_demo():
    """Resets all parking slots to AVAILABLE and cancels active sessions for a clean demo."""
    session = database.SessionLocal()
    try:
        # Reset slots
        session.query(ParkingSlot).update({
            "status": "AVAILABLE",
            "sensor_state": "EMPTY",
            "active_vehicle_number": None,
            "last_updated": datetime.datetime.utcnow()
        })
        # Mark active tickets as COMPLETED
        session.query(Ticket).filter_by(status="ACTIVE").update({
            "status": "COMPLETED",
            "exit_time": datetime.datetime.utcnow()
        })
        # Reset hardware barrier
        IoTGatewayService.trigger_entry_barrier("CLOSED")
        IoTGatewayService.trigger_exit_barrier("CLOSED")

        session.commit()
        return jsonify({"success": True, "message": "Demo state reset! All 20 slots are now AVAILABLE."})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        session.close()
