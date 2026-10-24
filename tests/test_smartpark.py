"""
SMARTPARK - Test Suite
Tests for database init, DSA slot allocation, Dijkstra navigation,
billing calculations, ANPR service, and REST API endpoints.
"""

import os
import sys
import unittest
import datetime
import uuid
from unittest.mock import patch
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

os.environ["FLASK_ENV"] = "development"
os.environ["FLASK_DEBUG"] = "0"
os.environ["AUTO_CREATE_SCHEMA"] = "1"
os.environ["SUPABASE_DB_URL"] = ""
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import create_app, _session_cookie_secure
import database
from database import init_db, ParkingSlot, Ticket, User, SystemConfig, LoginAttempt, GateLog
from services import (
    SlotAllocationService, ANPRService, BillingService,
    IoTGatewayService, facility_graph
)
from config import Config
from routes import _qr_token_expired


class SmartParkTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        # Reset demo before each test
        session = database.SessionLocal()
        try:
            session.query(ParkingSlot).update({
                "status": "AVAILABLE",
                "sensor_state": "EMPTY",
                "active_vehicle_number": None
            })
            session.query(Ticket).filter_by(status="ACTIVE").update({
                "status": "COMPLETED",
                "exit_time": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            })
            session.query(LoginAttempt).delete()
            session.commit()
        finally:
            session.close()
        IoTGatewayService.reset_all()

    def test_session_cookie_secure_defaults_to_environment_and_can_be_overridden(self):
        with patch.dict(os.environ, {"FLASK_ENV": "production"}, clear=False):
            os.environ.pop("SESSION_COOKIE_SECURE", None)
            self.assertTrue(_session_cookie_secure())
            os.environ["SESSION_COOKIE_SECURE"] = "0"
            self.assertFalse(_session_cookie_secure())
            os.environ["SESSION_COOKIE_SECURE"] = "true"
            self.assertTrue(_session_cookie_secure())

    # --------------------------------------------------------
    # 1. DSA & SERVICES TESTS
    # --------------------------------------------------------

    def test_qr_expiry_handles_aware_and_naive_timestamps(self):
        now = datetime.datetime(2026, 10, 24, 12, 0)
        self.assertFalse(_qr_token_expired(None, now))
        self.assertFalse(_qr_token_expired(now + datetime.timedelta(minutes=1), now))
        self.assertFalse(_qr_token_expired(
            (now + datetime.timedelta(minutes=1)).replace(tzinfo=datetime.timezone.utc),
            now
        ))
        self.assertTrue(_qr_token_expired(
            (now - datetime.timedelta(minutes=1)).replace(tzinfo=datetime.timezone.utc),
            now
        ))

    def test_dijkstra_graph_navigation(self):
        """Test Dijkstra's algorithm for finding shortest path and turn-by-turn guidance."""
        res = facility_graph.get_shortest_path("ENTRY_GATE", "A-01")
        self.assertTrue(res["success"])
        self.assertGreater(res["total_distance_meters"], 0)
        self.assertIn("ENTRY_GATE", res["path"])
        self.assertIn("A-01", res["path"])
        self.assertGreater(len(res["turn_by_turn"]), 0)
        self.assertFalse(facility_graph.get_shortest_path("ENTRY_GATE", "C-01")["success"])
        self.assertNotIn("RAMP_FLOOR_1", facility_graph.adj)

    def test_min_heap_slot_allocation(self):
        """Test Min-Heap priority queue allocation based on distance and compatibility."""
        session = database.SessionLocal()
        try:
            # Allocate for TWO_WHEELER
            slot, nav = SlotAllocationService.find_and_assign_slot(session, "TWO_WHEELER", "KA-01-AB-1234")
            self.assertIsNotNone(slot)
            self.assertEqual(slot.status, "OCCUPIED")
            self.assertIn(slot.slot_type, ["TWO_WHEELER", "COMPACT"])

            # Allocate for EV
            ev_slot, ev_nav = SlotAllocationService.find_and_assign_slot(session, "EV", "KA-01-EV-9999")
            self.assertIsNotNone(ev_slot)
            self.assertEqual(ev_slot.status, "OCCUPIED")
            self.assertIn(ev_slot.slot_type, ["EV", "COMPACT"])
        finally:
            session.close()

    def test_anpr_plate_cleaning(self):
        """Test license plate cleaning and formatting heuristic."""
        self.assertEqual(ANPRService.clean_plate_number("ka01mj5021"), "KA-01-MJ-5021")
        self.assertEqual(ANPRService.clean_plate_number("SP-ABC12345"), "SPABC12345")
        self.assertEqual(ANPRService.clean_plate_number(""), "")

    def test_billing_calculation(self):
        """Test dynamic billing calculation including duration and taxes."""
        entry = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=2, minutes=15)
        bill = BillingService.calculate_bill(entry, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None), "COMPACT")
        self.assertEqual(bill["vehicle_type"], "COMPACT")
        self.assertEqual(bill["duration_minutes"], 135)
        self.assertGreater(bill["grand_total"], bill["base_rate"])

    # --------------------------------------------------------
    # 2. REST API ENDPOINTS TESTS
    # --------------------------------------------------------

    def test_system_status(self):
        """Test /api/status endpoint."""
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "online")
        self.assertIn("capacity", data)
        self.assertEqual(data["capacity"]["total"], 18)

    def test_slots_list(self):
        """Test /api/slots endpoint."""
        res = self.client.get("/api/slots")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 18)
        self.assertTrue(all(slot["floor_level"] == 0 for slot in data["slots"]))

    def test_legacy_deck_slots_are_excluded_from_operational_inventory(self):
        session = database.SessionLocal()
        legacy_slot = None
        try:
            legacy_slot = ParkingSlot(
                slot_number="C-LEGACY",
                floor_level=1,
                zone="C",
                slot_type="COMPACT",
                distance_from_entry=50,
                status="AVAILABLE",
                sensor_state="EMPTY"
            )
            session.add(legacy_slot)
            session.commit()

            allocated, _ = SlotAllocationService.find_and_assign_slot(
                session, "COMPACT", "DL-01-AA-1234"
            )
            self.assertIsNotNone(allocated)
            self.assertNotEqual(allocated.slot_number, "C-LEGACY")
            session.rollback()

            reserve_response = self.client.post("/api/slots/reserve", json={
                "slot_number": "C-LEGACY",
                "vehicle_number": "DL-01-AA-4321",
                "vehicle_type": "COMPACT"
            })
            self.assertEqual(reserve_response.status_code, 400)

            status = self.client.get("/api/status").get_json()
            slots = self.client.get("/api/slots").get_json()
            self.assertEqual(status["capacity"]["total"], 18)
            self.assertEqual(slots["count"], 18)
            self.assertNotIn("C-LEGACY", {slot["slot_number"] for slot in slots["slots"]})

            analytics_client = self.app.test_client()
            with analytics_client.session_transaction() as admin_session:
                admin_session["admin_user"] = {"id": 1, "username": "test-admin", "role": "ADMIN"}
            analytics = analytics_client.get("/api/analytics").get_json()
            self.assertEqual(analytics["metrics"]["total_slots"], 18)
            self.assertEqual(analytics["metrics"]["available_slots"], 18)
            self.assertNotIn("C", analytics["zones"])
        finally:
            if legacy_slot:
                session.delete(legacy_slot)
                session.commit()
            session.close()

    def test_booking_and_payment_flow(self):
        """Test full workflow: reserve slot -> lookup ticket -> pay -> exit."""
        plate = "DL-04-CA-1029"

        # 1. Reserve Slot
        res = self.client.post("/api/slots/reserve", json={
            "vehicle_number": plate,
            "vehicle_type": "COMPACT"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        ticket = data["ticket"]
        ticket_code = ticket["ticket_code"]
        slot_session = database.SessionLocal()
        try:
            slot = slot_session.query(ParkingSlot).filter_by(id=ticket["slot_id"]).first()
            self.assertEqual(slot.status, "RESERVED")
        finally:
            slot_session.close()

        # Reservations are not billable until the vehicle is checked in.
        self.assertIsNone(ticket["entry_time"])
        rejected = self.client.post("/api/payment/process", json={"ticket_code": ticket_code})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(ticket["payment_status"], "PENDING")
        login = self.client.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD
        })
        csrf = login.headers["X-CSRF-Token"]
        entry = self.client.post("/api/admin/qr/scan-entry", json={"token": ticket_code}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(entry.status_code, 200)
        self.assertIsNotNone(entry.get_json()["ticket"]["entry_time"])
        slot_session = database.SessionLocal()
        try:
            slot = slot_session.query(ParkingSlot).filter_by(id=ticket["slot_id"]).first()
            self.assertEqual(slot.status, "OCCUPIED")
            self.assertEqual(slot.active_vehicle_number, plate)
        finally:
            slot_session.close()

        # 2. Lookup ticket
        res = self.client.get(f"/api/tickets/{ticket_code}")
        self.assertEqual(res.status_code, 200)
        ticket_data = res.get_json()["ticket"]
        self.assertEqual(ticket_data["payment_status"], "PENDING")
        self.assertTrue(ticket_data["billing"]["is_estimate"])

        # 3. Pay ticket
        failed = self.client.post("/api/payment/process", json={
            "ticket_code": ticket_code, "payment_method": "NOT_SUPPORTED"
        })
        self.assertEqual(failed.status_code, 400)
        active_session = database.SessionLocal()
        try:
            active_ticket = active_session.query(Ticket).filter_by(ticket_code=ticket_code).one()
            self.assertEqual(active_ticket.status, "ACTIVE")
            self.assertEqual(active_ticket.payment_status, "PENDING")
            self.assertEqual(active_ticket.slot.status, "OCCUPIED")
        finally:
            active_session.close()

        res = self.client.post("/api/payment/process", json={
            "ticket_code": ticket_code,
            "payment_method": "UPI_QR"
        })
        self.assertEqual(res.status_code, 200)
        pay_data = res.get_json()
        self.assertTrue(pay_data["success"])
        self.assertEqual(pay_data["payment_status"], "PAID")
        persisted = database.SessionLocal()
        try:
            stored_ticket = persisted.query(Ticket).filter_by(ticket_code=ticket_code).one()
            self.assertEqual(stored_ticket.status, "COMPLETED")
            self.assertIsNotNone(stored_ticket.exit_time)
            self.assertIsNotNone(stored_ticket.entry_time)
            self.assertGreaterEqual(stored_ticket.exit_time, stored_ticket.entry_time)
            self.assertGreater(stored_ticket.total_amount, 0)
            expected_bill = BillingService.calculate_bill(
                stored_ticket.entry_time, stored_ticket.exit_time, stored_ticket.vehicle_type
            )
            self.assertAlmostEqual(float(stored_ticket.total_amount), expected_bill["grand_total"])
            self.assertEqual(stored_ticket.duration_minutes, expected_bill["duration_minutes"])
            self.assertEqual(stored_ticket.slot.status, "AVAILABLE")
            exit_log_count = persisted.query(GateLog).filter_by(
                vehicle_number=plate, gate_type="EXIT"
            ).count()
            self.assertEqual(exit_log_count, 1)
        finally:
            persisted.close()

        duplicate = self.client.post("/api/payment/process", json={
            "ticket_code": ticket_code,
            "payment_method": "UPI_QR"
        })
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.get_json()["duplicate"])
        final_details = self.client.get(f"/api/tickets/{ticket_code}")
        self.assertEqual(final_details.status_code, 200)
        self.assertTrue(final_details.get_json()["ticket"]["billing"]["is_final"])
        self.assertEqual(final_details.get_json()["ticket"]["payment_status"], "PAID")
        persisted = database.SessionLocal()
        try:
            self.assertEqual(
                persisted.query(GateLog).filter_by(vehicle_number=plate, gate_type="EXIT").count(),
                1
            )
        finally:
            persisted.close()

        invalid_method = self.client.post("/api/payment/process", json={
            "ticket_code": ticket_code,
            "payment_method": "FORGED_GATEWAY"
        })
        self.assertEqual(invalid_method.status_code, 400)

    def test_admin_authentication_and_simulation(self):
        """Test admin login, simulated entry, QR scan, and exit."""
        # Unauthenticated admin action should fail
        res = self.client.post("/api/entry/simulate", json={"vehicle_number": "KA-01-MJ-5021"})
        self.assertEqual(res.status_code, 401)

        # Login as admin
        res = self.client.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME,
            "password": Config.ADMIN_PASSWORD
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])
        csrf_token = res.headers.get("X-CSRF-Token")
        self.assertTrue(csrf_token)

        booking = self.client.post("/api/slots/reserve", json={
            "vehicle_number": "KA-01-MJ-5021", "vehicle_type": "COMPACT"
        })
        self.assertEqual(booking.status_code, 200)
        ticket_code = booking.get_json()["ticket"]["ticket_code"]

        # Scan QR token
        res = self.client.post("/api/admin/qr/scan", json={"token": f"SMARTPARK:{ticket_code}"})
        # CSRF is required for all state-changing admin requests.
        self.assertEqual(res.status_code, 403)
        res = self.client.post("/api/admin/qr/scan", json={"token": f"SMARTPARK:{ticket_code}"}, headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])

        self.assertEqual(self.client.post("/api/entry/simulate", json={"booking_id": ticket_code}, headers={"X-CSRF-Token": csrf_token}).status_code, 410)
        self.assertEqual(self.client.post("/api/exit/simulate", json={"ticket_code": ticket_code}, headers={"X-CSRF-Token": csrf_token}).status_code, 410)

    def test_admin_can_force_clear_one_occupied_parking_without_payment(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/admin/slots/1/clear").status_code, 401)

        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME,
            "password": Config.ADMIN_PASSWORD
        })
        csrf = login.headers["X-CSRF-Token"]
        booking = admin.post("/api/slots/reserve", json={
            "vehicle_number": "KA-11-FC-8080",
            "vehicle_type": "COMPACT"
        })
        self.assertEqual(booking.status_code, 200)
        ticket_data = booking.get_json()["ticket"]
        slot_id = ticket_data["slot_id"]
        ticket_code = ticket_data["ticket_code"]
        entered = admin.post(
            "/api/admin/qr/scan-entry",
            json={"token": ticket_code},
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(entered.status_code, 200)

        missing_csrf = admin.post(f"/api/admin/slots/{slot_id}/clear")
        self.assertEqual(missing_csrf.status_code, 403)
        cleared = admin.post(
            f"/api/admin/slots/{slot_id}/clear",
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(cleared.status_code, 200)
        response = cleared.get_json()
        self.assertFalse(response["payment_recorded"])
        self.assertEqual(response["ticket"]["status"], "COMPLETED")
        self.assertEqual(response["ticket"]["payment_status"], "PENDING")
        self.assertEqual(response["ticket"]["payment_method"], "NONE")
        self.assertIsNotNone(response["ticket"]["exit_time"])
        self.assertGreater(response["ticket"]["total_amount"], 0)
        self.assertEqual(response["slot"]["status"], "AVAILABLE")
        self.assertIsNone(response["slot"]["active_vehicle_number"])

        repeated = admin.post(
            f"/api/admin/slots/{slot_id}/clear",
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(repeated.status_code, 409)

        persisted = database.SessionLocal()
        try:
            ticket = persisted.query(Ticket).filter_by(ticket_code=ticket_code).one()
            self.assertEqual(ticket.status, "COMPLETED")
            self.assertEqual(ticket.payment_status, "PENDING")
            self.assertEqual(ticket.slot.status, "AVAILABLE")
            self.assertEqual(
                persisted.query(GateLog).filter(
                    GateLog.vehicle_number == "KA-11-FC-8080",
                    GateLog.gate_type == "EXIT"
                ).count(),
                1
            )
        finally:
            persisted.close()

    def test_admin_can_cancel_reserved_parking_before_check_in(self):
        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME,
            "password": Config.ADMIN_PASSWORD
        })
        csrf = login.headers["X-CSRF-Token"]
        booking = admin.post("/api/slots/reserve", json={
            "vehicle_number": "KA-12-RS-9191",
            "vehicle_type": "COMPACT"
        })
        self.assertEqual(booking.status_code, 200)
        ticket = booking.get_json()["ticket"]

        response = admin.post(
            f"/api/admin/slots/{ticket['slot_id']}/clear",
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ticket"]["status"], "CANCELLED")
        self.assertEqual(response.get_json()["slot"]["status"], "AVAILABLE")
        persisted = database.SessionLocal()
        try:
            slot = persisted.query(ParkingSlot).filter_by(id=ticket["slot_id"]).one()
            stored_ticket = persisted.query(Ticket).filter_by(ticket_code=ticket["ticket_code"]).one()
            self.assertEqual(slot.status, "AVAILABLE")
            self.assertEqual(stored_ticket.status, "CANCELLED")
            self.assertIsNotNone(stored_ticket.qr_revoked_at)
        finally:
            persisted.close()

    def test_admin_can_edit_and_cancel_reserved_slot(self):
        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME,
            "password": Config.ADMIN_PASSWORD
        })
        headers = {"X-CSRF-Token": login.headers["X-CSRF-Token"]}
        booking_time = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
        ).isoformat()
        booking = admin.post("/api/slots/reserve", json={
            "vehicle_number": "KA-15-RS-4567",
            "vehicle_type": "COMPACT",
            "booking_time": booking_time
        })
        self.assertEqual(booking.status_code, 200)
        ticket = booking.get_json()["ticket"]
        slot_id = ticket["slot_id"]

        edited = admin.patch(
            f"/api/admin/slots/{slot_id}/edit",
            json={"vehicle_number": "KA15XY9876", "slot_type": "EV"},
            headers=headers
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()["slot"]["status"], "RESERVED")
        self.assertEqual(edited.get_json()["slot"]["active_vehicle_number"], "KA-15-XY-9876")
        self.assertEqual(edited.get_json()["ticket"]["vehicle_number"], "KA-15-XY-9876")

        cleared = admin.post(f"/api/admin/slots/{slot_id}/clear", headers=headers)
        self.assertEqual(cleared.status_code, 200)
        result = cleared.get_json()
        self.assertFalse(result["payment_recorded"])
        self.assertEqual(result["ticket"]["status"], "CANCELLED")
        self.assertEqual(result["slot"]["status"], "AVAILABLE")
        self.assertIsNone(result["slot"]["active_vehicle_number"])

        persisted = database.SessionLocal()
        try:
            stored_ticket = persisted.query(Ticket).filter_by(ticket_code=ticket["ticket_code"]).one()
            stored_slot = persisted.query(ParkingSlot).filter_by(id=slot_id).one()
            self.assertEqual(stored_ticket.status, "CANCELLED")
            self.assertIsNotNone(stored_ticket.qr_revoked_at)
            self.assertEqual(stored_slot.status, "AVAILABLE")
            self.assertEqual(stored_ticket.slot.slot_type, "EV")
        finally:
            persisted.close()

    def test_admin_can_edit_occupied_vehicle_number_and_slot_type(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.patch("/api/admin/slots/1/edit", json={"slot_type": "EV"}).status_code, 401)

        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME,
            "password": Config.ADMIN_PASSWORD
        })
        csrf = login.headers["X-CSRF-Token"]
        booking = admin.post("/api/slots/reserve", json={
            "vehicle_number": "KA-13-ED-2525",
            "vehicle_type": "COMPACT"
        })
        self.assertEqual(booking.status_code, 200)
        ticket = booking.get_json()["ticket"]
        headers = {"X-CSRF-Token": csrf}
        entered = admin.post(
            f"/api/admin/qr/scan-entry",
            json={"token": ticket["ticket_code"]},
            headers=headers
        )
        self.assertEqual(entered.status_code, 200)

        edited = admin.patch(
            f"/api/admin/slots/{ticket['slot_id']}/edit",
            json={"vehicle_number": "MH12AB9876", "slot_type": "EV"},
            headers=headers
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()["slot"]["active_vehicle_number"], "MH-12-AB-9876")
        self.assertEqual(edited.get_json()["slot"]["slot_type"], "EV")
        self.assertEqual(edited.get_json()["ticket"]["vehicle_number"], "MH-12-AB-9876")

        persisted = database.SessionLocal()
        try:
            stored_slot = persisted.query(ParkingSlot).filter_by(id=ticket["slot_id"]).one()
            stored_ticket = persisted.query(Ticket).filter_by(ticket_code=ticket["ticket_code"]).one()
            self.assertEqual(stored_slot.status, "OCCUPIED")
            self.assertEqual(stored_slot.active_vehicle_number, "MH-12-AB-9876")
            self.assertEqual(stored_slot.slot_type, "EV")
            self.assertEqual(stored_ticket.vehicle_number, "MH-12-AB-9876")
            self.assertEqual(stored_ticket.status, "ACTIVE")
        finally:
            persisted.close()

        payment = admin.post("/api/payment/process", json={"ticket_code": ticket["ticket_code"]})
        self.assertEqual(payment.status_code, 200)

    def test_admin_parking_edit_rejects_invalid_fields_and_unoccupied_slots(self):
        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME,
            "password": Config.ADMIN_PASSWORD
        })
        headers = {"X-CSRF-Token": login.headers["X-CSRF-Token"]}
        booking = admin.post("/api/slots/reserve", json={
            "vehicle_number": "KA-14-IV-3626",
            "vehicle_type": "COMPACT"
        })
        ticket = booking.get_json()["ticket"]
        entered = admin.post(
            "/api/admin/qr/scan-entry",
            json={"token": ticket["ticket_code"]},
            headers=headers
        )
        self.assertEqual(entered.status_code, 200)

        invalid_type = admin.patch(
            f"/api/admin/slots/{ticket['slot_id']}/edit",
            json={"slot_type": "FLYING"},
            headers=headers
        )
        self.assertEqual(invalid_type.status_code, 400)
        invalid_plate = admin.patch(
            f"/api/admin/slots/{ticket['slot_id']}/edit",
            json={"vehicle_number": "  "},
            headers=headers
        )
        self.assertEqual(invalid_plate.status_code, 400)

        slot_session = database.SessionLocal()
        try:
            available_slot_id = slot_session.query(ParkingSlot).filter_by(status="AVAILABLE").first().id
        finally:
            slot_session.close()
        not_occupied = admin.patch(
            f"/api/admin/slots/{available_slot_id}/edit",
            json={"slot_type": "SUV"},
            headers=headers
        )
        self.assertEqual(not_occupied.status_code, 409)

    def test_login_rate_limit(self):
        for _ in range(15):
            response = self.client.post("/api/admin/login", json={"username": "wrong", "password": "wrong"})
            self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/admin/login", json={"username": "wrong", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response.headers.get("Retry-After"))

    def test_customer_login_rate_limit_remains_five_attempts(self):
        for _ in range(5):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "missing_customer", "password": "wrong"}
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/auth/login",
            json={"username": "missing_customer", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 429)

    def test_booking_is_not_billable_until_single_use_qr_entry(self):
        scheduled = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(minutes=10)).isoformat()
        response = self.client.post("/api/slots/reserve", json={
            "vehicle_number": "MH-12-PQ-8891",
            "vehicle_type": "COMPACT",
            "booking_time": scheduled
        })
        self.assertEqual(response.status_code, 200)
        ticket = response.get_json()["ticket"]
        self.assertIsNone(ticket["entry_time"])
        details = self.client.get(f"/api/tickets/{ticket['ticket_code']}")
        self.assertEqual(details.status_code, 200)
        self.assertTrue(details.get_json()["ticket"]["billing"]["is_estimate"])
        self.assertEqual(details.get_json()["ticket"]["reservation_status"], "UPCOMING / NOT_CHECKED_IN")
        estimate = details.get_json()["ticket"]["billing"]
        self.assertEqual(estimate["expected_duration_minutes"], 120)
        self.assertGreater(estimate["grand_total"], 0)
        self.assertEqual(details.get_json()["ticket"]["slot_number"], ticket["slot_number"])
        self.assertEqual(details.get_json()["ticket"]["total_amount"], 0)
        self.assertEqual(details.get_json()["ticket"]["payment_status"], "PENDING")
        slot_session = database.SessionLocal()
        try:
            slot = slot_session.query(ParkingSlot).filter_by(id=ticket["slot_id"]).first()
            self.assertEqual(slot.status, "RESERVED")
        finally:
            slot_session.close()
        pending = self.client.post("/api/payment/process", json={"ticket_code": ticket["ticket_code"]})
        self.assertEqual(pending.status_code, 409)

        login = self.client.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD
        })
        csrf = login.headers["X-CSRF-Token"]
        entered = self.client.post("/api/admin/qr/scan-entry", json={"token": ticket["ticket_code"]}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(entered.status_code, 200)
        replay = self.client.post("/api/admin/qr/scan-entry", json={"token": ticket["ticket_code"]}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json()["scan_stage"], "EXIT_CHECK")

    def test_owned_ticket_cannot_be_inspected_or_paid_without_owner_session(self):
        suffix = uuid.uuid4().hex[:8]
        owner = self.app.test_client()
        registered = owner.post("/api/auth/register", json={
            "username": f"pay_owner_{suffix}", "password": "owner-password-123", "full_name": "Payment Owner"
        })
        self.assertEqual(registered.status_code, 201)
        booking = owner.post("/api/slots/reserve", json={
            "vehicle_number": f"KA-09-PO-{suffix[:4]}", "vehicle_type": "COMPACT"
        })
        self.assertEqual(booking.status_code, 200)
        code = booking.get_json()["ticket"]["ticket_code"]

        admin = self.app.test_client()
        admin_login = admin.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD
        })
        entered = admin.post(
            "/api/admin/qr/scan-entry",
            json={"token": code},
            headers={"X-CSRF-Token": admin_login.headers["X-CSRF-Token"]}
        )
        self.assertEqual(entered.status_code, 200)

        guest = self.app.test_client()
        self.assertEqual(guest.get(f"/api/tickets/{code}").status_code, 404)
        self.assertEqual(guest.post("/api/payment/process", json={"ticket_code": code}).status_code, 404)
        self.assertEqual(owner.get(f"/api/tickets/{code}").status_code, 200)
        self.assertEqual(owner.post("/api/payment/process", json={"ticket_code": code}).status_code, 403)
        paid = owner.post(
            "/api/payment/process",
            json={"ticket_code": code},
            headers={"X-CSRF-Token": registered.headers["X-CSRF-Token"]}
        )
        self.assertEqual(paid.status_code, 200)

    def test_static_page_routing(self):
        """Test static page serving including extensionless URLs."""
        anonymous = self.app.test_client()
        res = anonymous.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["Location"], "/portals")

        res = anonymous.get("/booking")
        self.assertEqual(res.status_code, 200)

        res = anonymous.get("/payment")
        self.assertEqual(res.status_code, 200)

        parking = anonymous.get("/parking")
        self.assertEqual(parking.status_code, 200)
        self.assertIn(b"Live Availability", parking.data)
        self.assertNotIn(b"/admin/login", parking.data)
        self.assertNotIn(b"Choose portal", parking.data)
        self.assertIn(b'href="/" class="nav-brand"', parking.data)
        self.assertIn(b'href="/parking" class="active">Live Availability</a>', parking.data)

        customer_dashboard = anonymous.get("/customer")
        self.assertIn(b'href="/parking">Discover parking</a>', customer_dashboard.data)
        self.assertIn(b'href="/parking">Parking website</a>', customer_dashboard.data)
        login_page = anonymous.get("/customer/login")
        self.assertIn(b'href="/parking"', login_page.data)
        register_page = anonymous.get("/customer/register")
        self.assertIn(b'href="/parking"', register_page.data)
        booking_page = anonymous.get("/booking")
        self.assertIn(b'href="/parking" class="nav-brand"', booking_page.data)
        payment_page = anonymous.get("/payment")
        self.assertIn(b'href="/parking" class="nav-brand"', payment_page.data)

        self.assertEqual(anonymous.get("/customer").status_code, 200)
        self.assertEqual(anonymous.get("/customer/login").status_code, 200)
        self.assertEqual(anonymous.get("/customer/register").status_code, 200)
        portals = anonymous.get("/portals")
        self.assertEqual(portals.status_code, 200)
        self.assertIn(b"Admin operations website", portals.data)
        self.assertIn(b"Customer website", portals.data)
        self.assertIn(b'href="/parking">Explore parking</a>', portals.data)
        self.assertEqual(anonymous.get("/portal", follow_redirects=False).status_code, 200)

    def test_customer_role_is_separate_from_admin(self):
        registration = self.client.post("/api/auth/register", json={
            "username": "customer_one", "password": "a-strong-customer-password", "full_name": "Customer One"
        })
        self.assertEqual(registration.status_code, 201)
        self.assertEqual(registration.get_json()["user"]["role"], "CUSTOMER")
        self.assertIn("/customer", self.client.get("/admin", follow_redirects=False).headers["Location"])
        self.assertIn("/customer", self.client.get("/admin/dashboard", follow_redirects=False).headers["Location"])
        self.assertEqual(self.client.get("/api/admin/me").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/reset-demo").status_code, 403)
        self.assertEqual(self.client.get("/admin/login").headers["Location"], "/customer")
        self.assertEqual(self.client.get("/customer").status_code, 200)
        self.assertEqual(self.client.get("/portals").headers["Location"], "/customer")

        self.client.post("/api/auth/logout")
        login = self.client.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD
        })
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.get_json()["user"]["role"], "ADMIN")
        self.assertEqual(self.client.get("/customer").headers["Location"], "/admin")
        self.assertEqual(self.client.get("/customer/login").headers["Location"], "/admin")
        self.assertEqual(self.client.get("/portals").headers["Location"], "/admin")

    def test_admin_pages_and_logout_are_protected(self):
        with self.client.session_transaction() as browser_session:
            browser_session.clear()
        for path in ("/admin", "/admin/", "/admin.html", "/admin/dashboard", "/admin/parking"):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login", response.headers["Location"])

        self.assertEqual(self.client.post("/api/admin/qr/scan", json={}).status_code, 401)
        login = self.client.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD
        })
        self.assertEqual(login.status_code, 200)
        csrf = login.headers["X-CSRF-Token"]
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 200)
        admin_page = self.client.get("/admin")
        self.assertIn(b"Edit parking", admin_page.data)
        self.assertIn(b"Delete / force remove", admin_page.data)
        self.assertIn(b"Reserved (yellow)", admin_page.data)
        app_script = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text()
        self.assertIn('class="admin-edit-parking"', app_script)
        self.assertIn('["OCCUPIED", "RESERVED"].includes(slot.status)', app_script)
        self.assertIn('data-edit-slot-id="${Number(slot.id)}"', app_script)
        self.assertIn('onclick="openAdminParkingEditorById(${Number(slot.id)}, event)"', app_script)
        self.assertIn('editButton = event.target.closest("[data-edit-slot-id]")', app_script)
        self.assertIn(b"and occupied slots have an", admin_page.data)
        self.assertIn(b"app.js?v=admin-reserved-editor", admin_page.data)
        self.assertIn("function scheduleDailyAdminRefresh()", app_script)
        self.assertIn("await loadAnalytics();", app_script)
        self.assertIn("scheduleDailyAdminRefresh();", app_script)
        self.assertEqual(self.client.post("/api/admin/qr/scan", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/reset-demo").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertEqual(self.client.post("/api/admin/qr/scan", json={}).status_code, 401)

    def test_admin_scanner_authorizes_first_entry_and_reports_second_scan(self):
        self.assertEqual(self.client.get("/admin/scanner", follow_redirects=False).status_code, 302)
        login = self.client.post("/api/admin/login", json={
            "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD
        })
        csrf = login.headers["X-CSRF-Token"]
        booking = self.client.post("/api/slots/reserve", json={
            "vehicle_number": "KA-09-QR-7777", "vehicle_type": "COMPACT"
        })
        code = booking.get_json()["ticket"]["ticket_code"]
        first = self.client.post("/api/admin/qr/scan-entry", json={"token": code}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()["entry_authorized"])
        second = self.client.post("/api/admin/qr/scan-entry", json={"token": code}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["scan_stage"], "EXIT_CHECK")

    def test_customer_owns_booking_history_and_cannot_read_other_customer(self):
        first = self.client.post("/api/auth/register", json={
            "username": "owner_one", "password": "owner-password-123", "full_name": "Owner One"
        })
        self.assertEqual(first.status_code, 201)
        booking = self.client.post("/api/slots/reserve", json={
            "vehicle_number": "KA-01-AB-1111", "vehicle_type": "COMPACT"
        })
        self.assertEqual(booking.status_code, 200)
        ticket_code = booking.get_json()["ticket"]["ticket_code"]
        history = self.client.get("/api/customer/history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.get_json()["bookings"][0]["ticket_code"], ticket_code)

        other = self.app.test_client()
        registered = other.post("/api/auth/register", json={
            "username": "owner_two", "password": "owner-password-456", "full_name": "Owner Two"
        })
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(other.get(f"/api/tickets/{ticket_code}").status_code, 404)
        self.assertEqual(other.post(
            "/api/payment/process",
            json={"ticket_code": ticket_code},
            headers={"X-CSRF-Token": registered.headers["X-CSRF-Token"]}
        ).status_code, 404)

    def test_ticket_qr_is_generated_locally_and_requires_owner_access(self):
        suffix = uuid.uuid4().hex[:8]
        owner = self.app.test_client()
        registered = owner.post("/api/auth/register", json={
            "username": f"qr_owner_{suffix}", "password": "qr-owner-password-123", "full_name": "QR Owner"
        })
        booking = owner.post("/api/slots/reserve", json={
            "vehicle_number": f"KA-10-QR-{suffix[:4]}"
        })
        self.assertEqual(booking.status_code, 200)
        code = booking.get_json()["ticket"]["ticket_code"]
        qr = owner.get(f"/api/tickets/{code}/qr")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, "image/png")
        self.assertIn("no-store", qr.headers["Cache-Control"])
        self.assertTrue(qr.data.startswith(b"\x89PNG\r\n\x1a\n"))

        guest = self.app.test_client()
        self.assertEqual(guest.get(f"/api/tickets/{code}/qr").status_code, 404)
        other = self.app.test_client()
        other_registered = other.post("/api/auth/register", json={
            "username": f"qr_other_{suffix}", "password": "qr-other-password-123", "full_name": "Other"
        })
        self.assertEqual(other_registered.status_code, 201)
        self.assertEqual(other.get(f"/api/tickets/{code}/qr").status_code, 404)

    def test_guest_lookup_requires_opaque_ticket_code(self):
        response = self.client.get("/api/tickets/KA-01-AB-1111")
        self.assertEqual(response.status_code, 404)
        for _ in range(5):
            failed = self.client.get("/api/tickets/SP-NOT-A-REAL-TICKET")
            self.assertEqual(failed.status_code, 404)
        throttled = self.client.get("/api/tickets/SP-NOT-A-REAL-TICKET")
        self.assertEqual(throttled.status_code, 429)

    def test_customer_profile_update_validates_ownership_and_unique_username(self):
        suffix = uuid.uuid4().hex[:8]
        first = self.client.post("/api/auth/register", json={
            "username": f"profile_{suffix}", "password": "profile-password-123", "full_name": "Profile User"
        })
        self.assertEqual(first.status_code, 201)
        csrf = first.headers["X-CSRF-Token"]
        changed = self.client.put("/api/customer/profile", json={"username": f"updated_{suffix}"}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.get_json()["user"]["username"], f"updated_{suffix}")
        invalid = self.client.put("/api/customer/profile", json={"username": "!!"}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(invalid.status_code, 400)
        protected = self.client.put("/api/customer/profile", json={"username": f"final_{suffix}", "role": "ADMIN", "id": 1}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(protected.status_code, 200)
        self.assertEqual(protected.get_json()["user"]["role"], "CUSTOMER")
        self.assertEqual(self.client.get("/api/customer/profile").status_code, 200)
        self.client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(self.client.get("/api/customer/profile").status_code, 401)

    def test_customer_can_cancel_only_future_owned_reservation(self):
        suffix = uuid.uuid4().hex[:8]
        registered = self.client.post("/api/auth/register", json={
            "username": f"cancel_{suffix}", "password": "cancel-password-123", "full_name": "Cancel User"
        })
        csrf = registered.headers["X-CSRF-Token"]
        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        booking = self.client.post("/api/slots/reserve", json={"vehicle_number": f"KA-01-CN-{suffix[:4]}", "booking_time": future})
        self.assertEqual(booking.status_code, 200)
        ticket = booking.get_json()["ticket"]
        cancelled = self.client.post(f"/api/customer/bookings/{ticket['ticket_code']}/cancel", headers={"X-CSRF-Token": csrf})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["ticket"]["status"], "CANCELLED")
        repeated = self.client.post(f"/api/customer/bookings/{ticket['ticket_code']}/cancel", headers={"X-CSRF-Token": csrf})
        self.assertEqual(repeated.status_code, 409)
        history = self.client.get("/api/customer/history").get_json()
        self.assertEqual(history["upcoming_reservations"], [])
        self.assertEqual(history["bookings"][0]["status"], "CANCELLED")

    def test_customer_cannot_cancel_another_customers_reservation(self):
        suffix = uuid.uuid4().hex[:8]
        owner = self.client.post("/api/auth/register", json={"username": f"owner_{suffix}", "password": "owner-password-123", "full_name": "Owner"})
        booking = self.client.post("/api/slots/reserve", json={"vehicle_number": f"KA-02-OW-{suffix[:4]}", "booking_time": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()})
        code = booking.get_json()["ticket"]["ticket_code"]
        other = self.app.test_client()
        other_login = other.post("/api/auth/register", json={"username": f"other_{suffix}", "password": "other-password-123", "full_name": "Other"})
        response = other.post(f"/api/customer/bookings/{code}/cancel", headers={"X-CSRF-Token": other_login.headers["X-CSRF-Token"]})
        self.assertEqual(response.status_code, 404)

    def test_walk_in_creates_owned_qr_and_entry_starts_actual_session(self):
        suffix = uuid.uuid4().hex[:8]
        registered = self.client.post("/api/auth/register", json={"username": f"walk_{suffix}", "password": "walk-password-123", "full_name": "Walk User"})
        self.assertEqual(registered.status_code, 201)
        walk_in = self.client.post("/api/customer/walk-in", json={"vehicle_number": f"KA-03-WK-{suffix[:4]}", "vehicle_type": "COMPACT"}, headers={"X-CSRF-Token": registered.headers["X-CSRF-Token"]})
        self.assertEqual(walk_in.status_code, 201)
        ticket = walk_in.get_json()["ticket"]
        self.assertEqual(ticket["entry_mode"], "WALK_IN")
        history = self.client.get("/api/customer/history").get_json()
        self.assertEqual(history["upcoming_reservations"][0]["entry_mode"], "WALK_IN")
        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={"username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD})
        entered = admin.post("/api/admin/qr/scan-entry", json={"token": ticket["ticket_code"]}, headers={"X-CSRF-Token": login.headers["X-CSRF-Token"]})
        self.assertEqual(entered.status_code, 200)
        self.assertEqual(entered.get_json()["entry_type"], "WALK_IN")
        self.assertIsNotNone(entered.get_json()["ticket"]["entry_time"])
        paid = self.client.post(
            "/api/payment/process",
            json={"ticket_code": ticket["ticket_code"]},
            headers={"X-CSRF-Token": registered.headers["X-CSRF-Token"]}
        )
        self.assertEqual(paid.status_code, 200)
        persisted = database.SessionLocal()
        try:
            completed = persisted.query(Ticket).filter_by(ticket_code=ticket["ticket_code"]).one()
            self.assertEqual(completed.status, "COMPLETED")
            self.assertIsNotNone(completed.exit_time)
            self.assertEqual(completed.slot.status, "AVAILABLE")
        finally:
            persisted.close()

    def test_reservation_qr_accepts_early_arrival(self):
        scheduled = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat()
        booking = self.client.post("/api/slots/reserve", json={"vehicle_number": "KA-04-EA-1234", "booking_time": scheduled})
        self.assertEqual(booking.status_code, 200)
        ticket = booking.get_json()["ticket"]
        admin = self.app.test_client()
        login = admin.post("/api/admin/login", json={"username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD})
        entered = admin.post("/api/admin/qr/scan-entry", json={"token": ticket["ticket_code"]}, headers={"X-CSRF-Token": login.headers["X-CSRF-Token"]})
        self.assertEqual(entered.status_code, 200)
        self.assertEqual(entered.get_json()["entry_type"], "RESERVATION")


if __name__ == "__main__":
    unittest.main()
