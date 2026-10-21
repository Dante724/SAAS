"""
Seed demo data for internal testing.
Runs once on startup — idempotent (checks if data already exists).
"""
from sqlalchemy import select
from backend.db.database import AsyncSessionLocal
from backend.models import (
    Organisation, Location, Role, Employee, Client, Device,
    Order, OrderStatusLog, Part, StockLevel, Task, Invoice,
    WorkOrderType, CustomField, NotificationTemplate, Service, Tax, Account, CashFlowItem
)
from backend.services.auth import hash_password
from scripts.demo_credentials import DEMO_FILE, demo_passwords
import uuid
from datetime import datetime, timedelta

def uid(): return str(uuid.uuid4())
def now(): return datetime.utcnow()

async def seed_demo_data():
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Organisation).limit(1))
        if existing.scalar_one_or_none():
            return  # Already seeded

        print("Seeding demo data...")

        # Org + location
        org = Organisation(id=uid(), name="Fix-It Pro Repairs", plan="pro")
        db.add(org)
        await db.flush()

        loc = Location(id=uid(), org_id=org.id, name="Main Shop",
                       address="123 Tech Street, Mumbai", phone="+91-9876543210")
        db.add(loc)
        loc2 = Location(id=uid(), org_id=org.id, name="Andheri Branch",
                        address="48 Link Road, Andheri West, Mumbai", phone="+91-9876500011")
        db.add(loc2)
        await db.flush()

        # Roles
        admin_role = Role(id=uid(), org_id=org.id, name="admin",
                          permissions={"orders": "write", "clients": "write",
                                       "inventory": "write", "finance": "write",
                                       "staff": "write", "reports": "write"})
        tech_role = Role(id=uid(), org_id=org.id, name="technician",
                         permissions={"orders": "write", "clients": "read",
                                      "inventory": "read", "finance": "none"})
        db.add(admin_role)
        db.add(tech_role)
        await db.flush()

        # Employees. Passwords come from the environment or .env.demo, never a published default
        # (launch QA F10 — see scripts/demo_credentials.py).
        admin_pw, staff_pw = demo_passwords()
        admin = Employee(id=uid(), org_id=org.id, location_id=loc.id,
                         role_id=admin_role.id, name="Rahul Sharma",
                         email="admin@repairos.dev",
                         password_hash=hash_password(admin_pw),
                         wage_type="fixed", wage_value=50000)
        tech1 = Employee(id=uid(), org_id=org.id, location_id=loc.id,
                         role_id=tech_role.id, name="Priya Patel",
                         email="priya@repairos.dev",
                         password_hash=hash_password(staff_pw),
                         wage_type="commission", commission_labour=10.0)
        tech2 = Employee(id=uid(), org_id=org.id, location_id=loc.id,
                         role_id=tech_role.id, name="Amit Singh",
                         email="amit@repairos.dev",
                         password_hash=hash_password(staff_pw),
                         wage_type="hourly", wage_value=250)
        db.add(admin); db.add(tech1); db.add(tech2)
        await db.flush()

        # Work order types + custom fields
        repair_type = WorkOrderType(id=uid(), org_id=org.id, name="Repair",
                                    color="#4f8ef7", initial_status="new",
                                    sort_order=0, is_default=True)
        warranty_type = WorkOrderType(id=uid(), org_id=org.id, name="Warranty claim",
                                      color="#9b7bf4", initial_status="new", sort_order=1)
        rental_type = WorkOrderType(id=uid(), org_id=org.id, name="Rental",
                                    color="#2cd4c0", initial_status="new", sort_order=2)
        db.add(repair_type); db.add(warranty_type); db.add(rental_type)
        await db.flush()

        # The problem itself is the order's built-in "Complaint / description" field; a second
        # "Fault reported" field asked the same thing twice (launch QA 2026-10-19).
        repair_fields = [
            ("Device passcode", "text", False, "If applicable", []),
            ("Accessories received", "select", False, None, ["None", "Charger", "Case", "Charger + Case"]),
            ("Data backup needed", "bool", False, None, []),
        ]
        warranty_fields = [
            ("Purchase date", "date", True, None, []),
            ("Invoice number", "text", True, "Original invoice #", []),
            ("Warranty provider", "select", True, None, ["Manufacturer", "Store", "Extended plan"]),
        ]
        rental_fields = [
            ("Rental period (days)", "number", True, "e.g. 7", []),
            ("Security deposit", "number", True, "Amount in ₹", []),
            ("ID proof type", "select", True, None, ["Aadhaar", "Passport", "Driving licence"]),
        ]
        for typ, fields in [(repair_type, repair_fields), (warranty_type, warranty_fields), (rental_type, rental_fields)]:
            for i, (label, ftype, req, ph, opts) in enumerate(fields):
                key = "".join(c.lower() if c.isalnum() else "_" for c in label).strip("_") + f"_{i}"
                db.add(CustomField(id=uid(), org_id=org.id, type_id=typ.id, entity_type="order",
                                   field_key=key, field_label=label, field_type=ftype,
                                   required=req, placeholder=ph, options=opts, sort_order=i))
        await db.flush()

        # Notification templates (unified {Variable} syntax — same resolver as documents)
        db.add(NotificationTemplate(
            id=uid(), org_id=org.id, channel="sms", trigger_event="status_change",
            trigger_status="done", name="Pickup reminder",
            body_template="Hi {Client Name}, your {Asset Brand} {Asset Model} (order {Order Number}) is complete at {Company Name}. Total: {Order Total}.",
            active=True))
        db.add(NotificationTemplate(
            id=uid(), org_id=org.id, channel="sms", trigger_event="status_change",
            trigger_status="in_repair", name="Repair started",
            body_template="Hi {Client Name}, we've started repairing your {Asset Brand} {Asset Model}. Order {Order Number} is now in progress. - {Company Name}",
            active=True))
        await db.flush()


        clients_data = [
            ("Neha Gupta", "+91-9111222333", "neha@email.com", "Corporate", "walk_in"),
            ("Vikram Reddy", "+91-9444555666", "vikram@email.com", "Standard", "referral"),
            ("Ananya Iyer", "+91-9777888999", "ananya@email.com", "VIP", "google_ads"),
            ("Saurabh Joshi", "+91-9000111222", None, "Standard", "walk_in"),
            ("Kavya Nair", "+91-9333444555", "kavya@email.com", "Standard", "instagram"),
        ]
        clients = []
        for name, phone, email, tier, source in clients_data:
            c = Client(id=uid(), org_id=org.id, name=name, phone=phone,
                       email=email, loyalty_tier=tier.lower(), source=source)
            db.add(c)
            clients.append(c)
        await db.flush()

        # Devices
        devices_data = [
            (clients[0].id, "Apple", "iPhone 14 Pro", "SN12345678", "355123456789012", "Space Black", "Good"),
            (clients[1].id, "Samsung", "Galaxy S23", "SN87654321", "355987654321098", "Phantom White", "Fair"),
            (clients[2].id, "Apple", "MacBook Pro 14", "SNMAC123456", None, "Silver", "Good"),
            (clients[3].id, "OnePlus", "11 Pro", "SNOP987654", "355111222333444", "Titan Black", "Poor"),
            (clients[4].id, "Apple", "iPad Air 5", "SNIPAD54321", None, "Starlight", "Good"),
        ]
        devices = []
        for cid, brand, model, serial, imei, color, cond in devices_data:
            d = Device(id=uid(), client_id=cid, brand=brand, model=model,
                       serial=serial, imei=imei, color=color, condition=cond)
            db.add(d)
            devices.append(d)
        await db.flush()

        # Parts
        parts_data = [
            ("iPhone 14 Pro Screen", "SCRN-IP14P", "Apple", "Screens", 4500, 8500, 5),
            ("Samsung S23 Battery", "BATT-SS23", "Samsung", "Batteries", 800, 1800, 10),
            ("iPhone Charging Port", "PORT-IPH", "Generic", "Ports", 300, 900, 8),
            ("MacBook Keyboard", "KB-MBP14", "Apple", "Keyboards", 3200, 6500, 3),
            ("USB-C Flex Cable", "FLEX-USBC", "Generic", "Cables", 150, 450, 15),
            ("iPad Screen", "SCRN-IPAD5", "Apple", "Screens", 3800, 7200, 4),
            ("Thermal Paste", "PASTE-TERM", "Arctic", "Consumables", 80, 200, 20),
        ]
        parts = []
        for name, sku, brand, cat, cost, sell, min_s in parts_data:
            p = Part(id=uid(), org_id=org.id, sku=sku, name=name,
                     brand=brand, category=cat, cost_price=cost,
                     sell_price=sell, min_stock=min_s)
            db.add(p)
            parts.append(p)
        await db.flush()

        # Supplier (for purchase orders)
        from backend.models import Supplier
        supplier = Supplier(id=uid(), org_id=org.id, name="TechParts Wholesale",
                            phone="+91 98200 11223", email="sales@techparts.example")
        db.add(supplier)
        await db.flush()
        # Stock levels
        for p in parts:
            qty = 12 if p.category == "Screens" else 25
            sl = StockLevel(id=uid(), part_id=p.id, location_id=loc.id,
                            qty_available=qty, qty_reserved=0)
            db.add(sl)
        await db.flush()

        # Services & Labors catalog
        services_data = [
            ("Screen replacement labour", "Repairs", 500, 0, 90),
            ("Battery replacement labour", "Repairs", 400, 0, 180),
            ("Diagnostics fee", "Diagnostics", 300, 0, 0),
            ("Water damage treatment", "Repairs", 1500, 200, 30),
            ("Software reinstall / setup", "Software", 800, 0, 0),
            ("Data recovery", "Software", 2500, 300, 0),
            ("Express service surcharge", "Other", 600, 0, 0),
        ]
        for nm, cat, price, cost, war in services_data:
            db.add(Service(id=uid(), org_id=org.id, name=nm, category=cat,
                           price=price, cost_price=cost, warranty_days=war))
        await db.flush()

        # Taxes (named, reusable)
        db.add(Tax(id=uid(), org_id=org.id, name="GST 18%", rate=18, mode="imposed", is_default=True))
        db.add(Tax(id=uid(), org_id=org.id, name="GST 12%", rate=12, mode="imposed"))
        db.add(Tax(id=uid(), org_id=org.id, name="GST 5%", rate=5, mode="imposed"))
        db.add(Tax(id=uid(), org_id=org.id, name="Tax-inclusive 18%", rate=18, mode="included"))
        await db.flush()

        # Cash accounts (cashboxes)
        db.add(Account(id=uid(), org_id=org.id, name="Main Cash Drawer", icon="wallet",
                       payment_option="cash", is_company=False, location_id=loc.id, balance=5000))
        db.add(Account(id=uid(), org_id=org.id, name="Bank Account", icon="bank",
                       payment_option="cashless", is_company=True, balance=125000))
        db.add(Account(id=uid(), org_id=org.id, name="Card Terminal", icon="card",
                       payment_option="card", is_company=False, location_id=loc.id,
                       balance=0, fee_pct=2.0))
        # Cash flow items (tags)
        for nm, dr, sysf in [("Sales","income",True),("Transaction fees","expense",True),
                             ("Rent","expense",False),("Payroll","expense",False),
                             ("Supplier payment","expense",False),("Utilities","expense",False),
                             ("Other income","income",False)]:
            db.add(CashFlowItem(id=uid(), org_id=org.id, name=nm, direction=dr, is_system=sysf))
        await db.flush()

        # Work-order statuses (configurable, grouped, with triggers)
        from backend.routers.statuses import DEFAULT_STATUSES, ESTIMATE_STATUSES
        from backend.models import OrderStatus
        for i, (skey, sname, grp, color, dflt, t_inv, t_not, t_pay, term) in enumerate(DEFAULT_STATUSES):
            db.add(OrderStatus(id=uid(), org_id=org.id, key=skey, name=sname, status_group=grp,
                               color=color, sort_order=i, is_default=dflt, triggers_invoice=t_inv,
                               triggers_notification=t_not, triggers_payment=t_pay, is_terminal=term))
        # Estimate statuses (separate set; est_converted auto-converts to a work order)
        for i, (skey, sname, grp, color, accept, conv) in enumerate(ESTIMATE_STATUSES):
            db.add(OrderStatus(id=uid(), org_id=org.id, key=skey, name=sname, status_group=grp,
                               color=color, sort_order=i, is_estimate=True,
                               is_default=(skey == "est_draft"),
                               shows_accept=accept, converts_to_order=conv,
                               is_terminal=grp in ("closed", "dropped"),
                               triggers_notification=(skey == "est_sent")))
        await db.flush()



        statuses = ["new", "diagnosed", "waiting_parts", "in_repair", "done", "delivered"]
        orders_data = [
            (clients[0].id, devices[0].id, "Screen cracked after drop", statuses[4], tech1.id, 8500 + 900, 8500, 900),
            (clients[1].id, devices[1].id, "Battery drains fast — 20% to 0 in 2 hrs", statuses[2], tech2.id, 1800, 0, 1800),
            (clients[2].id, devices[2].id, "Keyboard keys not responding", statuses[1], tech1.id, 6500 + 500, 6500, 500),
            (clients[3].id, devices[3].id, "Phone not charging at all", statuses[3], tech2.id, 900 + 300, 900, 300),
            (clients[4].id, devices[4].id, "Screen flickering and touch not working", statuses[0], None, 7200 + 800, 7200, 800),
        ]
        orders = []
        _today = now().replace(hour=9, minute=0, second=0, microsecond=0)
        _slots = [
            (tech1.id, _today.replace(hour=10), 120),
            (tech2.id, _today.replace(hour=9), 180),
            (tech1.id, _today.replace(hour=14), 90),
            (tech2.id, _today.replace(hour=13), 60),
            (None,     _today.replace(hour=16), 60),
        ]
        for i, (cid, did, complaint, status, tech_id, total, parts_t, labour_t) in enumerate(orders_data):
            _sched_tech, _sched_when, _sched_dur = _slots[i] if i < len(_slots) else (tech_id, None, 60)
            o = Order(
                id=uid(),
                order_number=f"WO-{i+1:05d}",
                org_id=org.id, client_id=cid, device_id=did,
                location_id=loc.id, assigned_to_id=tech_id,
                status=status, priority=i % 3,
                complaint=complaint,
                labour_total=labour_t, parts_total=parts_t, total=total,
                scheduled_for=_sched_when if status not in ("delivered",) else None,
                duration_min=_sched_dur,
                created_at=now() - timedelta(days=i * 2),
                closed_at=now() - timedelta(days=1) if status == "delivered" else None,
            )
            db.add(o)
            db.add(OrderStatusLog(id=uid(), order_id=o.id, from_status=None,
                                  to_status="new", changed_by_id=admin.id))
            if status != "new":
                db.add(OrderStatusLog(id=uid(), order_id=o.id, from_status="new",
                                      to_status=status, changed_by_id=admin.id))
            orders.append(o)
        await db.flush()

        # Sample invoice for first (done) order
        inv = Invoice(
            id=uid(), invoice_number="INV-00001",
            org_id=org.id, order_id=orders[0].id, client_id=clients[0].id,
            status="paid", subtotal=9400, discount=0, tax=0, total=9400,
            amount_paid=9400, issued_at=now() - timedelta(days=1),
        )
        db.add(inv)

        # Tasks
        tasks_data = [
            ("Order Samsung S23 battery from supplier", tech1.id, orders[1].id, "todo", "urgent"),
            ("Call Neha Gupta about screen replacement", admin.id, None, "in_progress", "normal"),
            ("Clean workbench and tools", tech2.id, None, "todo", "low"),
            ("Update MacBook keyboard stock count", admin.id, None, "done", "normal"),
        ]
        for title, emp_id, order_id, status, priority in tasks_data:
            t = Task(id=uid(), org_id=org.id, title=title,
                     assigned_to_id=emp_id, created_by_id=admin.id,
                     order_id=order_id, status=status, priority=priority,
                     due_at=now() + timedelta(days=2) if status != "done" else None,
                     completed_at=now() if status == "done" else None)
            db.add(t)

        await db.commit()

        # Phase G seed: location resources (bays / workbench)
        from backend.models import Resource as _Res
        for i, (rname, rkind, rcolor) in enumerate([
            ("Bay 1", "bay", "#4f8ef7"), ("Bay 2", "bay", "#2cd4c0"),
            ("Workbench", "equipment", "#9b7bf4")]):
            db.add(_Res(id=uid(), org_id=org.id, location_id=loc.id, name=rname,
                        kind=rkind, color=rcolor, sort_order=i))
        await db.commit()

        await db.flush()

        # Bin-level storage (Orderry "bin locations"): a few demo bins in Main Shop
        from backend.models import StorageBin as _Bin
        for nm, zone, shelf in [("A1", "Front", "A"), ("A2", "Front", "A"), ("B1", "Back", "B")]:
            db.add(_Bin(id=uid(), org_id=org.id, location_id=loc.id, name=nm,
                        zone=zone, shelf=shelf, bin_number=nm[-1], max_weight_capacity=50.0))
        await db.flush()

        # Phase E/F seeds: feedback config + a starter work-order document template
        from backend.models import FeedbackSettings, DocumentTemplate, OrderStatus as _OS
        done_status = (await db.execute(
            select(_OS).where(_OS.org_id == org.id, _OS.key == "done"))).scalar_one_or_none()
        db.add(FeedbackSettings(
            id=uid(), org_id=org.id, system="five_point",
            trigger_status_id=done_status.id if done_status else None,
            heading="How did we do?", question="How would you rate your repair experience?",
            clarify_question="Anything we could improve?",
            bands={"low": "We're sorry — a manager will reach out shortly.",
                   "mid": "Thanks! What would have made it a 5?",
                   "high": "Thank you! We'd love a Google review."},
            active=True))
        db.add(DocumentTemplate(
            id=uid(), org_id=org.id, name="Work order receipt", doc_type="work_order",
            is_default=True, auto_print_on="close",
            body_html=(
                "<div style='font-family:Inter,sans-serif;max-width:640px;margin:auto'>"
                "<h2>{Company Name}</h2>"
                "<p><b>Work order:</b> {Order Number} &nbsp; <b>Status:</b> {Order Status}</p>"
                "<p><b>Client:</b> {Client Name} &nbsp; {Client Phone}</p>"
                "<p><b>Asset:</b> {Asset Brand} {Asset Model} &nbsp; SN: {Asset Serial Number}</p>"
                "<p><b>Complaint:</b> {Order Complaint}</p>"
                "{Items Table}"
                "<p style='text-align:right'><b>Total:</b> {Order Total} &nbsp; "
                "<b>Paid:</b> {Order Paid} &nbsp; <b>Balance:</b> {Order Balance Due}</p>"
                "<p><b>Warranty until:</b> {Order Warranty Expiration Date}</p>"
                "<p><b>Technician:</b> {Technician}</p>"
                "<div style='margin-top:16px'>{Barcode}</div></div>")))
        await db.commit()
        await db.commit()

        # Mandate 1: demo commission rules — 10% of labor + 15% of product margin
        from backend.models import CommissionRule as _CR
        from decimal import Decimal as _D
        db.add(_CR(id=uid(), org_id=org.id, name="Labor commission", event="labor_performed",
                   calc_type="percent", percent_basis="total", amount=_D("10"), coefficient=_D("1")))
        db.add(_CR(id=uid(), org_id=org.id, name="Parts margin commission", event="product_sold",
                   calc_type="percent", percent_basis="margin", amount=_D("15"), coefficient=_D("1")))
        await db.commit()

        # Phase 1: demo client notification — SMS when an order reaches "Ready"
        from backend.models import NotificationTemplate as _NT
        db.add(_NT(id=uid(), org_id=org.id, channel="sms", trigger_event="status_change",
                   trigger_status="ready", name="Ready for pickup",
                   subject=None,
                   body_template="Hi {Client Name}, your {Asset Brand} {Asset Model} (order {Order Number}) is ready for pickup at {Company Name}. Total: {Order Total}."))
        db.add(_NT(id=uid(), org_id=org.id, channel="email", trigger_event="order_created",
                   trigger_status=None, name="Order received",
                   subject="We received your device — {Order Number}",
                   body_template="Hi {Client Name}, we've logged your {Asset Brand} {Asset Model} as order {Order Number}. We'll keep you posted. — {Company Name}"))
        await db.commit()

        # Phase 1 front door: demo inquiries with SLA timers
        from backend.models import Lead as _Lead
        from datetime import timedelta as _tdl
        db.add(_Lead(id=uid(), org_id=org.id, location_id=loc.id, name="Rohit Malhotra",
                     phone="+91 98100 11223", source="whatsapp",
                     inquiry="MacBook Air not charging, asking for diagnostic cost",
                     status="new", manager_id=admin.id,
                     sla_due_at=datetime.utcnow() + _tdl(minutes=45)))
        db.add(_Lead(id=uid(), org_id=org.id, location_id=loc.id, name="Sneha Kulkarni",
                     phone="+91 98220 44556", source="instagram",
                     inquiry="Quote for iPad screen replacement",
                     status="in_progress", manager_id=admin.id,
                     sla_due_at=datetime.utcnow() - _tdl(minutes=20)))
        await db.commit()

        # Pricing foundation: price types (markups over cost) + a demo client discount
        from backend.models import PriceType as _PT
        pt_retail = _PT(id=uid(), org_id=org.id, name="Retail", markup_pct=100.0, is_default=True)
        pt_whole = _PT(id=uid(), org_id=org.id, name="Wholesale", markup_pct=60.0)
        db.add(pt_retail); db.add(pt_whole)
        await db.flush()   # price types must exist before a client references one (FK)
        # Neha gets 10% off materials; Vikram buys at Wholesale prices for materials
        clients[0].disc_materials_mode = "percent"; clients[0].disc_materials_value = 10.0
        clients[1].disc_materials_mode = "price_type"; clients[1].disc_materials_price_type_id = pt_whole.id
        await db.commit()

        # demo bundle + chat thread
        from backend.models import Bundle as _B, BundleComponent as _BC, ChatThread as _CT, ChatMessage as _CM
        bdl = _B(id=uid(), org_id=org.id, name="Screen Replacement Kit", price_mode="fixed", price=4500.0)
        db.add(bdl)
        await db.flush()
        from sqlalchemy import select as _sel
        from backend.models import Service as _SvcM
        _first_svc = (await db.execute(_sel(_SvcM).where(_SvcM.org_id == org.id))).scalars().first()
        db.add(_BC(id=uid(), bundle_id=bdl.id, ref_type="service", ref_id=_first_svc.id, qty=1))
        db.add(_BC(id=uid(), bundle_id=bdl.id, ref_type="product", ref_id=parts[0].id, qty=1))
        th = _CT(id=uid(), org_id=org.id, channel="whatsapp", contact_phone=clients[0].phone,
                 contact_name=clients[0].name, client_id=clients[0].id, last_message_at=now(),
                 status="replied", last_inbound_at=now(), last_outbound_at=now())
        db.add(th)
        await db.flush()   # thread row must exist before its messages (FK)
        db.add(_CM(id=uid(), thread_id=th.id, direction="in", body="Hi! My MacBook screen is flickering, can you take a look this week?", status="received"))
        db.add(_CM(id=uid(), thread_id=th.id, direction="out", body="Of course! Bring it in any day before 6pm, diagnostics are free.", status="sent"))
        await db.commit()
        print(f"Demo data seeded. Login: admin@repairos.dev (passwords: $DEMO_ADMIN_PASSWORD/$DEMO_STAFF_PASSWORD or {DEMO_FILE.name})")
