"""
SMARTPARK - REST API Routes Module
Exposes endpoints for customer booking, billing, and protected operations.
"""

import uuid
import datetime
import io
import logging
import secrets
import re
from functools import wraps
from flask import Blueprint, request, jsonify, session, session as flask_session, send_file
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, func, or_
import qrcode
from config import Config
import database
from database import (
    ParkingSlot, Ticket, GateLog, SensorTelemetry, User, Vehicle, SystemConfig, LoginAttempt,
    get_db
)
from services import (
    SlotAllocationService, ANPRService, BillingService,
    IoTGatewayService, facility_graph, utc_naive, ist_naive_to_utc, IST
)

api = Blueprint("api", __name__, url_prefix="/api")
logger = logging.getLogger("smartpark.routes")
LOGIN_WINDOW = datetime.timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 5
REGISTRATION_RE = re.compile(r"^[A-Z]{2}-\d{2}-[A-Z]{2}-[A-Z0-9]{4}$")


def normalize_registration_number(raw_value):
    """Normalize Indian registration input to AA-00-AA-0000 or reject it."""
    raw = str(raw_value or "").strip().upper()
    compact = re.sub(r"[^A-Z0-9]", "", raw)
    if len(compact) == 10 and re.fullmatch(r"[A-Z]{2}\d{2}[A-Z]{2}[A-Z0-9]{4}", compact):
        raw = f"{compact[:2]}-{compact[2:4]}-{compact[4:6]}-{compact[6:]}"
    return raw if REGISTRATION_RE.fullmatch(raw) else None
ADMIN_LOGIN_FAILURE_LIMIT = 15


def _release_parking_slot(slot, released_at):
    """Release a slot, keeping retired non-ground inventory out of service."""
    slot.status = "AVAILABLE" if slot.floor_level == 0 else "MAINTENANCE"
    slot.sensor_state = "EMPTY"
    slot.active_vehicle_number = None
    slot.last_updated = released_at


def _login_throttle(session_db, username, remote_address, successful=False, failure_limit=LOGIN_FAILURE_LIMIT):
    """Atomically enforce a shared database-backed login failure window."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    key = {"username": username[:60], "remote_address": (remote_address or "unknown")[:64]}
    attempt = session_db.query(LoginAttempt).filter_by(**key).with_for_update().first()
    if not attempt:
        attempt = LoginAttempt(**key, window_started_at=now, failure_count=0)
        session_db.add(attempt)
        session_db.flush()
    elif now - attempt.window_started_at >= LOGIN_WINDOW:
        attempt.window_started_at = now
        attempt.failure_count = 0
    if successful:
        attempt.failure_count = 0
        session_db.commit()
        return True, 0
    if attempt.failure_count >= failure_limit:
        retry_after = max(1, int((LOGIN_WINDOW - (now - attempt.window_started_at)).total_seconds()))
        session_db.commit()
        return False, retry_after
    attempt.failure_count += 1
    session_db.commit()
    return True, 0


def admin_required(route_handler):
    """Require an authenticated administrator for admin-only API routes."""
    @wraps(route_handler)
    def wrapped(*args, **kwargs):
        admin = session.get("admin_user")
        if not admin or admin.get("role") != "ADMIN":
            if session.get("customer_user"):
                return jsonify({"success": False, "error": "Administrator access required"}), 403
            return jsonify({"success": False, "error": "Administrator login required"}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return jsonify({"success": False, "error": "CSRF validation failed"}), 403
        return route_handler(*args, **kwargs)
    return wrapped


def customer_csrf_required(route_handler):
    @wraps(route_handler)
    def wrapped(*args, **kwargs):
        if request.method == "OPTIONS":
            return route_handler(*args, **kwargs)
        if not session.get("customer_user"):
            return jsonify({"success": False, "error": "Customer login required"}), 401
        supplied = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            return jsonify({"success": False, "error": "CSRF validation failed"}), 403
        return route_handler(*args, **kwargs)
    return wrapped


def customer_required(route_handler):
    @wraps(route_handler)
    def wrapped(*args, **kwargs):
        if request.method == "OPTIONS":
            return route_handler(*args, **kwargs)
        if not session.get("customer_user"):
            return jsonify({"success": False, "error": "Customer login required"}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return jsonify({"success": False, "error": "CSRF validation failed"}), 403
        return route_handler(*args, **kwargs)
    return wrapped


def _customer_ticket_owned(ticket):
    customer = session.get("customer_user")
    return bool(customer and ticket and ticket.user_id == customer.get("id"))


@api.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    session_db = database.SessionLocal()
    try:
        allowed, retry_after = _login_throttle(
            session_db,
            f"admin:{username}",
            request.remote_addr,
            failure_limit=ADMIN_LOGIN_FAILURE_LIMIT
        )
        if not allowed:
            response = jsonify({"success": False, "error": "Too many login attempts. Try again later."})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        valid_username = secrets.compare_digest(username, Config.ADMIN_USERNAME)
        valid_password = secrets.compare_digest(password, Config.ADMIN_PASSWORD)
        if not valid_username or not valid_password:
            return jsonify({"success": False, "error": "Invalid administrator credentials"}), 401
        _login_throttle(
            session_db,
            f"admin:{username}",
            request.remote_addr,
            successful=True,
            failure_limit=ADMIN_LOGIN_FAILURE_LIMIT
        )
        session.clear()
        session["admin_user"] = {"username": Config.ADMIN_USERNAME, "full_name": "System Administrator", "role": "ADMIN"}
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"success": True, "user": session["admin_user"]})
    finally:
        session_db.close()


@api.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.pop("admin_user", None)
    return jsonify({"success": True})


@api.route("/admin/me", methods=["GET"])
def admin_me():
    if not session.get("admin_user"):
        if session.get("customer_user"):
            return jsonify({"authenticated": False, "error": "Administrator access required"}), 403
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": session["admin_user"]})


@api.route("/auth/register", methods=["POST"])
def customer_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower() or None
    if len(username) < 3 or len(username) > 60 or not username.replace("_", "").isalnum():
        return jsonify({"success": False, "error": "Username must be 3-60 letters, numbers, or underscores."}), 400
    if len(password) < 12:
        return jsonify({"success": False, "error": "Password must be at least 12 characters."}), 400
    if not full_name or len(full_name) > 100:
        return jsonify({"success": False, "error": "Full name is required."}), 400
    session_db = database.SessionLocal()
    try:
        if session_db.query(User).filter((User.username == username) | (User.email == email if email else User.username == username)).first():
            return jsonify({"success": False, "error": "That customer account already exists."}), 409
        user = User(username=username, password_hash=generate_password_hash(password), full_name=full_name, email=email, role="CUSTOMER")
        session_db.add(user)
        session_db.commit()
        session["customer_user"] = {"id": user.id, "username": user.username, "full_name": user.full_name, "role": "CUSTOMER"}
        session.pop("admin_user", None)
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"success": True, "user": session["customer_user"]}), 201
    except Exception:
        session_db.rollback()
        logger.exception("Customer registration failed")
        return jsonify({"success": False, "error": "Unable to create customer account."}), 500
    finally:
        session_db.close()


@api.route("/auth/login", methods=["POST"])
def customer_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    session_db = database.SessionLocal()
    try:
        allowed, retry_after = _login_throttle(session_db, username, request.remote_addr)
        if not allowed:
            response = jsonify({"success": False, "error": "Too many login attempts. Try again later."})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        user = session_db.query(User).filter_by(username=username, role="CUSTOMER").first()
        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({"success": False, "error": "Invalid customer credentials."}), 401
        _login_throttle(session_db, username, request.remote_addr, successful=True)
        session.clear()
        session["customer_user"] = {"id": user.id, "username": user.username, "full_name": user.full_name, "role": "CUSTOMER"}
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"success": True, "user": session["customer_user"]})
    finally:
        session_db.close()


@api.route("/auth/logout", methods=["POST"])
@customer_csrf_required
def customer_logout():
    session.clear()
    return jsonify({"success": True})


@api.route("/auth/me", methods=["GET"])
def customer_me():
    user = session.get("customer_user")
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user})


@api.route("/customer/profile", methods=["GET", "PUT"])
@customer_required
def customer_profile():
    """Read or update only the authenticated customer's safe profile fields."""
    session_db = database.SessionLocal()
    try:
        customer_id = flask_session["customer_user"].get("id")
        user = session_db.query(User).filter_by(id=customer_id, role="CUSTOMER").first()
        if not user:
            flask_session.clear()
            return jsonify({"success": False, "error": "Customer account is no longer available."}), 401
        if request.method == "GET":
            return jsonify({"success": True, "user": user.to_dict(), "vehicles": [v.to_dict() for v in user.vehicles]})

        data = request.get_json(silent=True) or {}
        raw_username = data.get("username", user.username)
        if not isinstance(raw_username, str):
            return jsonify({"success": False, "error": "Username is required."}), 400
        username = raw_username.strip().lower()
        if not 3 <= len(username) <= 60 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?", username):
            return jsonify({"success": False, "error": "Username must be 3-60 characters using letters, numbers, dot, underscore, or hyphen."}), 400
        duplicate = session_db.query(User).filter(User.username == username, User.id != user.id).first()
        if duplicate:
            return jsonify({"success": False, "error": "That username is already in use."}), 409
        user.username = username
        for field in ("full_name", "email", "phone"):
            if field in data:
                value = str(data[field] or "").strip() or None
                if field == "full_name" and (not value or len(value) > 100):
                    return jsonify({"success": False, "error": "Full name is required and must be 100 characters or fewer."}), 400
                setattr(user, field, value)
        session_db.commit()
        flask_session["customer_user"] = {"id": user.id, "username": user.username, "full_name": user.full_name, "role": "CUSTOMER"}
        return jsonify({"success": True, "user": user.to_dict()})
    except IntegrityError:
        session_db.rollback()
        return jsonify({"success": False, "error": "That username is already in use."}), 409
    finally:
        session_db.close()


