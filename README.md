# SMARTPARK - Smart Parking Management System

**SMARTPARK** is a software-only Smart Parking Management System engineered with Python, Supabase PostgreSQL, Data Structures & Algorithms (DSA), and modern Vanilla HTML5/CSS3/JavaScript, with server-validated QR entry, billing, and exit workflows.

---

## 🌟 Key Highlights & Academic Viva Guide

This project is architected for college demonstrations, technical vivas, and real-world deployment:

1. **Python Backend**: Built with Flask, SQLAlchemy ORM, and psycopg2.
2. **Supabase PostgreSQL database**: Pure relational database design (`database/schema.sql`) featuring foreign key constraints, `utf8mb4` encoding, indexes, and audit logs.
3. **Data Structures & Algorithms (DSA)**:
   - **Min-Heap Priority Queue (`heapq`)**: Achieves `O(log N)` nearest-slot allocation based on Euclidean/road distance from the entry gate and vehicle compatibility.
   - **Graph Representation & Dijkstra's Algorithm**: Generates turn-by-turn driving directions (`O((V + E) log V)`) from the Entry Gate to the allocated bay and from the bay to the Exit Gate.
   - **Hash Map Indexing**: Constant-time `O(1)` active session lookups by vehicle plate and digital QR token.
4. **Software-only operation**:
   - Runs without physical gate hardware. Entry and exit are completed through protected QR and database workflows.
5. **Security & Best Practices**:
   - Salted password hashing (PBKDF2/SHA256).
   - SQL injection immunization via SQLAlchemy ORM parameter binding.
   - Session authentication, CSRF protection, rate limiting, and server-side QR validation.
6. **Software-only operation**:
   - Entry and exit are performed through protected admin workflows; retired IoT routes return `410 Gone`.

---

## 📁 Project Directory Structure

```
SMARTPARK/
│
├── backend/
│   ├── app.py                  # Flask server entry point & static host
│   ├── wsgi.py                 # Production WSGI entry point (Gunicorn)
│   ├── config.py               # Configuration & dynamic tariff settings
│   ├── database.py             # SQLAlchemy models (User, ParkingSlot, Ticket, GateLog)
│   ├── routes.py               # REST APIs (Booking, ANPR, Billing, admin workflows)
│   ├── services.py             # DSA (Min-Heap, Dijkstra), ANPR & Billing logic
│   ├── requirements.txt        # Python dependencies (includes Gunicorn)
│   ├── Procfile                # Platform deployment config (Heroku/Render)
│   ├── production.env.example  # Production environment template
│   ├── .env.example            # Environment variable template (dev)
│   └── .env                    # Active configuration (dev)
│
├── frontend/
│   ├── index.html              # Public landing page & live bay availability map
│   ├── booking.html            # Customer pre-booking portal & QR pass generator
│   ├── payment.html            # Checkout, duration counter, dynamic pricing & payment gateway
│   ├── admin.html              # Admin dashboard, QR scanner & software testing console
│   ├── style.css               # Glassmorphism dark-theme design system
│   └── app.js                  # Single-page client logic & real-time polling
│
├── database/
│   └── schema.sql              # Supabase PostgreSQL schema
│
├── vercel.json                 # Vercel deployment configuration
├── README.md                   # Project documentation & viva guide
└── .gitignore
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.9+ (Python 3.10 / 3.11 / 3.12 supported)
- A Supabase project with PostgreSQL enabled

### 2. Database Setup

For ordinary local development, the test configuration uses an in-memory
SQLite database and creates tables for the test process. This is convenient for
UI/API development but does not validate PostgreSQL locking or migration
behavior.

For a real database, use an isolated PostgreSQL database and follow
[`migrations/README.md`](migrations/README.md). The destructive
`database/schema.sql` file is only for a disposable empty database. Existing
databases must use ordered migrations. The application never runs migrations
automatically at startup.

### 3. Backend Setup
1. Navigate to the `backend/` directory:
   ```bash
   cd backend
   ```
2. Create and activate a virtual environment (optional but recommended):
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```
3. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```
4. For local SQLite development, create `backend/.env` from the safe template
   and verify the development values:
   ```ini
   DATABASE_URL=sqlite:///smartpark_demo.db
   AUTO_CREATE_SCHEMA=1
   SESSION_COOKIE_SECURE=0
   FLASK_SECRET_KEY=CHANGE_ME
   ADMIN_PASSWORD=CHANGE_ME
   ```

   Set a non-default local admin username/password. Never use these values in
   production; production requires PostgreSQL and `AUTO_CREATE_SCHEMA=0`.
   `SESSION_COOKIE_SECURE=0` is for local HTTP only; keep it enabled for
   production HTTPS deployments.

### 4. Run the Server
Launch the unified Flask server:
```bash
cd backend
python app.py
```

The application will start on: **`http://localhost:5000`**

---

## 🚀 Production Deployment

For production deployments, use **Gunicorn** (production WSGI server) instead of Flask's development server, and connect to your Supabase PostgreSQL database.

### 1. Production Dependencies
```bash
pip install -r backend/requirements.txt
```

### 2. Configure Production Environment
Copy the production template and fill in secure values:
```bash
cp backend/production.env.example backend/.env
# Edit backend/.env with your actual secrets, database password, etc.
```

### 3. Supabase connection

For production, the Flask backend must be able to reach the Supabase PostgreSQL database. Do not use `localhost` for the production database.

### 4. Start with Gunicorn
```bash
cd backend
gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app
```

The app will be available at **`http://localhost:5000`**.

