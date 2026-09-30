"""
SMARTPARK - Test Suite
Tests for database init, DSA slot allocation, Dijkstra navigation,
billing calculations, ANPR service, and REST API endpoints.
"""

import os
import sys
import unittest
import datetime
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

os.environ["FLASK_ENV"] = "development"
os.environ["FLASK_DEBUG"] = "0"
os.environ["AUTO_CREATE_SCHEMA"] = "1"

from app import create_app
import database
from database import init_db, ParkingSlot, Ticket, User, SystemConfig
from services import (
    SlotAllocationService, ANPRService, BillingService,
    IoTGatewayService, facility_graph
)
from config import Config


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
                "exit_time": datetime.datetime.utcnow()
            })
            session.commit()
        finally:
            session.close()
        IoTGatewayService.reset_all()

    # --------------------------------------------------------
    # 1. DSA & SERVICES TESTS
    # --------------------------------------------------------

    def test_dijkstra_graph_navigation(self):
        """Test Dijkstra's algorithm for finding shortest path and turn-by-turn guidance."""
        res = facility_graph.get_shortest_path("ENTRY_GATE", "A-01")
        self.assertTrue(res["success"])
        self.assertGreater(res["total_distance_meters"], 0)
        self.assertIn("ENTRY_GATE", res["path"])
        self.assertIn("A-01", res["path"])
        self.assertGreater(len(res["turn_by_turn"]), 0)

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
        entry = datetime.datetime.utcnow() - datetime.timedelta(hours=2, minutes=15)
        bill = BillingService.calculate_bill(entry, datetime.datetime.utcnow(), "COMPACT")
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
        self.assertEqual(data["capacity"]["total"], 20)

    def test_slots_list(self):
        """Test /api/slots endpoint."""
        res = self.client.get("/api/slots")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 20)

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

        # 2. Lookup ticket
        res = self.client.get(f"/api/tickets/{ticket_code}")
        self.assertEqual(res.status_code, 200)
        ticket_data = res.get_json()["ticket"]
        self.assertEqual(ticket_data["payment_status"], "PENDING")

        # 3. Pay ticket
        res = self.client.post("/api/payment/process", json={
            "ticket_code": ticket_code,
            "payment_method": "UPI_QR"
        })
        self.assertEqual(res.status_code, 200)
        pay_data = res.get_json()
        self.assertTrue(pay_data["success"])
        self.assertEqual(pay_data["payment_status"], "PAID")

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

        # Simulate walk-in entry
        res = self.client.post("/api/entry/simulate", json={
            "vehicle_number": "KA-01-MJ-5021",
            "vehicle_type": "COMPACT"
        })
        self.assertEqual(res.status_code, 200)
        entry_data = res.get_json()
        self.assertTrue(entry_data["success"])
        ticket_code = entry_data["ticket"]["ticket_code"]

        # Scan QR token
        res = self.client.post("/api/admin/qr/scan", json={"token": f"SMARTPARK:{ticket_code}"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])

        # Simulate exit with bypass payment
        res = self.client.post("/api/exit/simulate", json={
            "ticket_code": ticket_code,
            "bypass_payment": True
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])

    def test_static_page_routing(self):
        """Test static page serving including extensionless URLs."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)

        res = self.client.get("/booking")
        self.assertEqual(res.status_code, 200)

        res = self.client.get("/payment")
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