def _current_customer(session_db):
    customer_id = flask_session.get("customer_user", {}).get("id")
    return session_db.query(User).filter_by(id=customer_id, role="CUSTOMER").first()


@api.route("/customer/vehicles", methods=["GET", "POST", "OPTIONS"])
@customer_required
def customer_vehicles():
    if request.method == "OPTIONS":
        return ("", 204)
    db = database.SessionLocal()
    try:
        user = _current_customer(db)
        if not user: return jsonify({"success": False, "error": "Customer account unavailable."}), 401
        if request.method == "GET": return jsonify({"success": True, "vehicles": [v.to_dict() for v in user.vehicles]})
        data = request.get_json(silent=True) or {}
        number = normalize_registration_number(data.get("vehicle_number") or data.get("registration_number", ""))
        kind = str(data.get("vehicle_type", "COMPACT")).upper()
        nickname = str(data.get("nickname", "")).strip()[:50] or None
        if not number or kind not in Config.RATES: return jsonify({"success": False, "error": "Invalid registration number. Use AA-00-AA-0000."}), 400
        if db.query(Vehicle).filter_by(user_id=user.id, vehicle_number=number).first(): return jsonify({"success": False, "error": "This vehicle is already saved."}), 409
        vehicle = Vehicle(user_id=user.id, vehicle_number=number, vehicle_type=kind, nickname=nickname)
        db.add(vehicle); db.commit()
        return jsonify({"success": True, "vehicle": vehicle.to_dict()}), 201
    except IntegrityError:
        db.rollback(); return jsonify({"success": False, "error": "This vehicle is already saved."}), 409
    finally: db.close()


@api.route("/customer/vehicles/<int:vehicle_id>", methods=["PUT", "DELETE", "OPTIONS"])
@customer_csrf_required
def customer_vehicle_detail(vehicle_id):
    if request.method == "OPTIONS":
        return ("", 204)
    db = database.SessionLocal()
    try:
        user_id = flask_session["customer_user"]["id"]
        vehicle = db.query(Vehicle).filter_by(id=vehicle_id, user_id=user_id).first()
        if not vehicle: return jsonify({"success": False, "error": "Vehicle not found."}), 404
        if request.method == "DELETE": db.delete(vehicle); db.commit(); return jsonify({"success": True})
        data = request.get_json(silent=True) or {}
        if "vehicle_number" in data or "registration_number" in data:
            vehicle.vehicle_number = normalize_registration_number(data.get("vehicle_number") or data.get("registration_number"))
        if "vehicle_type" in data and str(data["vehicle_type"]).upper() in Config.RATES: vehicle.vehicle_type = str(data["vehicle_type"]).upper()
        if "nickname" in data: vehicle.nickname = str(data["nickname"] or "").strip()[:50] or None
        if not vehicle.vehicle_number: return jsonify({"success": False, "error": "Invalid registration number. Use AA-00-AA-0000."}), 400
        db.commit(); return jsonify({"success": True, "vehicle": vehicle.to_dict()})
    except IntegrityError:
        db.rollback(); return jsonify({"success": False, "error": "This vehicle is already saved."}), 409
    finally: db.close()


@api.route("/customer/account", methods=["DELETE"])
@customer_csrf_required
def delete_customer_account():
    db = database.SessionLocal()
    try:
        user = _current_customer(db); password = str((request.get_json(silent=True) or {}).get("password", ""))
        if not user or not check_password_hash(user.password_hash, password): return jsonify({"success": False, "error": "Password confirmation failed."}), 403
        db.query(Ticket).filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)
        db.delete(user); db.commit(); flask_session.clear()
        return jsonify({"success": True, "message": "Customer account deleted. Historical parking records were preserved."})
    except Exception:
        db.rollback(); logger.exception("Customer account deletion failed"); return jsonify({"success": False, "error": "Unable to delete account."}), 500
    finally: db.close()


@api.route("/customer/history", methods=["GET"])
@customer_required
def customer_history():
    session_db = database.SessionLocal()
    try:
        tickets = session_db.query(Ticket).filter_by(user_id=flask_session["customer_user"]["id"]).order_by(Ticket.created_at.desc()).all()
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        active = [ticket for ticket in tickets if ticket.status == "ACTIVE" and ticket.entry_time is not None]
        upcoming = [ticket for ticket in tickets if ticket.status == "ACTIVE" and ticket.entry_time is None]
        payments = [ticket for ticket in tickets if ticket.payment_status in ("PAID", "EXEMPT")]
        return jsonify({
            "success": True,
            "bookings": [ticket.to_dict() for ticket in tickets],
            "active_sessions": [ticket.to_dict() for ticket in active],
            "upcoming_reservations": [ticket.to_dict() for ticket in upcoming],
            "active_qr": [{
                "ticket_code": ticket.ticket_code,
                "qr_payload": f"SMARTPARK:{ticket.ticket_code}",
                "expires_at": ticket.qr_expires_at.isoformat() if ticket.qr_expires_at else None,
                "vehicle_number": ticket.vehicle_number,
                "slot_number": ticket.slot.slot_number if ticket.slot else None,
                "entry_time": ticket.entry_time.isoformat() if ticket.entry_time else None,
                "payment_status": ticket.payment_status,
                "payment_scan_ready": bool(session_db.query(GateLog).filter(
                    GateLog.gate_type == "ENTRY",
                    GateLog.notes.like(f"Second QR scan accepted for ticket {ticket.ticket_code}%")
                ).first()),
            } for ticket in tickets if ticket.status == "ACTIVE" and not ticket.exit_time and
                not _qr_token_expired(ticket.qr_expires_at, now_utc)],
            "payments": [{"ticket_code": ticket.ticket_code, "vehicle_number": ticket.vehicle_number,
                          "amount": float(ticket.total_amount), "payment_status": ticket.payment_status,
                          "payment_method": ticket.payment_method, "transaction_id": ticket.transaction_id,
                          "exit_time": ticket.exit_time.isoformat() if ticket.exit_time else None} for ticket in payments]
        })
    finally:
        session_db.close()


@api.route("/customer/walk-in", methods=["POST"])
@customer_csrf_required
def customer_walk_in():
    """Create an owned walk-in QR ticket; entry still requires the admin scanner."""
    session_db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        vehicle_type = str(data.get("vehicle_type", "COMPACT")).upper()
        vehicle_number = normalize_registration_number(data.get("vehicle_number", ""))
        if not vehicle_number:
            return jsonify({"success": False, "error": "Invalid registration number. Use AA-00-AA-0000."}), 400
        if vehicle_type not in Config.RATES:
            return jsonify({"success": False, "error": "Unsupported vehicle type."}), 400
        existing = session_db.query(Ticket).filter_by(vehicle_number=vehicle_number, status="ACTIVE").first()
        if existing:
            return jsonify({"success": False, "error": "This vehicle already has an active parking record."}), 409
        slot, reason = SlotAllocationService.find_and_assign_slot(session_db, vehicle_type, vehicle_number)
        if not slot:
            session_db.rollback()
            return jsonify({"success": False, "error": reason or "No compatible slots are available."}), 409
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        slot.status = "RESERVED"
        slot.active_vehicle_number = vehicle_number
        ticket = Ticket(ticket_code=f"SP-{uuid.uuid4().hex.upper()}", vehicle_number=vehicle_number,
                        vehicle_type=vehicle_type, slot_id=slot.id,
                        user_id=flask_session["customer_user"]["id"], booking_time=None,
                        entry_mode="WALK_IN", entry_time=None,
                        qr_expires_at=now_utc + datetime.timedelta(hours=2),
                        base_rate=Config.RATES[vehicle_type]["base"], payment_status="PENDING", status="ACTIVE")
        session_db.add(ticket)
        session_db.commit()
        return jsonify({"success": True, "entry_mode": "WALK_IN", "message": "Walk-in pass created. Scan the QR at the entrance.", "ticket": ticket.to_dict()}), 201
    except Exception:
        session_db.rollback()
        logger.exception("Walk-in ticket creation failed")
        return jsonify({"success": False, "error": "Unable to create a walk-in pass."}), 500
    finally:
        session_db.close()