### 5. Deployment to Cloud Platforms
| Platform | Config | Notes |
| :--- | :--- | :--- |
| **Vercel** | `vercel.json` in project root | Deploys the frontend and Flask API as one project; configure production environment variables in Vercel |
| **Heroku / Render** | `backend/Procfile` | Set `BACKEND_URL` env var to your backend URL |
| **Railway** | `backend/requirements.txt` + `Procfile` | Auto-detects Python/Flask app |

### 6. Deploying to Vercel
1. Import the repository into Vercel with the project root as the deployment root.
2. Add the variables from `backend/production.env.example` in **Project Settings > Environment Variables**. At minimum, set `FLASK_SECRET_KEY`, `ADMIN_PASSWORD`, and `SUPABASE_DB_URL` (or `DATABASE_URL`).
3. Apply the reviewed SQL files in `migrations/` to staging and then production before deploying.
4. Set `AUTO_CREATE_SCHEMA=0`. Production startup validates connectivity only; it does not create or alter tables.

Vercel functions are stateless and can be restarted at any time, so production state must remain in Supabase. Do not use the local SQLite fallback in Vercel.

### PostgreSQL setup and migration safety

`database/schema.sql` is a destructive fresh-database script. It is not an
upgrade mechanism and must never be run against production or an existing
database. Existing installations must use the ordered files in `migrations/`.
Production startup does not run migrations.

For an isolated PostgreSQL test database, use the explicit harness documented
in [`migrations/README.md`](migrations/README.md). Set
`SMARTPARK_TEST_DATABASE_URL` only in the shell running the test, verify the
reported database identity, and set `SMARTPARK_DISPOSABLE_DB=1` only for a
database that can be destroyed. The harness records applied files in
`smartpark_migrations`, stops on SQL errors, and never prints the connection
URL. Do not use a production or shared Supabase URL for `fresh`.

`SUPABASE_URL`, `SUPABASE_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are server-side project configuration values. Automatic table creation specifically requires the PostgreSQL connection string in `SUPABASE_DB_URL`; Supabase API keys alone cannot run PostgreSQL DDL.

### 7. Running with Docker (Optional)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY frontend/ ../frontend/
ENV FLASK_ENV=production FLASK_DEBUG=0
EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "wsgi:app"]
```

### ⚠️ Important Security Notes
- **Always** configure strong, unique `FLASK_SECRET_KEY` and `ADMIN_PASSWORD` values before production
- **Never** commit `.env` files to version control (already excluded by `.gitignore`)
- Use a strong, random `FLASK_SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- For a separate frontend/backend deployment, configure the frontend's `/api` proxy to point at the backend service and set CORS for the frontend origin.

---

## 🖥️ Web Portals & Usage

SMARTPARK exposes two separate web experiences over the same Flask API and
Supabase database:

| Interface | Routes | Authentication | Scope |
| :--- | :--- | :--- | :--- |
| Customer website | `/customer`, `/booking`, `/payment` | Optional customer account via `/customer/login` and `/customer/register` | Availability, booking, QR pass, billing, demo payment, and exit |
| Admin website | `/admin`, `/admin/login`, `/admin/scanner` | Server-side administrator session, CSRF, role check, and database-backed rate limiting | Operations, real database analytics, QR scanning, and audit history |

Customer sessions are stored separately from administrator sessions. Admin APIs
require an authenticated session whose role is `ADMIN`; changing a URL or
hiding a button does not grant access. The customer registration flow creates
only `CUSTOMER` accounts and cannot access admin endpoints.

| Page | URL | Purpose |
| :--- | :--- | :--- |
| **Live Availability** | `http://localhost:5000/index.html` | Real-time 2D parking lot visualizer & tariffs |
| **Slot Booking** | `http://localhost:5000/booking.html` | Pre-book a slot & download instant QR entry pass |
| **Pay & Checkout** | `http://localhost:5000/payment.html` | Lookup ticket, calculate dynamic fees, and use the clearly labelled development payment workflow |
| **Admin Console** | `http://localhost:5000/admin` | Software-only dashboard for QR scanning, booked entry, walk-in allocation, payment, exit, active parking, and history |

---

## 💡 How to Demonstrate in a Viva / College Presentation

1. **Demonstrate the software-only flow**:
   - Open `/admin` and sign in.
   - Create a public booking from `/booking`, then keep its QR pass open.
   - In Admin, open **QR Scanner**, click **Start Scanner**, or enter the token manually.
   - Scan the QR at `/admin/scanner`; the first scan authorizes entry and allocates the reserved slot.
   - Use the payment page for the clearly labelled development payment workflow; successful server-side payment completes exit and releases the slot.
   - Confirm the bay is available again and the completed session remains in **History**.
2. **Demonstrate Real-Time Grid Updates**:
   - Open `index.html` in a second browser window.
   - Notice that slot `A-01` turned **RED (Occupied)** with the vehicle plate displayed in real-time.
3. **Demonstrate Dynamic Pricing & Payment**:
   - Open `payment.html`, type `KA-01-MJ-5021` or click the payment link.
   - Point out the itemized fare breakdown: Base Rate + Additional Hours + Peak Surge (1.25x if between 9-11 AM or 5-8 PM) + GST.
   - Select a development payment method. No real financial provider is integrated and no money is transferred.
4. **Demonstrate software exit release**:
   - After payment, open the Admin **Active Parking** section and click **Complete Paid Exit & Release Bay**.
   - The session is completed and the allocated bay turns back to **GREEN (Available)**.
5. **Demonstrate One-Click Reset**:
   - Click **"🔄 Reset Demo"** to restore all slots to available for the next reviewer.

---

## 🛡️ License
Academic & Educational Use Only. Built for demonstration and portfolio showcasing.
