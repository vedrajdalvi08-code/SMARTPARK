"""
SMARTPARK - Database Models & Supabase PostgreSQL Initialization
Relational database models using SQLAlchemy with Supabase PostgreSQL.
"""
import uuid
import datetime
import logging
import os
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    Numeric, ForeignKey, Enum as SQLEnum, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship
from werkzeug.security import generate_password_hash
from config import Config

logger = logging.getLogger("smartpark.database")
Base = declarative_base()

# ============================================================
# MODELS
# ============================================================

class User(Base):
    """User account model for Admins, Operators, and Customers."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(60), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    email = Column(String(120), unique=True, nullable=True)
    phone = Column(String(20), nullable=True)
    role = Column(SQLEnum("ADMIN", "OPERATOR", "CUSTOMER", name="user_role_enum"), default="CUSTOMER", nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    tickets = relationship("Ticket", back_populates="user")

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class ParkingSlot(Base):
    """
    Physical/Virtual Parking Slot model.
    'distance_from_entry' is the key weight used in DSA Min-Heap Priority Queue.
    """
    __tablename__ = "parking_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slot_number = Column(String(15), unique=True, nullable=False)
    floor_level = Column(Integer, default=0, nullable=False)
    zone = Column(String(10), default="A", nullable=False)
    slot_type = Column(
        SQLEnum("TWO_WHEELER", "COMPACT", "SUV", "EV", name="slot_type_enum"),
        default="COMPACT",
        nullable=False
    )
    distance_from_entry = Column(Integer, nullable=False, comment="Distance in meters from entry gate")
    status = Column(
        SQLEnum("AVAILABLE", "RESERVED", "OCCUPIED", "MAINTENANCE", name="slot_status_enum"),
        default="AVAILABLE",
        nullable=False
    )
    sensor_state = Column(
        SQLEnum("EMPTY", "DETECTED", name="sensor_state_enum"),
        default="EMPTY",
        nullable=False
    )
    active_vehicle_number = Column(String(25), nullable=True)
    last_updated = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    tickets = relationship("Ticket", back_populates="slot")

    def to_dict(self):
        return {
            "id": self.id,
            "slot_number": self.slot_number,
            "floor_level": self.floor_level,
            "zone": self.zone,
            "slot_type": self.slot_type,
            "distance_from_entry": self.distance_from_entry,
            "status": self.status,
            "sensor_state": self.sensor_state,
            "active_vehicle_number": self.active_vehicle_number,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None
        }


class Ticket(Base):
    """Parking Session Ticket tracking arrival, slot, billing, and checkout."""
    __tablename__ = "bookings_and_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_code = Column(String(64), unique=True, nullable=False, index=True)
    vehicle_number = Column(String(25), nullable=False, index=True)
    vehicle_type = Column(
        SQLEnum("TWO_WHEELER", "COMPACT", "SUV", "EV", name="ticket_vehicle_type_enum"),
        default="COMPACT",
        nullable=False
    )
    slot_id = Column(Integer, ForeignKey("parking_slots.id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", onupdate="CASCADE", ondelete="SET NULL"), nullable=True)
    booking_time = Column(DateTime, nullable=True)
    entry_time = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    exit_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, default=0)
    base_rate = Column(Numeric(10, 2), default=20.00, nullable=False)
    total_amount = Column(Numeric(10, 2), default=0.00, nullable=False)
    payment_status = Column(
        SQLEnum("PENDING", "PAID", "EXEMPT", "FAILED", name="payment_status_enum"),
        default="PENDING",
        nullable=False
    )
    payment_method = Column(
        SQLEnum("NONE", "CASH", "UPI_QR", "CREDIT_CARD", "FASTAG_SIM", name="payment_method_enum"),
        default="NONE",
        nullable=False
    )
    transaction_id = Column(String(100), nullable=True)
    status = Column(
        SQLEnum("ACTIVE", "COMPLETED", "CANCELLED", name="ticket_status_enum"),
        default="ACTIVE",
        nullable=False
    )
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    slot = relationship("ParkingSlot", back_populates="tickets")
    user = relationship("User", back_populates="tickets")

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_code": self.ticket_code,
            "vehicle_number": self.vehicle_number,
            "vehicle_type": self.vehicle_type,
            "slot_id": self.slot_id,
            "slot_number": self.slot.slot_number if self.slot else None,
            "floor_level": self.slot.floor_level if self.slot else 0,
            "zone": self.slot.zone if self.slot else "A",
            "booking_time": self.booking_time.strftime("%Y-%m-%d %H:%M:%S") if self.booking_time else None,
            "entry_time": self.entry_time.strftime("%Y-%m-%d %H:%M:%S") if self.entry_time else None,
            "exit_time": self.exit_time.strftime("%Y-%m-%d %H:%M:%S") if self.exit_time else None,
            "duration_minutes": self.duration_minutes,
            "base_rate": float(self.base_rate),
            "total_amount": float(self.total_amount),
            "payment_status": self.payment_status,
            "payment_method": self.payment_method,
            "transaction_id": self.transaction_id,
            "status": self.status,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None
        }


class GateLog(Base):
    """Audit log for Entry and Exit gate events and ANPR scans."""
    __tablename__ = "gate_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gate_type = Column(SQLEnum("ENTRY", "EXIT", name="gate_type_enum"), nullable=False)
    vehicle_number = Column(String(25), nullable=True)
    anpr_confidence = Column(Numeric(5, 2), default=0.00)
    trigger_source = Column(
        SQLEnum("MANUAL_SIM", "ESP32_CAM", "IR_SENSOR", "API", name="trigger_source_enum"),
        default="MANUAL_SIM",
        nullable=False
    )
    action_taken = Column(
        SQLEnum("GATE_OPENED", "ACCESS_DENIED", "SIMULATED_PASS", name="action_taken_enum"),
        default="GATE_OPENED",
        nullable=False
    )
    image_snapshot = Column(Text, nullable=True)
    notes = Column(String(255), nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "gate_type": self.gate_type,
            "vehicle_number": self.vehicle_number,
            "anpr_confidence": float(self.anpr_confidence or 0.0),
            "trigger_source": self.trigger_source,
            "action_taken": self.action_taken,
            "image_snapshot": self.image_snapshot,
            "notes": self.notes,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else None
        }


class SensorTelemetry(Base):
    """Real-time IR sensor & barrier servo telemetry logs."""
    __tablename__ = "sensor_telemetry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), nullable=False, index=True)
    sensor_type = Column(
        SQLEnum(
            "IR_ENTRY", "IR_ENTRY_PASS", "IR_EXIT", "IR_EXIT_PASS",
            "SERVO_BARRIER_ENTRY", "SERVO_BARRIER_EXIT", "SLOT_IR",
            name="sensor_type_enum"
        ),
        nullable=False
    )
    state_value = Column(String(50), nullable=False)
    voltage = Column(Numeric(4, 2), default=3.30)
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "sensor_type": self.sensor_type,
            "state_value": self.state_value,
            "voltage": float(self.voltage or 3.30),
            "recorded_at": self.recorded_at.strftime("%Y-%m-%d %H:%M:%S") if self.recorded_at else None
        }


class SystemConfig(Base):
    """Runtime key-value system settings."""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(50), unique=True, nullable=False)
    config_value = Column(String(255), nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "key": self.config_key,
            "value": self.config_value,
            "description": self.description
        }


# ============================================================
# DATABASE SESSION & INITIALIZATION
# ============================================================

engine = None
SessionLocal = None

def init_db(app=None):
    """
    Initializes the Supabase PostgreSQL database through SQLAlchemy.
    Supabase hosts PostgreSQL, so the Flask application connects directly
    using the Supabase database connection string.
    """
    global engine, SessionLocal

    database_url = Config.SQLALCHEMY_DATABASE_URI

    try:
        logger.info("Connecting to Supabase PostgreSQL database...")
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=300
        )

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        logger.info("Successfully connected to Supabase PostgreSQL.")
    except Exception as exc:
        logger.error("Supabase PostgreSQL connection failed: %s", exc)
        raise RuntimeError(
            "SMARTPARK could not connect to Supabase PostgreSQL. "
            "Check SUPABASE_DB_URL/DATABASE_URL and your Supabase database credentials."
        ) from exc

    SessionLocal = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )

    # SQLAlchemy creates/updates the initial application schema.
    Base.metadata.create_all(bind=engine)

    # Keep older deployments compatible with the booking_time field.
    inspector = __import__("sqlalchemy").inspect(engine)
    if inspector.has_table("bookings_and_tickets"):
        ticket_columns = {
            column["name"]
            for column in inspector.get_columns("bookings_and_tickets")
        }
        if "booking_time" not in ticket_columns:
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE bookings_and_tickets "
                    "ADD COLUMN booking_time TIMESTAMP NULL"
                ))

    seed_initial_data()
    return engine


def get_db():
    """Return a scoped SQLAlchemy database session."""
    session = SessionLocal()
    try:
        return session
    finally:
        pass


def seed_initial_data():
    """Populates initial slots and admin users if database is empty."""
    session = SessionLocal()
    try:
        # Check if slots exist
        slot_count = session.query(ParkingSlot).count()
        if slot_count == 0:
            logger.info("Seeding 20 initial parking slots across zones A, B, EV, and C...")
            slots_data = [
                # Zone A - Ground Floor Compact
                ("A-01", 0, "A", "COMPACT", 10),
                ("A-02", 0, "A", "COMPACT", 15),
                ("A-03", 0, "A", "COMPACT", 20),
                ("A-04", 0, "A", "COMPACT", 25),
                ("A-05", 0, "A", "COMPACT", 30),
                ("A-06", 0, "A", "COMPACT", 35),
                # Zone B - Ground Floor SUV & Two Wheeler
                ("B-01", 0, "B", "SUV", 18),
                ("B-02", 0, "B", "SUV", 24),
                ("B-03", 0, "B", "SUV", 32),
                ("B-04", 0, "B", "SUV", 40),
                ("B-05", 0, "B", "TWO_WHEELER", 12),
                ("B-06", 0, "B", "TWO_WHEELER", 16),
                ("B-07", 0, "B", "TWO_WHEELER", 22),
                ("B-08", 0, "B", "TWO_WHEELER", 28),
                # Zone EV - EV Charging Spots
                ("EV-01", 0, "EV", "EV", 14),
                ("EV-02", 0, "EV", "EV", 21),
                ("EV-03", 0, "EV", "EV", 29),
                ("EV-04", 0, "EV", "EV", 38),
                # Zone C - First Floor
                ("C-01", 1, "C", "COMPACT", 50),
                ("C-02", 1, "C", "SUV", 55),
            ]
            for num, floor, zone, stype, dist in slots_data:
                slot = ParkingSlot(
                    slot_number=num,
                    floor_level=floor,
                    zone=zone,
                    slot_type=stype,
                    distance_from_entry=dist,
                    status="AVAILABLE",
                    sensor_state="EMPTY"
                )
                session.add(slot)

        # Keep the seeded administrator aligned with the configured account.
        admin_user = session.query(User).filter_by(username=Config.ADMIN_USERNAME).first()
        if not admin_user:
            logger.info("Creating configured administrator account.")
            admin = User(
                username=Config.ADMIN_USERNAME,
                password_hash=generate_password_hash(Config.ADMIN_PASSWORD),
                full_name="System Administrator",
                email="admin@smartpark.local",
                phone="+91-9876543210",
                role="ADMIN"
            )
            session.add(admin)

        # Check default operator account
        operator_user = session.query(User).filter_by(username="operator").first()
        if not operator_user:
            operator = User(
                username="operator",
                password_hash=generate_password_hash("operator123"),
                full_name="Gate Operator",
                email="operator@smartpark.local",
                phone="+91-9876543211",
                role="OPERATOR"
            )
            session.add(operator)

        # Pre-seed initial pricing config
        configs = [
            ("demo_mode", "true", "Software simulation mode"),
            ("RATE_COMPACT_BASE", "20.00", "Compact vehicle base fee"),
            ("RATE_SUV_BASE", "30.00", "SUV vehicle base fee"),
            ("RATE_TWO_WHEELER_BASE", "10.00", "Two wheeler base fee"),
            ("RATE_EV_BASE", "25.00", "EV bay base fee"),
            ("PEAK_MULTIPLIER", "1.25", "Peak hour surge multiplier")
        ]
        for key, val, desc in configs:
            existing = session.query(SystemConfig).filter_by(config_key=key).first()
            if not existing:
                session.add(SystemConfig(config_key=key, config_value=val, description=desc))

        session.commit()
        logger.info("Database seed completed successfully.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error during database seed: {e}")
    finally:
        session.close()