@api.route("/customer/bookings/<ticket_code>/cancel", methods=["POST"])
@customer_csrf_required
def cancel_customer_booking(ticket_code):
    """Cancel an owned, not-yet-entered reservation and release its slot."""
    session_db = database.SessionLocal()
    try:
        clean_code = normalize_token(ticket_code)
        customer_id = flask_session["customer_user"].get("id")
        ticket = session_db.query(Ticket).filter_by(ticket_code=clean_code).with_for_update().first()
        if not ticket or ticket.user_id != customer_id:
            return jsonify({"success": False, "error": "Booking not found."}), 404
        if ticket.status != "ACTIVE":
            return jsonify({"success": False, "error": "Only an active reservation can be cancelled."}), 409
        if ticket.entry_time is not None or ticket.exit_time is not None:
            return jsonify({"success": False, "error": "This reservation has already started and cannot be cancelled."}), 409
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        booking_time = utc_naive(ticket.booking_time)
        if not booking_time or booking_time <= now_utc:
            return jsonify({"success": False, "error": "This reservation is no longer eligible for cancellation."}), 409
        slot = session_db.query(ParkingSlot).filter_by(id=ticket.slot_id).with_for_update().first()
        ticket.status = "CANCELLED"
        ticket.qr_revoked_at = now_utc
        if slot and slot.active_vehicle_number == ticket.vehicle_number and slot.status == "RESERVED":
            _release_parking_slot(slot, now_utc)
        session_db.commit()
        return jsonify({"success": True, "message": "Reservation cancelled.", "ticket": ticket.to_dict()})
    except Exception:
        session_db.rollback()
        logger.exception("Customer reservation cancellation failed")
        return jsonify({"success": False, "error": "Unable to cancel reservation."}), 500
    finally:
        session_db.close()


def normalize_token(raw_token):
    """Clean up and extract token string from raw user input / QR scan."""
    token = str(raw_token or "").strip()
    if token.upper().startswith("SMARTPARK:"):
        token = token.split(":", 1)[1].strip()
    return token.upper()


def _qr_token_expired(expires_at, now):
    """Compare QR expiry timestamps consistently as naive UTC values."""
    expires_at_utc = utc_naive(expires_at)
    now_utc = utc_naive(now)
    return expires_at_utc is not None and expires_at_utc <= now_utc


def _qr_ticket_details(ticket, session_db):
    """Return scanner-safe booking/session data without exposing secrets."""
    slot = ticket.slot
    user = ticket.user
    if ticket.status == "COMPLETED":
        entry_status = "COMPLETED"
    elif slot and slot.status == "OCCUPIED":
        entry_status = "ENTERED"
    else:
        entry_status = "NOT_ENTERED"

    scan_count = 2 if session_db.query(GateLog).filter(
        GateLog.gate_type == "ENTRY",
        GateLog.notes.like(f"Second QR scan accepted for ticket {ticket.ticket_code}%")
    ).first() else (1 if ticket.entry_time else 0)
    return {
        "booking_id": ticket.ticket_code,
        "vehicle_number": ticket.vehicle_number,
        "vehicle_type": ticket.vehicle_type,
        "user": {
            "username": user.username,
            "full_name": user.full_name,
            "email": user.email
        } if user else None,
        "slot": slot.to_dict() if slot else None,
        "booking_time": ticket.booking_time.isoformat() if ticket.booking_time else None,
        "booking_status": ticket.status,
        "entry_status": entry_status,
        "entry_time": ticket.entry_time.isoformat() if entry_status != "NOT_ENTERED" and ticket.entry_time else None,
        "exit_time": ticket.exit_time.isoformat() if ticket.exit_time else None,
        "payment_status": ticket.payment_status,
        "payment_scan_ready": bool(ticket.entry_time and scan_count >= 2),
        "scan_count": scan_count,
        "ticket": ticket.to_dict()
    }


@api.route("/admin/qr/scan", methods=["POST"])
@admin_required
def scan_admin_qr():
    """Validate a booking/session token for the authenticated admin scanner."""
    session_db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        token = normalize_token(data.get("token") or data.get("qr_token"))
        if not token or len(token) > 64:
            return jsonify({"success": False, "error": "A valid booking QR token is required."}), 400

        ticket = session_db.query(Ticket).filter_by(ticket_code=token).first()
        if not ticket:
            return jsonify({"success": False, "error": "Invalid or expired QR token."}), 404

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if ticket.qr_revoked_at or _qr_token_expired(ticket.qr_expires_at, now):
            return jsonify({"success": False, "error": "This QR token has expired or been revoked."}), 410

        details = _qr_ticket_details(ticket, session_db)
        if ticket.status != "ACTIVE":
            return jsonify({
                "success": False,
                "error": f"This QR token belongs to a {ticket.status.lower()} booking and cannot be used.",
                "booking": details
            }), 409
        return jsonify({"success": True, "message": "QR token validated.", "booking": details})
    finally:
        session_db.close()


@api.route("/admin/qr/scan-entry", methods=["POST"])
@admin_required
def scan_admin_qr_entry():
    """Process a protected scanner event without trusting QR payload contents."""
    session_db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        token = normalize_token(data.get("token") or data.get("qr_token"))
        if not token or len(token) > 64:
            return jsonify({"success": False, "error": "A valid booking QR token is required."}), 400
        ticket = session_db.query(Ticket).filter_by(ticket_code=token).first()
        if not ticket:
            return jsonify({"success": False, "error": "Invalid or expired QR token."}), 404
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if ticket.qr_revoked_at or _qr_token_expired(ticket.qr_expires_at, now):
            return jsonify({"success": False, "error": "This QR token has expired or been revoked."}), 410
        if ticket.status != "ACTIVE":
            return jsonify({"success": False, "error": "This booking is no longer active."}), 409

        if not ticket.entry_time:
            _ticket, response, status = _autonomous_entry({
                "booking_id": token,
                "vehicle_number": ticket.vehicle_number,
                "trigger_source": "API"
            }, session_db, use_hardware=False)
            return jsonify(response), status

        details = _qr_ticket_details(ticket, session_db)
        session_db.add(GateLog(
            gate_type="ENTRY",
            vehicle_number=ticket.vehicle_number,
            trigger_source="API",
            action_taken="GATE_OPENED",
            notes=f"Second QR scan accepted for ticket {ticket.ticket_code} at slot {ticket.slot.slot_number if ticket.slot else 'N/A'}"
        ))
        session_db.commit()
        details = _qr_ticket_details(ticket, session_db)
        return jsonify({
            "success": True,
            "message": "Active parking session found.",
            "scan_stage": "EXIT_CHECK",
            "next_action": "PAYMENT_REQUIRED" if ticket.payment_status not in ("PAID", "EXEMPT") else "EXIT_AUTHORIZATION",
            "payment_url": f"/payment?ticket={ticket.ticket_code}" if ticket.payment_status not in ("PAID", "EXEMPT") else None,
            "booking": details
        })
    except Exception:
        session_db.rollback()
        logger.exception("Admin QR scan workflow failed")
        return jsonify({"success": False, "error": "QR scan processing failed."}), 500
    finally:
        session_db.close()


@api.route("/admin/slots/<int:slot_id>/clear", methods=["POST"])
@admin_required
def clear_admin_parking(slot_id):
    """Cancel one reservation or force-close one occupied session."""
    session_db = database.SessionLocal()
    try:
        active_tickets = session_db.query(Ticket).filter_by(
            slot_id=slot_id,
            status="ACTIVE"
        ).with_for_update().all()
        if len(active_tickets) > 1:
            return jsonify({"success": False, "error": "Multiple active sessions reference this slot; resolve the records before clearing it."}), 409

        slot = session_db.query(ParkingSlot).filter_by(id=slot_id).with_for_update().first()
        if not slot:
            return jsonify({"success": False, "error": "Parking slot not found."}), 404

        ticket = active_tickets[0] if active_tickets else None
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if slot.status == "RESERVED":
            if ticket and (
                ticket.entry_time is not None
                or ticket.exit_time is not None
                or ticket.payment_status != "PENDING"
                or slot.active_vehicle_number != ticket.vehicle_number
            ):
                return jsonify({"success": False, "error": "The reservation is no longer eligible for cancellation."}), 409
            if ticket:
                ticket.status = "CANCELLED"
                ticket.qr_revoked_at = now_utc
            _release_parking_slot(slot, now_utc)
            session_db.add(GateLog(
                gate_type="EXIT",
                vehicle_number=ticket.vehicle_number if ticket else None,
                trigger_source="API",
                action_taken="GATE_OPENED",
                notes=(
                    f"Admin cancelled reservation and released slot {slot.slot_number}"
                    if ticket else
                    f"Admin cleared stale reservation allocation from slot {slot.slot_number}"
                )
            ))
            session_db.commit()
            return jsonify({
                "success": True,
                "message": (
                    f"Reservation at {slot.slot_number} was cancelled and the slot released."
                    if ticket else
                    f"Stale reservation allocation at {slot.slot_number} was cleared."
                ),
                "slot": slot.to_dict(),
                "ticket": ticket.to_dict() if ticket else None,
                "payment_recorded": False
            })

        if slot.status != "OCCUPIED":
            return jsonify({"success": False, "error": "Only reserved or occupied slots can be cleared."}), 409
        if ticket and not ticket.entry_time:
            return jsonify({"success": False, "error": "This booking has not checked in and cannot be force-closed as a parking session."}), 409
        if ticket and ticket.payment_status != "PENDING":
            return jsonify({"success": False, "error": "This session is not in an unpaid state and cannot be force-closed with the unpaid action."}), 409
        if ticket and slot.active_vehicle_number != ticket.vehicle_number:
            return jsonify({"success": False, "error": "The occupied slot does not match its active session; resolve the mismatch before clearing it."}), 409

        exit_time = now_utc
        if ticket:
            bill = BillingService.calculate_bill(ticket.entry_time, exit_time, ticket.vehicle_type)
            ticket.exit_time = exit_time
            ticket.duration_minutes = bill["duration_minutes"]
            ticket.total_amount = bill["grand_total"]
            ticket.status = "COMPLETED"

        vehicle_number = slot.active_vehicle_number or (ticket.vehicle_number if ticket else None)
        _release_parking_slot(slot, exit_time)
        session_db.add(GateLog(
            gate_type="EXIT",
            vehicle_number=vehicle_number,
            trigger_source="API",
            action_taken="GATE_OPENED",
            notes=(
                f"Admin force-cleared slot {slot.slot_number}; unpaid final amount "
                f"{ticket.total_amount}" if ticket else
                f"Admin cleared stale occupancy from slot {slot.slot_number}; no active session found"
            )
        ))
        session_db.commit()

        return jsonify({
            "success": True,
            "message": (
                f"Parking at {slot.slot_number} was force-closed. No payment was recorded."
                if ticket else
                f"Stale occupancy at {slot.slot_number} was cleared."
            ),
            "slot": slot.to_dict(),
            "ticket": ticket.to_dict() if ticket else None,
            "payment_recorded": False
        })
    except Exception:
        session_db.rollback()
        logger.exception("Administrator parking clear failed")
        return jsonify({"success": False, "error": "Parking could not be cleared; no changes were committed."}), 500
    finally:
        session_db.close()


@api.route("/admin/slots/<int:slot_id>/edit", methods=["PATCH"])
@admin_required
def edit_admin_parking(slot_id):
    """Update the vehicle number and/or category on one occupied slot."""
    session_db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        allowed_fields = {"vehicle_number", "slot_type"}
        if not data or set(data) - allowed_fields:
            return jsonify({"success": False, "error": "Provide a vehicle number and/or supported slot type."}), 400

        active_tickets = session_db.query(Ticket).filter_by(
            slot_id=slot_id,
            status="ACTIVE"
        ).with_for_update().all()
        if len(active_tickets) > 1:
            return jsonify({"success": False, "error": "Multiple active sessions reference this slot; resolve the records before editing it."}), 409

        slot = session_db.query(ParkingSlot).filter_by(id=slot_id).with_for_update().first()
        if not slot:
            return jsonify({"success": False, "error": "Parking slot not found."}), 404
        if slot.status not in {"OCCUPIED", "RESERVED"}:
            return jsonify({"success": False, "error": "Only an occupied or reserved slot can be edited."}), 409

        ticket = active_tickets[0] if active_tickets else None
        if ticket and (
            slot.active_vehicle_number != ticket.vehicle_number
            or (slot.status == "OCCUPIED" and not ticket.entry_time)
            or (slot.status == "RESERVED" and (ticket.entry_time or ticket.exit_time))
        ):
            return jsonify({"success": False, "error": "The slot does not match its active parking record; resolve the mismatch before editing it."}), 409

        vehicle_number = None
        if "vehicle_number" in data:
            if not isinstance(data["vehicle_number"], str):
                return jsonify({"success": False, "error": "Invalid registration number. Use AA-00-AA-0000."}), 400
            vehicle_number = normalize_registration_number(data["vehicle_number"])
            if not vehicle_number:
                return jsonify({"success": False, "error": "Invalid registration number. Use AA-00-AA-0000."}), 400
        slot_type = None
        if "slot_type" in data:
            if not isinstance(data["slot_type"], str):
                return jsonify({"success": False, "error": "Choose a supported parking slot type."}), 400
            slot_type = data["slot_type"].upper()
            if slot_type not in {"TWO_WHEELER", "COMPACT", "SUV", "EV"}:
                return jsonify({"success": False, "error": "Choose a supported parking slot type."}), 400

        if vehicle_number is not None:
            slot.active_vehicle_number = vehicle_number
            if ticket:
                ticket.vehicle_number = vehicle_number
        if slot_type is not None:
            slot.slot_type = slot_type
        slot.last_updated = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        session_db.commit()

        return jsonify({
            "success": True,
            "message": f"Parking at {slot.slot_number} was updated.",
            "slot": slot.to_dict(),
            "ticket": ticket.to_dict() if ticket else None
        })
    except Exception:
        session_db.rollback()
        logger.exception("Administrator parking edit failed")
        return jsonify({"success": False, "error": "Parking could not be updated; no changes were committed."}), 500
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
        active_inventory = ParkingSlot.floor_level == 0
        total_slots = session.query(ParkingSlot).filter(active_inventory).count()
        available_slots = session.query(ParkingSlot).filter(
            active_inventory, ParkingSlot.status == "AVAILABLE"
        ).count()
        occupied_slots = session.query(ParkingSlot).filter(
            active_inventory, ParkingSlot.status == "OCCUPIED"
        ).count()
        reserved_slots = session.query(ParkingSlot).filter(
            active_inventory, ParkingSlot.status == "RESERVED"
        ).count()

        return jsonify({
            "status": "online",
            "system_name": "SMARTPARK - Smart Parking Management System",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
            "facility_location": {
                "latitude": float(Config.FACILITY_LATITUDE) if Config.FACILITY_LATITUDE else None,
                "longitude": float(Config.FACILITY_LONGITUDE) if Config.FACILITY_LONGITUDE else None,
                "directions_url": (
                    f"https://www.openstreetmap.org/?mlat={Config.FACILITY_LATITUDE}&mlon={Config.FACILITY_LONGITUDE}#map=18/{Config.FACILITY_LATITUDE}/{Config.FACILITY_LONGITUDE}"
                    if Config.FACILITY_LATITUDE and Config.FACILITY_LONGITUDE else None
                )
            },
            "capacity": {
                "total": total_slots,
                "available": available_slots,
                "occupied": occupied_slots,
                "reserved": reserved_slots,
                "occupancy_rate_percent": round((occupied_slots / total_slots * 100), 1) if total_slots > 0 else 0
            },
        })
    finally:
        session.close()


# ============================================================
# PARKING SLOTS
# ============================================================

@api.route("/slots", methods=["GET"])
def get_all_slots():
    """Fetch active ground-level parking inventory grouped by zone."""
    session = database.SessionLocal()
    try:
        slots = session.query(ParkingSlot).filter(
            ParkingSlot.floor_level == 0
        ).order_by(
            ParkingSlot.zone.asc(),
            ParkingSlot.slot_number.asc()
        ).all()
        serialized_slots = [slot.to_dict() for slot in slots]
        admin = flask_session.get("admin_user")
        if not admin or admin.get("role") != "ADMIN":
            for slot_data in serialized_slots:
                slot_data.pop("active_vehicle_number", None)
        return jsonify({
            "success": True,
            "count": len(slots),
            "slots": serialized_slots
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
        vehicle_number = normalize_registration_number(data.get("vehicle_number", ""))
        slot_number = data.get("slot_number")
        booking_time_value = data.get("booking_time")

        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if booking_time_value:
            try:
                booking_time = datetime.datetime.fromisoformat(str(booking_time_value).replace("Z", "+00:00"))
                booking_time = ist_naive_to_utc(booking_time)
            except ValueError:
                return jsonify({"success": False, "error": "Booking time must be a valid date and time"}), 400
            if booking_time < now_utc - datetime.timedelta(minutes=15):
                return jsonify({"success": False, "error": "Booking time must be in the future"}), 400
        else:
            booking_time = now_utc

        if not vehicle_number:
            return jsonify({"success": False, "error": "Invalid registration number. Use AA-00-AA-0000."}), 400

        # Check if vehicle already has an active ticket/booking
        existing = session.query(Ticket).filter(
            Ticket.vehicle_number == vehicle_number,
            Ticket.status.in_(["ACTIVE"])
        ).first()
        if existing:
            return jsonify({
                "success": False,
                "error": "This vehicle already has an active booking or parking session. Check My Bookings before trying again."
            }), 409

        if slot_number:
            slot = session.query(ParkingSlot).filter(
                ParkingSlot.slot_number == slot_number,
                ParkingSlot.status == "AVAILABLE",
                ParkingSlot.floor_level == 0
            ).with_for_update().first()
            if not slot:
                return jsonify({"success": False, "error": f"Slot {slot_number} is no longer available"}), 400
            compatible = {
                "TWO_WHEELER": {"TWO_WHEELER", "COMPACT"},
                "COMPACT": {"COMPACT", "SUV"},
                "SUV": {"SUV"},
                "EV": {"EV", "COMPACT"},
            }
            if slot.slot_type not in compatible.get(vehicle_type, {"COMPACT"}):
                return jsonify({"success": False, "error": f"Slot {slot_number} is not compatible with {vehicle_type}"}), 400
        else:
            # Min-Heap allocation
            slot, _ = SlotAllocationService.find_and_assign_slot(session, vehicle_type, vehicle_number)
            if not slot:
                return jsonify({"success": False, "error": "No compatible slots available for reservation"}), 400

        slot.status = "RESERVED"
        slot.active_vehicle_number = vehicle_number

        ticket_code = f"SP-{uuid.uuid4().hex.upper()}"
        ticket = Ticket(
            ticket_code=ticket_code,
            vehicle_number=vehicle_number,
            vehicle_type=vehicle_type,
            slot_id=slot.id,
            user_id=flask_session.get("customer_user", {}).get("id"),
            booking_time=booking_time,
            entry_time=None,
            qr_expires_at=booking_time + datetime.timedelta(hours=2),
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
# VEHICLE ENTRY: QR-authorized software workflow
# ============================================================

def _recognize_vehicle(data):
    image_data = data.get("image_snapshot")
    provided_plate = data.get("vehicle_number")
    if provided_plate and str(provided_plate).strip():
        return {
            "plate_number": normalize_registration_number(provided_plate),
            "confidence": 99.0,
            "is_synthetic": True,
            "method": "MANUAL_INPUT"
        }, image_data
    return ANPRService.recognize_from_image(image_data), image_data


def _autonomous_entry(data, session, use_hardware=True):
    vehicle_type = str(data.get("vehicle_type", "COMPACT")).upper()
    trigger_source = data.get("trigger_source", "API")
    booking_token = data.get("booking_id") or data.get("ticket_code") or data.get("qr_token")
    anpr_result, image_data = _recognize_vehicle(data)
    vehicle_number = anpr_result["plate_number"]

    if booking_token:
        clean_token = normalize_token(booking_token)
        # Lock the ticket first, then its slot. Cancellation uses the same order.
        active_ticket = session.query(Ticket).filter_by(
            ticket_code=clean_token,
            status="ACTIVE"
        ).with_for_update().first()
        if not active_ticket:
            return None, {
                "success": False,
                "error": "Invalid, expired, or completed booking token.",
                "entry_authorized": False
            }, 404
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if active_ticket.qr_revoked_at or _qr_token_expired(active_ticket.qr_expires_at, now_utc):
            return None, {"success": False, "error": "This booking QR token has expired or been revoked.", "entry_authorized": False}, 410
        if active_ticket.qr_used_at:
            return None, {"success": False, "error": "This booking QR token has already been used.", "entry_authorized": False}, 409
        if active_ticket.vehicle_number != vehicle_number and data.get("vehicle_number"):
            return None, {
                "success": False,
                "error": "Vehicle number does not match this booking.",
                "entry_authorized": False
            }, 409
        booking_time = utc_naive(active_ticket.booking_time)
        active_ticket_slot = session.query(ParkingSlot).filter_by(id=active_ticket.slot_id).with_for_update().first()
        if not active_ticket_slot or active_ticket_slot.status not in ("RESERVED", "AVAILABLE"):
            return None, {
                "success": False,
                "error": "This booking has already entered or its reserved slot is unavailable.",
                "entry_authorized": False
            }, 409
        vehicle_number = active_ticket.vehicle_number
        anpr_result["plate_number"] = vehicle_number
    else:
        active_ticket = session.query(Ticket).filter(
            Ticket.vehicle_number == vehicle_number,
            Ticket.status == "ACTIVE"
        ).first()

    if active_ticket:
        if active_ticket.slot.status == "OCCUPIED":
            return None, {
                "success": False,
                "error": f"Vehicle {vehicle_number} is already inside at slot {active_ticket.slot.slot_number}.",
                "entry_authorized": False
            }, 409
        slot = active_ticket.slot
        slot.status = "OCCUPIED"
        slot.sensor_state = "DETECTED"
        slot.last_updated = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        ticket = active_ticket
        entry_kind = "WALK_IN" if active_ticket.entry_mode == "WALK_IN" else "RESERVATION"
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
        ticket_code = f"SP-{uuid.uuid4().hex.upper()}"
        rates = Config.RATES.get(vehicle_type, Config.RATES["COMPACT"])
        ticket = Ticket(
            ticket_code=ticket_code,
            vehicle_number=vehicle_number,
            vehicle_type=vehicle_type,
            slot_id=slot.id,
            entry_time=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
            base_rate=rates["base"],
            payment_status="PENDING",
            status="ACTIVE", entry_mode="WALK_IN"
        )
        session.add(ticket)
        entry_kind = "WALK_IN"

    ticket.entry_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if booking_token:
        ticket.qr_used_at = ticket.entry_time
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
        "entry_recorded": True
    }, 200


@api.route("/entry/automatic", methods=["POST"])
def automatic_entry():
    """Authorize pre-booked or walk-in entry without an admin browser."""
    return jsonify({"success": False, "error": "Hardware entry integration is retired; use the protected admin QR scanner."}), 410
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
    return jsonify({"success": False, "error": "This endpoint has been retired; use the protected QR scanner."}), 410


# ============================================================
# VEHICLE EXIT & BILLING
# ============================================================

@api.route("/tickets/<identifier>", methods=["GET"])
def get_ticket_details(identifier):
    """
    Looks up an authorized ticket and returns its estimate or final bill.
    """
    session = database.SessionLocal()
    try:
        clean_id = normalize_token(identifier)
        if not flask_session.get("customer_user") and not clean_id.startswith("SP-"):
            return jsonify({"success": False, "error": "Use the booking ticket code to access guest parking details."}), 404
        allowed, retry_after = _login_throttle(session, f"ticket:{clean_id}", request.remote_addr)
        if not allowed:
            response = jsonify({"success": False, "error": "Too many ticket lookup attempts. Try again later."})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        ticket_query = session.query(Ticket).filter(
            (Ticket.ticket_code == clean_id) | (Ticket.vehicle_number == clean_id),
            Ticket.status.in_(("ACTIVE", "COMPLETED"))
        )
        customer = flask_session.get("customer_user")
        admin = flask_session.get("admin_user")
        if customer:
            ticket_query = ticket_query.filter(Ticket.user_id == customer.get("id"))
        elif not admin:
            ticket_query = ticket_query.filter(Ticket.user_id.is_(None))
        ticket = ticket_query.order_by(Ticket.created_at.desc()).first()

        if not ticket:
            session.commit()
            return jsonify({"success": False, "error": f"No active parking ticket found for: {identifier}"}), 404
        _login_throttle(session, f"ticket:{clean_id}", request.remote_addr, successful=True)

        bill_info = None
        if ticket.entry_time and ticket.exit_time:
            bill_info = BillingService.calculate_bill(ticket.entry_time, ticket.exit_time, ticket.vehicle_type)
            bill_info["is_final"] = True
        elif ticket.entry_time:
            bill_info = BillingService.calculate_bill(ticket.entry_time, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None), ticket.vehicle_type)
            bill_info["is_estimate"] = True
        elif ticket.booking_time:
            # Upcoming reservation estimates are informational only.
            estimate_start = utc_naive(ticket.booking_time)
            estimate_end = utc_naive(ticket.qr_expires_at) or (estimate_start + datetime.timedelta(hours=2))
            bill_info = BillingService.calculate_bill(estimate_start, estimate_end, ticket.vehicle_type)
            bill_info.update({
                "is_estimate": True,
                "expected_duration_minutes": bill_info["duration_minutes"],
            })

        response_data = ticket.to_dict()
        response_data["billing"] = bill_info
        response_data["reservation_status"] = (
            "COMPLETED" if ticket.status == "COMPLETED"
            else "UPCOMING / NOT_CHECKED_IN" if not ticket.entry_time
            else "ACTIVE / CHECKED_IN"
        )
        response_data["payment_scan_ready"] = bool(
            ticket.entry_time and session.query(GateLog).filter(
                GateLog.gate_type == "ENTRY",
                GateLog.notes.like(f"Second QR scan accepted for ticket {ticket.ticket_code}%")
            ).first()
        )
        response_data["scheduled_end_time"] = ticket.qr_expires_at.isoformat() if ticket.qr_expires_at else None
        return jsonify({"success": True, "ticket": response_data})
    finally:
        session.close()


@api.route("/tickets/<ticket_code>/qr", methods=["GET"])
def get_ticket_qr(ticket_code):
    """Generate an authorized parking QR locally without exposing the token to a third party."""
    session_db = database.SessionLocal()
    try:
        clean_code = normalize_token(ticket_code)
        customer = flask_session.get("customer_user")
        admin = flask_session.get("admin_user")
        ticket_query = session_db.query(Ticket).filter_by(ticket_code=clean_code, status="ACTIVE")
        if customer:
            ticket_query = ticket_query.filter(Ticket.user_id == customer.get("id"))
        elif not admin:
            ticket_query = ticket_query.filter(Ticket.user_id.is_(None))
        ticket = ticket_query.first()
        if not ticket:
            return jsonify({"success": False, "error": "Active ticket not found"}), 404
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if ticket.qr_revoked_at or _qr_token_expired(ticket.qr_expires_at, now_utc):
            return jsonify({"success": False, "error": "This QR token has expired or been revoked."}), 410

        qr_code = qrcode.QRCode(
            box_size=6,
            border=4,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
        )
        qr_code.add_data(f"SMARTPARK:{ticket.ticket_code}")
        qr_code.make(fit=True)
        image = qr_code.make_image(fill_color="black", back_color="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        response = send_file(output, mimetype="image/png", max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    finally:
        session_db.close()


@api.route("/payment/process", methods=["POST"])
def process_payment():
    """Records a clearly labelled development payment and completes exit."""
    session = database.SessionLocal()
    try:
        customer = flask_session.get("customer_user")
        admin = flask_session.get("admin_user")
        if customer:
            supplied_csrf = request.headers.get("X-CSRF-Token", "")
            expected_csrf = flask_session.get("csrf_token", "")
            if not expected_csrf or not secrets.compare_digest(supplied_csrf, expected_csrf):
                return jsonify({"success": False, "error": "CSRF validation failed"}), 403
        elif admin:
            supplied_csrf = request.headers.get("X-CSRF-Token", "")
            expected_csrf = flask_session.get("csrf_token", "")
            if not expected_csrf or not secrets.compare_digest(supplied_csrf, expected_csrf):
                return jsonify({"success": False, "error": "CSRF validation failed"}), 403

        data = request.get_json() or {}
        raw_code = data.get("ticket_code")
        payment_method = data.get("payment_method", "UPI_QR").upper()
        allowed_methods = {"CASH", "UPI_QR", "CREDIT_CARD", "FASTAG_SIM"}

        if not raw_code:
            return jsonify({"success": False, "error": "ticket_code is required"}), 400
        if payment_method not in allowed_methods:
            return jsonify({"success": False, "error": "Unsupported demo payment method"}), 400

        ticket_code = normalize_token(raw_code)
        allowed, retry_after = _login_throttle(session, f"payment:{ticket_code}", request.remote_addr)
        if not allowed:
            response = jsonify({"success": False, "error": "Too many payment attempts. Try again later."})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        ticket = session.query(Ticket).filter_by(ticket_code=ticket_code).first()
        if not ticket:
            session.commit()
            return jsonify({"success": False, "error": "Active ticket not found"}), 404
        if ticket.user_id is not None and not admin and (
            not customer or ticket.user_id != customer.get("id")
        ):
            return jsonify({"success": False, "error": "This ticket does not belong to the signed-in customer."}), 404
        _login_throttle(session, f"payment:{ticket_code}", request.remote_addr, successful=True)
        ticket = session.query(Ticket).filter_by(ticket_code=ticket_code).with_for_update().populate_existing().first()
        if not ticket:
            return jsonify({"success": False, "error": "Active ticket not found"}), 404
        if ticket.user_id is not None and not admin and (
            not customer or ticket.user_id != customer.get("id")
        ):
            return jsonify({"success": False, "error": "This ticket does not belong to the signed-in customer."}), 404
        if ticket.payment_status == "PAID" and ticket.status == "COMPLETED":
            return jsonify({
                "success": True,
                "message": "Payment was already recorded.",
                "transaction_id": ticket.transaction_id,
                "payment_status": "PAID",
                "exit_authorized": True,
                "duplicate": True,
                "receipt": {
                    "ticket_code": ticket.ticket_code,
                    "vehicle_number": ticket.vehicle_number,
                    "amount_paid": float(ticket.total_amount),
                    "payment_method": ticket.payment_method,
                    "timestamp": ticket.exit_time.isoformat() if ticket.exit_time else None,
                    "entry_time": ticket.entry_time.isoformat() if ticket.entry_time else None,
                    "exit_time": ticket.exit_time.isoformat() if ticket.exit_time else None,
                    "duration_minutes": ticket.duration_minutes,
                }
            })
        if ticket.status != "ACTIVE":
            return jsonify({"success": False, "error": "This ticket is no longer an active parking session."}), 409
        if not ticket.entry_time:
            return jsonify({"success": False, "error": "The booking must be checked in before payment."}), 409

        second_scan = session.query(GateLog).filter(
            GateLog.gate_type == "ENTRY",
            GateLog.notes.like(f"Second QR scan accepted for ticket {ticket.ticket_code}%")
        ).first()
        scan_count = 2 if second_scan else (1 if ticket.entry_time else 0)
        if scan_count < 2:
            return jsonify({"success": False, "error": "Scan the QR ticket a second time at the exit scanner before payment."}), 409

        if ticket.payment_status == "PAID":
            return jsonify({"success": False, "error": "Payment is recorded but the parking session is not completed."}), 409

        slot = session.query(ParkingSlot).filter_by(id=ticket.slot_id).with_for_update().first()
        if not slot or slot.status != "OCCUPIED" or slot.active_vehicle_number != ticket.vehicle_number:
            return jsonify({"success": False, "error": "The active parking slot could not be verified; payment was not processed."}), 409

        exit_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        bill = BillingService.calculate_bill(ticket.entry_time, exit_time, ticket.vehicle_type)

        if payment_method == "CASH":
            ticket.payment_method = "CASH"
            ticket.total_amount = bill["grand_total"]
            ticket.duration_minutes = bill["duration_minutes"]
            session.commit()
            return jsonify({
                "success": True,
                "cash_pending": True,
                "message": "Cash payment requested. An administrator must confirm cash received before exit.",
                "payment_status": "PENDING",
                "amount_due": bill["grand_total"],
                "ticket_code": ticket.ticket_code
            }), 202

        txn_id = f"TXN_{payment_method}_{uuid.uuid4().hex[:10].upper()}"
        ticket.payment_status = "PAID"
        ticket.payment_method = payment_method
        ticket.transaction_id = txn_id
        ticket.total_amount = bill["grand_total"]
        ticket.duration_minutes = bill["duration_minutes"]
        ticket.exit_time = exit_time
        ticket.status = "COMPLETED"
        _release_parking_slot(slot, exit_time)
        session.add(GateLog(
            gate_type="EXIT",
            vehicle_number=ticket.vehicle_number,
            trigger_source="API",
            action_taken="GATE_OPENED",
            notes=f"Development payment exit confirmed; released slot {slot.slot_number}"
        ))
        session.commit()

        return jsonify({
            "success": True,
            "message": "Development payment recorded and software exit completed.",
            "transaction_id": txn_id,
            "payment_status": "PAID",
            "exit_authorized": True,
            "exit_confirmed": True,
            "receipt": {
                "ticket_code": ticket.ticket_code,
                "vehicle_number": ticket.vehicle_number,
                "amount_paid": bill["grand_total"],
                "payment_method": payment_method,
                "timestamp": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_time": ticket.entry_time.isoformat(),
                "exit_time": exit_time.isoformat(),
                "duration_minutes": bill["duration_minutes"],
            },
            "exit": {"success": True, "exit_confirmed": True, "ticket": ticket.to_dict()}
        })
    except Exception as e:
        session.rollback()
        logger.exception("Development payment and exit failed")
        return jsonify({"success": False, "error": "Payment could not be completed; the parking session remains active."}), 500
    finally:
        session.close()


@api.route("/admin/payment/release-cash", methods=["POST"])
@admin_required
def release_cash_payment():
    """Admin-only confirmation for cash received at the exit desk."""
    db = database.SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        code = normalize_token(data.get("ticket_code"))
        ticket = db.query(Ticket).filter_by(ticket_code=code).with_for_update().first()
        if not ticket or ticket.status != "ACTIVE":
            return jsonify({"success": False, "error": "Active ticket not found."}), 404
        if ticket.payment_method != "CASH" or ticket.payment_status != "PENDING":
            return jsonify({"success": False, "error": "This ticket is not awaiting cash confirmation."}), 409
        if not ticket.entry_time:
            return jsonify({"success": False, "error": "The vehicle has not been checked in."}), 409
        slot = db.query(ParkingSlot).filter_by(id=ticket.slot_id).with_for_update().first()
        if not slot or slot.status != "OCCUPIED" or slot.active_vehicle_number != ticket.vehicle_number:
            return jsonify({"success": False, "error": "The occupied slot could not be verified."}), 409
        exit_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        bill = BillingService.calculate_bill(ticket.entry_time, exit_time, ticket.vehicle_type)
        ticket.payment_status = "PAID"
        ticket.transaction_id = f"CASH_{uuid.uuid4().hex[:10].upper()}"
        ticket.total_amount = bill["grand_total"]
        ticket.duration_minutes = bill["duration_minutes"]
        ticket.exit_time = exit_time
        ticket.status = "COMPLETED"
        _release_parking_slot(slot, exit_time)
        db.add(GateLog(gate_type="EXIT", vehicle_number=ticket.vehicle_number, trigger_source="API", action_taken="GATE_OPENED", notes=f"Admin confirmed cash and released slot {slot.slot_number}"))
        db.commit()
        return jsonify({"success": True, "message": "Cash confirmed; exit completed and slot released.", "ticket": ticket.to_dict()})
    except Exception:
        db.rollback()
        logger.exception("Cash release failed")
        return jsonify({"success": False, "error": "Cash release failed; the session remains active."}), 500
    finally:
        db.close()


@api.route("/admin/payment/cash-pending", methods=["GET"])
@admin_required
def pending_cash_payments():
    """List active sessions for which a customer requested cash payment."""
    db = database.SessionLocal()
    try:
        tickets = db.query(Ticket).filter(
            Ticket.status == "ACTIVE",
            Ticket.entry_time.isnot(None),
            Ticket.payment_method == "CASH",
            Ticket.payment_status == "PENDING"
        ).order_by(Ticket.entry_time.asc()).all()
        return jsonify({
            "success": True,
            "payments": [{
                "ticket_code": ticket.ticket_code,
                "vehicle_number": ticket.vehicle_number,
                "vehicle_type": ticket.vehicle_type,
                "slot_number": ticket.slot.slot_number if ticket.slot else None,
                "amount_due": float(ticket.total_amount),
                "entry_time": ticket.entry_time.isoformat(),
            } for ticket in tickets]
        })
    finally:
        db.close()


def _find_active_ticket(session_db, identifier):
    clean_id = normalize_token(identifier)
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
    if not ticket.entry_time:
        return {"success": False, "error": "The vehicle has not entered the facility", "exit_authorized": False}, 409
    if ticket.payment_status not in ("PAID", "EXEMPT"):
        bill = BillingService.calculate_bill(
            ticket.entry_time, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None), ticket.vehicle_type
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


def _complete_exit(session_db, identifier=None, trigger_source="API", use_hardware=False):
    clean_identifier = normalize_token(identifier)
    ticket = session_db.query(Ticket).filter(
        (Ticket.ticket_code == clean_identifier) | (Ticket.vehicle_number == clean_identifier),
        Ticket.status == "ACTIVE"
    ).with_for_update().first()
    if not ticket:
        return {"success": True, "duplicate": True, "message": "Exit already completed"}, 200
    if not ticket.entry_time:
        return {"success": False, "error": "The vehicle has not entered the facility"}, 409
    if ticket.payment_status not in ("PAID", "EXEMPT"):
        return {"success": False, "error": "Payment pending; exit remains closed"}, 402

    exit_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ticket.exit_time = exit_time
    ticket.status = "COMPLETED"
    ticket.duration_minutes = max(1, int((exit_time - utc_naive(ticket.entry_time)).total_seconds() // 60))
    slot = session_db.query(ParkingSlot).filter_by(id=ticket.slot_id).with_for_update().first()
    if slot:
        _release_parking_slot(slot, exit_time)
    session_db.add(GateLog(
        gate_type="EXIT",
        vehicle_number=ticket.vehicle_number,
        trigger_source=trigger_source,
        action_taken="GATE_OPENED",
        notes=f"QR payment exit confirmed; released slot {slot.slot_number if slot else 'N/A'}"
    ))
    session_db.commit()
    if use_hardware:
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
    return jsonify({"success": False, "error": "Hardware exit integration is retired; use the protected admin QR scanner."}), 410
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
    return jsonify({"success": False, "error": "This endpoint has been retired; use the QR payment and exit workflow."}), 410


# ============================================================
# ANALYTICS & LOGS
# ============================================================

@api.route("/admin/records/<record_type>", methods=["GET"])
@admin_required
def get_admin_records(record_type):
    """Return paginated administrator views over persisted customer and ticket data."""
    allowed_types = {"users", "reservations", "payments", "qr"}
    if record_type not in allowed_types:
        return jsonify({"success": False, "error": "Unknown record type."}), 404

    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(100, max(1, int(request.args.get("page_size", "25"))))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Page and page_size must be integers."}), 400

    search = str(request.args.get("q", "")).strip()[:100]
    status = str(request.args.get("status", "")).strip().upper()[:30]
    session_db = database.SessionLocal()
    try:
        if record_type == "users":
            query = session_db.query(User).filter(User.role == "CUSTOMER")
            if search:
                term = f"%{search}%"
                query = query.filter(or_(
                    User.username.ilike(term), User.full_name.ilike(term),
                    User.email.ilike(term), User.phone.ilike(term)
                ))
            total = query.count()
            rows = query.order_by(User.created_at.desc(), User.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size).all()
            records = []
            for user in rows:
                ticket_count = session_db.query(func.count(Ticket.id)).filter(
                    Ticket.user_id == user.id
                ).scalar()
                records.append({
                    "id": user.id,
                    "username": user.username,
                    "full_name": user.full_name,
                    "email": user.email,
                    "phone": user.phone,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "reservation_count": int(ticket_count or 0),
                })
        elif record_type == "payments":
            query = session_db.query(
                Ticket.id,
                Ticket.ticket_code,
                Ticket.vehicle_number,
                Ticket.total_amount,
                Ticket.payment_method,
                Ticket.payment_status,
                Ticket.transaction_id,
                Ticket.status,
            )
            if search:
                term = f"%{search}%"
                query = query.filter(or_(
                    Ticket.ticket_code.ilike(term),
                    Ticket.vehicle_number.ilike(term),
                    Ticket.transaction_id.ilike(term),
                ))
            if status and status not in {"PENDING", "PAID", "EXEMPT", "FAILED"}:
                return jsonify({"success": False, "error": "Invalid payment status."}), 400
            if status:
                query = query.filter(Ticket.payment_status == status)

            total = query.count()
            rows = query.order_by(Ticket.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size).all()
            records = [{
                "id": row.id,
                "ticket_code": row.ticket_code,
                "vehicle_number": row.vehicle_number,
                "total_amount": float(row.total_amount or 0),
                "payment_method": row.payment_method,
                "payment_status": row.payment_status,
                "transaction_id": row.transaction_id,
                "status": row.status,
            } for row in rows]
        else:
            now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            query = session_db.query(Ticket, User.username, ParkingSlot.slot_number).outerjoin(
                User, Ticket.user_id == User.id
            ).outerjoin(ParkingSlot, Ticket.slot_id == ParkingSlot.id)
            if search:
                term = f"%{search}%"
                query = query.filter(or_(
                    Ticket.ticket_code.ilike(term),
                    Ticket.vehicle_number.ilike(term),
                    Ticket.transaction_id.ilike(term),
                    User.username.ilike(term)
                ))
            if record_type == "reservations":
                if status and status not in {"ACTIVE", "COMPLETED", "CANCELLED"}:
                    return jsonify({"success": False, "error": "Invalid reservation status."}), 400
                if status:
                    query = query.filter(Ticket.status == status)
            else:
                if status and status not in {"ACTIVE", "EXPIRED", "REVOKED", "CLOSED"}:
                    return jsonify({"success": False, "error": "Invalid QR status."}), 400
                if status == "ACTIVE":
                    query = query.filter(
                        Ticket.status == "ACTIVE", Ticket.qr_revoked_at.is_(None),
                        or_(Ticket.qr_expires_at.is_(None), Ticket.qr_expires_at > now_utc)
                    )
                elif status == "EXPIRED":
                    query = query.filter(
                        Ticket.status == "ACTIVE", Ticket.qr_revoked_at.is_(None),
                        Ticket.qr_expires_at <= now_utc
                    )
                elif status == "REVOKED":
                    query = query.filter(Ticket.qr_revoked_at.is_not(None))
                elif status == "CLOSED":
                    query = query.filter(Ticket.status != "ACTIVE")

            total = query.count()
            rows = query.order_by(Ticket.created_at.desc(), Ticket.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size).all()
            records = [{
                "id": ticket.id,
                "ticket_code": ticket.ticket_code,
                "vehicle_number": ticket.vehicle_number,
                "vehicle_type": ticket.vehicle_type,
                "slot_number": slot_number,
                "username": username,
                "booking_time": ticket.booking_time.isoformat() if ticket.booking_time else None,
                "entry_time": ticket.entry_time.isoformat() if ticket.entry_time else None,
                "exit_time": ticket.exit_time.isoformat() if ticket.exit_time else None,
                "duration_minutes": ticket.duration_minutes or 0,
                "total_amount": float(ticket.total_amount or 0),
                "payment_status": ticket.payment_status,
                "payment_method": ticket.payment_method,
                "transaction_id": ticket.transaction_id,
                "status": ticket.status,
                "qr_status": (
                    "REVOKED" if ticket.qr_revoked_at
                    else "EXPIRED" if _qr_token_expired(ticket.qr_expires_at, now_utc)
                    else "ACTIVE" if ticket.status == "ACTIVE"
                    else "CLOSED"
                ),
                "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            } for ticket, username, slot_number in rows]

        return jsonify({
            "success": True,
            "records": records,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": (total + page_size - 1) // page_size,
            }
        })
    except Exception:
        logger.exception("Administrator %s records request failed", record_type)
        return jsonify({"success": False, "error": "Unable to load administrator records."}), 500
    finally:
        session_db.close()


@api.route("/admin/settings", methods=["GET"])
@admin_required
def get_admin_settings():
    """Expose non-secret runtime settings; rate changes are deployment-configured."""
    return jsonify({
        "success": True,
        "editable": False,
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "rates": {
            vehicle_type: {
                "base": float(rates["base"]),
                "hourly": float(rates["hourly"]),
            }
            for vehicle_type, rates in Config.RATES.items()
        },
        "peak_hours": [{"start": start, "end": end} for start, end in Config.PEAK_HOURS],
        "peak_multiplier": Config.PEAK_MULTIPLIER,
        "facility": {
            "latitude": float(Config.FACILITY_LATITUDE) if Config.FACILITY_LATITUDE else None,
            "longitude": float(Config.FACILITY_LONGITUDE) if Config.FACILITY_LONGITUDE else None,
        },
        "integrations": {
            "hardware": "disabled",
            "payments": "development simulation",
        },
    })


@api.route("/analytics", methods=["GET"])
@admin_required
def get_analytics():
    """Dashboard analytics: occupancy, revenue, zone utilization, and logs."""
    session = database.SessionLocal()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        today_ist = now.astimezone(IST).date()
        today_start = datetime.datetime.combine(today_ist, datetime.time.min, tzinfo=IST)
        today_start = today_start.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        active_inventory = ParkingSlot.floor_level == 0
        total_slots = session.query(ParkingSlot).filter(active_inventory).count()
        occupied_slots = session.query(ParkingSlot).filter(
            active_inventory, ParkingSlot.status == "OCCUPIED"
        ).count()
        available_slots = session.query(ParkingSlot).filter(
            active_inventory, ParkingSlot.status == "AVAILABLE"
        ).count()

        # Tickets today
        tickets_today = session.query(Ticket).filter(Ticket.created_at >= today_start).all()
        revenue_today = sum(float(t.total_amount) for t in tickets_today if t.payment_status == "PAID")

        # Zone stats
        slots = session.query(ParkingSlot).filter(active_inventory).all()
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
                "available_slots": available_slots,
                "occupancy_rate": round((occupied_slots / total_slots * 100), 1) if total_slots > 0 else 0,
                "total_vehicles_today": len(tickets_today),
                "revenue_today": round(revenue_today, 2)
            },
            "zones": zones,
            "recent_logs": [log.to_dict() for log in recent_logs]
        })
    finally:
        session.close()


@api.route("/admin/analytics", methods=["GET"])
@admin_required
def get_admin_analytics():
    """Return bounded, persisted analytics grouped on India-local calendar dates."""
    today_ist = datetime.datetime.now(datetime.timezone.utc).astimezone(IST).date()
    period = str(request.args.get("period", "7d")).strip().lower()
    if period == "today":
        start_date = end_date = today_ist
    elif period in {"7d", "30d"}:
        days = 7 if period == "7d" else 30
        start_date, end_date = today_ist - datetime.timedelta(days=days - 1), today_ist
    elif period == "custom":
        try:
            start_date = datetime.date.fromisoformat(request.args.get("start", ""))
            end_date = datetime.date.fromisoformat(request.args.get("end", ""))
        except ValueError:
            return jsonify({"success": False, "error": "Custom dates must use YYYY-MM-DD."}), 400
        if start_date > end_date or (end_date - start_date).days > 365:
            return jsonify({"success": False, "error": "Select a valid date range of at most 366 days."}), 400
    else:
        return jsonify({"success": False, "error": "Period must be today, 7d, 30d, or custom."}), 400

    start_utc = datetime.datetime.combine(
        start_date, datetime.time.min, tzinfo=IST
    ).astimezone(datetime.timezone.utc).replace(tzinfo=None)
    end_utc = datetime.datetime.combine(
        end_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=IST
    ).astimezone(datetime.timezone.utc).replace(tzinfo=None)
    session_db = database.SessionLocal()
    try:
        def within_range(column):
            return and_(column >= start_utc, column < end_utc)

        ticket_rows = session_db.query(
            Ticket.created_at, Ticket.entry_time, Ticket.exit_time, Ticket.vehicle_type,
            Ticket.duration_minutes, Ticket.total_amount, Ticket.payment_status, Ticket.status
        ).filter(or_(
            within_range(Ticket.created_at),
            within_range(Ticket.entry_time),
            within_range(Ticket.exit_time),
        )).all()
        daily = {}
        vehicle_counts = {}
        peak_hours = [0] * 24
        paid_revenue = 0.0
        completed_durations = []
        tickets_created = 0
        arrivals = 0
        for created_at, entry_time, exit_time, vehicle_type, duration, amount, payment_status, status in ticket_rows:
            if created_at and start_utc <= utc_naive(created_at) < end_utc:
                local_created = utc_naive(created_at).replace(tzinfo=datetime.timezone.utc).astimezone(IST)
                created_day = local_created.date().isoformat()
                day = daily.setdefault(created_day, {"date": created_day, "tickets_created": 0, "arrivals": 0, "revenue": 0.0})
                day["tickets_created"] += 1
                tickets_created += 1
            if entry_time and start_utc <= utc_naive(entry_time) < end_utc:
                local_entry = utc_naive(entry_time).replace(tzinfo=datetime.timezone.utc).astimezone(IST)
                entry_day = local_entry.date().isoformat()
                day = daily.setdefault(entry_day, {"date": entry_day, "tickets_created": 0, "arrivals": 0, "revenue": 0.0})
                day["arrivals"] += 1
                arrivals += 1
                vehicle_counts[vehicle_type] = vehicle_counts.get(vehicle_type, 0) + 1
                peak_hours[local_entry.hour] += 1
            if exit_time and start_utc <= utc_naive(exit_time) < end_utc and payment_status == "PAID":
                local_exit = utc_naive(exit_time).replace(tzinfo=datetime.timezone.utc).astimezone(IST)
                exit_day = local_exit.date().isoformat()
                day = daily.setdefault(exit_day, {"date": exit_day, "tickets_created": 0, "arrivals": 0, "revenue": 0.0})
                paid_revenue += float(amount or 0)
                day["revenue"] += float(amount or 0)
            if status == "COMPLETED" and exit_time and start_utc <= utc_naive(exit_time) < end_utc and duration and duration > 0:
                completed_durations.append(int(duration))

        date_count = (end_date - start_date).days + 1
        series = []
        for offset in range(date_count):
            date_key = (start_date + datetime.timedelta(days=offset)).isoformat()
            series.append(daily.get(date_key, {
                "date": date_key, "tickets_created": 0, "arrivals": 0, "revenue": 0.0
            }))

        active_inventory = ParkingSlot.floor_level == 0
        total_slots = session_db.query(ParkingSlot).filter(active_inventory).count()
        occupied_slots = session_db.query(ParkingSlot).filter(
            active_inventory, ParkingSlot.status == "OCCUPIED"
        ).count()
        return jsonify({
            "success": True,
            "range": {"start": start_date.isoformat(), "end": end_date.isoformat(), "timezone": "Asia/Kolkata"},
            "metrics": {
                "tickets_created": tickets_created,
                "arrivals": arrivals,
                "paid_revenue": round(paid_revenue, 2),
                "average_duration_minutes": round(sum(completed_durations) / len(completed_durations), 1)
                    if completed_durations else 0,
                "current_occupancy_percent": round(occupied_slots / total_slots * 100, 1)
                    if total_slots else 0,
            },
            "daily": series,
            "vehicle_types": vehicle_counts,
            "entry_hours_ist": peak_hours,
        })
    except Exception:
        logger.exception("Administrator analytics request failed")
        return jsonify({"success": False, "error": "Unable to load analytics."}), 500
    finally:
        session_db.close()


# ============================================================
# IOT HARDWARE WEBHOOKS (ESP32-CAM & ARDUINO)
# ============================================================

@api.route("/iot/entry-camera", methods=["POST"])
def iot_entry_camera():
    """ESP32-CAM image upload endpoint at Entry Gate."""
    return jsonify({"success": False, "error": "IoT integrations are retired in the software-only deployment."}), 410

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
    return jsonify({"success": False, "error": "IoT integrations are retired in the software-only deployment."}), 410

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
    return jsonify({"success": False, "error": "IoT integrations are retired in the software-only deployment."}), 410
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
    return jsonify({"success": False, "error": "IoT integrations are retired in the software-only deployment."}), 410
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
    return jsonify({"success": False, "error": "Demo reset has been retired; operational records are preserved."}), 410
