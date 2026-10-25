"""
Tekly — all database models.
31 tables covering the full Orderry feature set.
"""
import secrets
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from backend.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())

def _now() -> datetime:
    return datetime.utcnow()

# ─────────────────────────────────────────────
# ORGANISATION & LOCATIONS
# ─────────────────────────────────────────────

class Organisation(Base):
    __tablename__ = "organisations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(50), default="trial")
    brand_primary_color: Mapped[Optional[str]] = mapped_column(String(9))     # #RRGGBB white-label accent
    brand_custom_domain: Mapped[Optional[str]] = mapped_column(String(120))   # portal.acme.com
    brand_email_from: Mapped[Optional[str]] = mapped_column(String(160))      # "Acme <no-reply@acme.com>"
    brand_hide_powered_by: Mapped[bool] = mapped_column(Boolean, default=False)
    subdomain: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    # The employee who created the shop. Only they can change their own account (password,
    # 2-step, email, role, access) — a teammate with equal permissions cannot. Not a foreign
    # key: organisations <-> employees would otherwise reference each other.
    owner_id: Mapped[Optional[str]] = mapped_column(String)
    # General / company profile (Settings > General > Company info)
    company_email: Mapped[Optional[str]] = mapped_column(String(150))
    company_phone: Mapped[Optional[str]] = mapped_column(String(50))
    company_address: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(String(2))  # build 123: ISO 3166-1 alpha-2 country code
    # Build 144cg: captured at signup (Orderry-style onboarding).
    region: Mapped[Optional[str]] = mapped_column(String(20))       # us | ca | eu | mena | other (data-residency grouping)
    employee_count: Mapped[Optional[str]] = mapped_column(String(20))  # bracket, e.g. "1", "2-5", "6-20", "21-50", "50+"
    # Build 133: inventory settings (Settings > Inventory). All nullable → app coalesces
    # to sensible defaults; keeps the migration safe on populated tables.
    inv_enable_bins: Mapped[Optional[bool]] = mapped_column(Boolean)
    inv_unique_identifier: Mapped[Optional[str]] = mapped_column(String(10))   # name|sku|barcode|code
    inv_enable_reservations: Mapped[Optional[bool]] = mapped_column(Boolean)
    inv_po_to_job: Mapped[Optional[str]] = mapped_column(String(10))   # ADR-144po: "auto" (default) | "manual" — parts arriving on a job's PO
    inv_reservation_max_days: Mapped[Optional[int]] = mapped_column(Integer)
    inv_product_serial_template: Mapped[Optional[str]] = mapped_column(String(40))
    inv_asset_serial_template: Mapped[Optional[str]] = mapped_column(String(40))
    # ii144a: code generator (Settings > Inventory > Product codes). Empty = built-in default.
    inv_sku_template: Mapped[Optional[str]] = mapped_column(String(40))      # empty = SKUs stay as typed
    inv_bin_template: Mapped[Optional[str]] = mapped_column(String(40))      # default BIN-{C:4}
    inv_barcode_prefix: Mapped[Optional[str]] = mapped_column(String(2))     # GS1 in-store 20–29, default 20
    inv_default_product_type: Mapped[Optional[str]] = mapped_column(String(30))
    inv_default_vehicle_type: Mapped[Optional[str]] = mapped_column(String(30))
    company_details: Mapped[Optional[str]] = mapped_column(Text)  # registration / tax ID
    company_note: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(String(150))
    logo_data: Mapped[Optional[str]] = mapped_column(Text)  # data-URI logo
    currency: Mapped[str] = mapped_column(String(8), default="₹")
    currency_code: Mapped[str] = mapped_column(String(8), default="INR")
    # Build 121: document language + multi-currency support
    document_language: Mapped[Optional[str]] = mapped_column(String(8), default="en")
    currencies_json: Mapped[Optional[dict]] = mapped_column(JSON)   # [{code,symbol,rate,primary}]
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Kolkata")
    industry_template: Mapped[str] = mapped_column(String(30), default="generic")  # renames the asset entity app-wide (Orderry-style)
    # Build 144al: customizable purchase-order charge types (labels the buyer can add to a PO)
    po_charge_types: Mapped[Optional[list]] = mapped_column(JSON)  # e.g. ["Shipping","In-transit","Customs"]
    # Build 144ah — POs whose total exceeds this need a manager (role with can_manage_settings)
    # to send. 0/None disables the gate.
    po_approval_threshold: Mapped[Optional[float]] = mapped_column(Float)
    payment_provider: Mapped[str] = mapped_column(String(20), default="auto")  # auto|stripe|square|sumup|simulated
    loyalty_earn_rate: Mapped[float] = mapped_column(Float, default=0.0)   # points per 1.00 paid; 0 = program off
    loyalty_point_value: Mapped[float] = mapped_column(Float, default=0.0)  # currency value of 1 point at redemption
    price_type_tickets_id: Mapped[Optional[str]] = mapped_column(String)   # default price list per doc type
    price_type_sales_id: Mapped[Optional[str]] = mapped_column(String)
    price_type_invoices_id: Mapped[Optional[str]] = mapped_column(String)
    price_decimals: Mapped[int] = mapped_column(Integer, default=2)
    # 144gy — margin-floor guardrail: warn/block a line whose margin falls below this.
    min_margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    margin_guard: Mapped[str] = mapped_column(String, default="off")   # off | warn | block
    # 144hd — line discounts above this % need can_approve_discount (or manager). 0 = off.
    discount_approval_pct: Mapped[float] = mapped_column(Float, default=0.0)
    contact_consent_phone_default: Mapped[bool] = mapped_column(Boolean, default=True)
    contact_consent_email_default: Mapped[bool] = mapped_column(Boolean, default=True)
    prohibit_dup_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    prohibit_dup_email: Mapped[bool] = mapped_column(Boolean, default=False)
    prevent_backdating_invoices: Mapped[bool] = mapped_column(Boolean, default=False)
    default_due_days: Mapped[int] = mapped_column(Integer, default=0)
    default_comment_visibility: Mapped[str] = mapped_column(String(10), default="private")
    default_file_visibility: Mapped[str] = mapped_column(String(10), default="private")
    warranty_count_from: Mapped[str] = mapped_column(String(10), default="closure")  # closure|created
    asset_one_open_ticket: Mapped[bool] = mapped_column(Boolean, default=False)
    # Several items per job (tools / equipment / device shops only — see services/multi_asset.py).
    multi_asset_jobs: Mapped[bool] = mapped_column(Boolean, default=False)
    rounding_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    currency_display_style: Mapped[str] = mapped_column(String(10), default="symbol")  # symbol|code|iso
    currency_position: Mapped[str] = mapped_column(String(10), default="before")  # before|after
    # Build 144cd: org-wide security policy. When on, every member must set up an
    # authenticator (TOTP) before using the app, and can't turn their own 2FA off.
    require_mfa: Mapped[bool] = mapped_column(Boolean, default=False)   # opt-in: owners enable it
    # Build 144ce: subscription lifecycle, controlled from the separate /admin portal.
    # sub_status drives the portal's segments: trial | active | trial_lost | churned | suspended
    # ("new" is derived = trial started within 14 days; "paid" = active; "lost paid" = churned).
    sub_status: Mapped[str] = mapped_column(String(20), default="trial")
    sub_start: Mapped[Optional[date]] = mapped_column(Date)
    sub_end: Mapped[Optional[date]] = mapped_column(Date)     # term end; past this the tenant is locked
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)   # manual per-account discount %
    admin_notes: Mapped[Optional[str]] = mapped_column(Text)          # platform-admin notes (never shown to tenant)
    # ── Time clocks (build 144x): org master switches. Effective clock for an
    # employee = org switch ON *and* their per-employee track_* flag ON. ──
    clock_wo_enabled: Mapped[bool] = mapped_column(Boolean, default=True)          # job/task labour timer
    clock_attendance_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # shop clock-in/out
    # optional: when on, stopping a job timer on an order adds/updates a labour line
    # priced by tracked time × the employee's billable rate (Orderry "time → service").
    clock_labour_billing: Mapped[bool] = mapped_column(Boolean, default=False)
    # ── Public pages / online appointment config (Orderry "Public pages") ──
    appointment_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    page_color: Mapped[str] = mapped_column(String(16), default="#3b6fe0")
    page_radius: Mapped[int] = mapped_column(Integer, default=8)
    page_show_language: Mapped[bool] = mapped_column(Boolean, default=True)
    page_show_logo: Mapped[bool] = mapped_column(Boolean, default=True)
    appointment_time_slot: Mapped[int] = mapped_column(Integer, default=60)
    appointment_allow_multiple: Mapped[bool] = mapped_column(Boolean, default=True)
    appointment_consider_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    # ── Build 144bo: availability engine policy ──
    appointment_lead_min: Mapped[int] = mapped_column(Integer, default=0)        # min minutes before earliest bookable slot
    appointment_window_days: Mapped[int] = mapped_column(Integer, default=30)    # how far ahead a client may book
    appointment_theme: Mapped[int] = mapped_column(Integer, default=1)           # client wizard skin 1..4
    page_background_pattern: Mapped[str] = mapped_column(String(20), default="none")  # 1a: none|topo|gears|dots|waves
    oe_show_logo: Mapped[bool] = mapped_column(Boolean, default=True)                 # 1b order/estimate header toggles
    oe_logo_size: Mapped[str] = mapped_column(String(6), default="big")              # small|big
    oe_show_name: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_show_address: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_show_email: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_show_description: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_separate_tables: Mapped[bool] = mapped_column(Boolean, default=False)
    oe_show_zero_price_products: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_show_zero_price_services: Mapped[bool] = mapped_column(Boolean, default=True)
    # 144bb — public-page feature switches (estimate approval, customer messaging)
    oe_allow_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_allow_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    oe_msg_days_after_close: Mapped[int] = mapped_column(Integer, default=14)  # customer messages stay open N days after closing
    oe_allow_recommend: Mapped[bool] = mapped_column(Boolean, default=True)  # 144bd: customer approves recommended services
    oe_allow_staff_reply: Mapped[bool] = mapped_column(Boolean, default=True)  # 144be: staff can reply to customers on the public page
    oe_show_scheduled: Mapped[bool] = mapped_column(Boolean, default=False)  # 144dr: show "Scheduled for" on the track page
    book_show_employee_step: Mapped[bool] = mapped_column(Boolean, default=True)  # 144dr: online appointment asks the client to pick an employee
    # 144ih — accept/decline gating (Orderry parity): which statuses expose sign, per doc type.
    # Comma-separated status keys; empty = any status. Work orders are opt-in.
    oe_wo_allow_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    oe_approval_statuses: Mapped[Optional[str]] = mapped_column(String(500))       # estimate: trigger statuses (comma keys)
    oe_wo_approval_statuses: Mapped[Optional[str]] = mapped_column(String(500))    # work order: trigger statuses
    # 144ii — Orderry-exact accept/decline transitions + require-signature, per doc type.
    oe_estimate_require_signature: Mapped[bool] = mapped_column(Boolean, default=False)
    oe_wo_require_signature: Mapped[bool] = mapped_column(Boolean, default=False)
    oe_estimate_accept_status: Mapped[Optional[str]] = mapped_column(String(50))
    oe_estimate_decline_status: Mapped[Optional[str]] = mapped_column(String(50))
    oe_wo_accept_status: Mapped[Optional[str]] = mapped_column(String(50))
    oe_wo_decline_status: Mapped[Optional[str]] = mapped_column(String(50))
    # 144dr — invoice loop: auto-draft an invoice when a work order closes, and the default line-grouping mode.
    auto_draft_invoice_on_close: Mapped[bool] = mapped_column(Boolean, default=False)
    default_invoice_display: Mapped[str] = mapped_column(String(12), default="detailed")  # detailed|simplified|one_line
    cash_category_tickets_id: Mapped[Optional[str]] = mapped_column(String)           # 2b: default payment category per doc type
    cash_category_sales_id: Mapped[Optional[str]] = mapped_column(String)
    # Build 110: shared secret for inbound provider webhooks (inbox, comms). Generated at
    # registration; rotatable from Settings -> API. Inbound posts must present it.
    webhook_secret: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    locations: Mapped[list["Location"]] = relationship(back_populates="org")
    employees: Mapped[list["Employee"]] = relationship(back_populates="org")

class Location(Base):
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    receipt_header: Mapped[Optional[str]] = mapped_column(Text)
    price_type_id: Mapped[Optional[str]] = mapped_column(String)
    legal_entity_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("legal_entities.id"))  # overrides org default at this location
    working_hours: Mapped[Optional[dict]] = mapped_column(JSON)  # build 136: {mon:{open,close,closed}, ...}
    # Build 144cm: stable creation order so listings are deterministic (the primary
    # "Main location" created at signup always sorts first). Fixes a flaky ordering
    # bug where /locations returned rows in random UUID order.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    org: Mapped["Organisation"] = relationship(back_populates="locations")
    orders: Mapped[list["Order"]] = relationship(back_populates="location")
    employees: Mapped[list["Employee"]] = relationship(back_populates="location")
    stock_levels: Mapped[list["StockLevel"]] = relationship(back_populates="location")
    order_statuses: Mapped[list["OrderStatus"]] = relationship(back_populates="location")

# ─────────────────────────────────────────────
# STAFF & AUTH
# ─────────────────────────────────────────────

class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(100))
    location_scope: Mapped[str] = mapped_column(String(10), default="all")  # all|selected
    location_ids: Mapped[Optional[str]] = mapped_column(Text)  # JSON list when scope=selected
    # ── Build 83: record-level scoping (Orderry "access conditions") ──────────
    contact_scope: Mapped[str] = mapped_column(String(10), default="all")   # all|own (own = clients I manage)
    order_scope: Mapped[str] = mapped_column(String(10), default="all")     # all|own (own = orders assigned to me)
    warehouse_scope: Mapped[str] = mapped_column(String(10), default="all")  # all|selected
    warehouse_ids: Mapped[Optional[dict]] = mapped_column(JSON)   # list of location ids when scope=selected
    account_ids: Mapped[Optional[dict]] = mapped_column(JSON)     # cash accounts this role may see (null = all)
    payroll_visibility_days: Mapped[Optional[int]] = mapped_column(Integer)  # how far back own-compensation is visible
    # E3: whose payroll this role may see and manage (own | all | selected); selected = payroll_employee_ids
    payroll_scope: Mapped[str] = mapped_column(String(10), default="all", server_default="all")
    payroll_employee_ids: Mapped[Optional[list]] = mapped_column(JSON)
    refund_ceiling: Mapped[Optional[Numeric]] = mapped_column(Numeric(12, 2))  # Build 108: above this needs approval
    # A5: per-status VIEW ACL — order/estimate status keys this role may NOT see
    # (deny-wins, default-allow). Complements the existing transition (set) ACL.
    hidden_status_keys: Mapped[Optional[dict]] = mapped_column(JSON)  # list of OrderStatus.key
    home_feed_sources: Mapped[Optional[dict]] = mapped_column(JSON)   # list of feed signal keys this role sees on Home (null/empty = all)
    home_route: Mapped[Optional[str]] = mapped_column(String(40))     # landing page for this role (e.g. "/my-work"); null = Home ("/")
    permissions: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    # True once every permission is written out explicitly (allow-list). Explicit roles are
    # default-deny: a permission added to the catalogue later stays off until someone turns
    # it on. Legacy roles (False) keep default-allow until snapshotted (services/permissions).
    perms_explicit: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    employees: Mapped[list["Employee"]] = relationship(back_populates="role")


class EmployeeGroup(Base):
    """Build 83 — a named group of employees, separate from roles. Roles grant
    permissions; groups are used for assignment and notification targeting."""
    __tablename__ = "employee_groups"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    member_ids: Mapped[Optional[dict]] = mapped_column(JSON, default=list)  # employee ids
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

class Employee(Base):
    __tablename__ = "employees"
    # Sign-in emails are case-insensitive (migration ls144r3); the app stores them lowercase.
    __table_args__ = (Index("uq_employees_email_lower", text("lower(email)"), unique=True),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    role_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("roles.id"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    pin_hash: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    wage_type: Mapped[str] = mapped_column(String(20), default="fixed")  # fixed|hourly|commission
    wage_value: Mapped[float] = mapped_column(Float, default=0.0)
    hourly_cost_rate: Mapped[float] = mapped_column(Float, default=0.0)   # labor cost per hour (time clock)
    billable_rate: Mapped[float] = mapped_column(Float, default=0.0)      # charge-out rate per hour
    commission_labour: Mapped[float] = mapped_column(Float, default=0.0)
    commission_parts: Mapped[float] = mapped_column(Float, default=0.0)
    weekly_hours: Mapped[Optional[float]] = mapped_column(Float)   # build 144: expected working hours/week
    # build 144i: which clock drives hourly pay — labour hours (time on work
    # orders) or shop attendance (clock-in/out shifts). null = labour (default).
    hourly_basis: Mapped[Optional[str]] = mapped_column(String(16))
    # build 144j: which time clocks are visible to this employee. Both on by default.
    track_job_hours: Mapped[bool] = mapped_column(Boolean, default=True)      # work-order labour timer
    track_attendance: Mapped[bool] = mapped_column(Boolean, default=True)     # shop clock-in / clock-out
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    hired_at: Mapped[Optional[date]] = mapped_column(Date)
    # build 144k: richer employee profile (Orderry General tab)
    position: Mapped[Optional[str]] = mapped_column(String(120))    # job title, e.g. "Technician"
    extension: Mapped[Optional[str]] = mapped_column(String(20))    # B7: telephony extension for incoming-call routing
    tax_id: Mapped[Optional[str]] = mapped_column(String(60))       # Employee ITN / tax number
    public_name: Mapped[Optional[str]] = mapped_column(String(200))  # name shown on public documents
    note: Mapped[Optional[str]] = mapped_column(Text)              # internal note
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    must_reset_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))  # H1: still on the old default password
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(64))          # base32 TOTP secret
    mfa_recovery_codes: Mapped[Optional[str]] = mapped_column(Text)        # JSON list of hashed single-use codes
    mfa_last_step: Mapped[Optional[int]] = mapped_column(Integer)          # last accepted TOTP time step (replay guard)  # bump to revoke all issued tokens (lost-device logout)
    # Build 144cb: email verification. Existing users default unverified; verification
    # is enforced only where the org opts in, so this never locks out current logins.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Build 144ce: optional access window set from the /admin portal. Outside it the
    # user can't authenticate (both null = always allowed).
    access_start: Mapped[Optional[date]] = mapped_column(Date)
    access_end: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    org: Mapped["Organisation"] = relationship(back_populates="employees")
    location: Mapped[Optional["Location"]] = relationship(back_populates="employees")
    role: Mapped[Optional["Role"]] = relationship(back_populates="employees")
    assigned_orders: Mapped[list["Order"]] = relationship(back_populates="assigned_to", foreign_keys="Order.assigned_to_id")
    tasks: Mapped[list["Task"]] = relationship(back_populates="assigned_employee", foreign_keys="Task.assigned_to_id")
    shifts: Mapped[list["Shift"]] = relationship(back_populates="employee")

class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    hours_worked: Mapped[float] = mapped_column(Float, default=0.0)
    employee: Mapped["Employee"] = relationship(back_populates="shifts")

class WageRecord(Base):
    __tablename__ = "wage_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    base_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    commission_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    bonus: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    deductions: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    net_payable: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)

# ─────────────────────────────────────────────
# CLIENTS & DEVICES
# ─────────────────────────────────────────────

class Client(Base):
    __tablename__ = "clients"
    # Clients list: filter org_id, sort by name (clients had no org index before).
    __table_args__ = (Index("ix_clients_org_name", "org_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(200))
    client_type: Mapped[str] = mapped_column(String(20), default="person")  # person|organization
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    manager_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    payer_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    discount_card: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    # 144hl — B2B billing details for documents (Orderry client print variables)
    tax_id: Mapped[Optional[str]] = mapped_column(String(80))
    reg_no: Mapped[Optional[str]] = mapped_column(String(80))
    billing_address: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(100))
    # 144ja — contact birthday (ISO YYYY-MM-DD, string keeps sqlite/postgres identical).
    # Drives the Contacts-module birthday notification (Orderry parity).
    birthday: Mapped[Optional[str]] = mapped_column(String(10))
    loyalty_tier: Mapped[str] = mapped_column(String(50), default="standard")
    consent_phone: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_email: Mapped[bool] = mapped_column(Boolean, default=True)
    balance: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Build 122: preferred billing currency for this contact (None → org base). Documents
    # default to it, and it drives converted price/balance display.
    currency_code: Mapped[Optional[str]] = mapped_column(String(8))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    watch_out: Mapped[Optional[str]] = mapped_column(Text)   # Build 144ag — prominent per-contact warning shown on their work orders
    portal_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)  # customer-portal magic link
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Discount matrix (Orderry "Managing contact discounts") — orders & invoices.
    # mode: none | percent | price_type. Sales-page discounts arrive with the Sales module.
    disc_services_mode: Mapped[str] = mapped_column(String(12), default="none")
    disc_services_value: Mapped[float] = mapped_column(Float, default=0.0)
    disc_services_price_type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("price_types.id"))
    disc_materials_mode: Mapped[str] = mapped_column(String(12), default="none")
    disc_materials_value: Mapped[float] = mapped_column(Float, default=0.0)
    disc_materials_price_type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("price_types.id"))
    devices: Mapped[list["Device"]] = relationship(back_populates="client")
    orders: Mapped[list["Order"]] = relationship(back_populates="client")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="client", foreign_keys="Invoice.client_id")
    custom_data: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)  # values for org custom client fields

class Device(Base):
    __tablename__ = "devices"
    # Intake / scan look a device up by identifier within the shop (values stored normalized,
    # see services/identifiers.py).
    __table_args__ = (
        Index("ix_devices_org_serial", "org_id", "serial"),
        Index("ix_devices_org_imei", "org_id", "imei"),
        Index("ix_devices_org_vin", "org_id", "vin"),
        Index("ix_devices_org_hin", "org_id", "hin"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Build 144do (security): devices are now tenant-anchored directly. Previously
    # tenancy was inferred via client_id -> Client.org_id, which left company-owned
    # assets (client_id NULL) unscoped — a cross-tenant IDOR. Every asset query now
    # filters on org_id. Nullable only to allow the backfill migration.
    org_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    owner_type: Mapped[str] = mapped_column(String(20), default="client")  # client|company
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))  # warehouse the asset sits in
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 90: asset warehouse
    asset_status: Mapped[str] = mapped_column(String(20), default="in_use")  # in_use|in_stock|for_sale|sold|written_off
    purchase_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    sale_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    converted_to_part_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("parts.id"))  # if turned into sellable product
    asset_type: Mapped[Optional[str]] = mapped_column(String(50))  # Device|Vehicle|Tool|Appliance
    brand: Mapped[Optional[str]] = mapped_column(String(100))
    model: Mapped[Optional[str]] = mapped_column(String(100))
    serial: Mapped[Optional[str]] = mapped_column(String(100))
    imei: Mapped[Optional[str]] = mapped_column(String(40))
    vin: Mapped[Optional[str]] = mapped_column(String(20))   # vehicles: cars/trucks/bikes/RVs/powersports
    hin: Mapped[Optional[str]] = mapped_column(String(20))   # marine: hull identification number
    template_key: Mapped[Optional[str]] = mapped_column(String(30), index=True)  # industry template this asset was created under (null = legacy)
    asset_group_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("asset_groups.id"))  # per-template group
    is_generated_serial: Mapped[bool] = mapped_column(Boolean, default=False)
    custom_data: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)  # values for org custom asset fields
    color: Mapped[Optional[str]] = mapped_column(String(50))
    condition: Mapped[Optional[str]] = mapped_column(String(50))
    watch_out: Mapped[Optional[str]] = mapped_column(Text)   # Build 144ag — prominent per-asset warning (e.g. "battery swollen") shown on its work orders
    # Build 144ai: an optional asset photo (data URL), shown on the board card,
    # in the work-order list (thumb + hover-enlarge), and on the general-info page.
    image_data: Mapped[Optional[str]] = mapped_column(Text)
    passcode_enc: Mapped[Optional[str]] = mapped_column(String(255))
    purchase_date: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    client: Mapped["Client"] = relationship(back_populates="devices")
    orders: Mapped[list["Order"]] = relationship(back_populates="device")

# ─────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────

class RevokedSession(Base):
    """A sign-in ended with POST /api/auth/logout. Its tokens are rejected until they expire;
    rows past expires_at are swept by the jobs runner."""
    __tablename__ = "revoked_sessions"
    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class OrderStatus(Base):
    """A configurable work-order status. Belongs to a status GROUP (new/in_progress/
    pending/ready/closed/dropped), which is what reports and triggers key off.
    Per-status automation: auto-invoice, auto-notify, request payment, lock edits."""
    __tablename__ = "order_statuses"
    # One row per key in each pipeline: a new shop's parallel first requests used to seed
    # the defaults several times over (migration ls144r1 removes the old copies).
    __table_args__ = (
        UniqueConstraint("org_id", "record_type", "key", name="uq_order_statuses_org_rt_key"),
        Index("ix_order_statuses_org_id", "org_id"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    key: Mapped[str] = mapped_column(String(50))           # stable slug, e.g. "in_repair"
    name: Mapped[str] = mapped_column(String(100))         # display label
    status_group: Mapped[str] = mapped_column(String(30), default="in_progress")  # new|in_progress|pending|ready|closed|dropped
    color: Mapped[str] = mapped_column(String(20), default="#888888")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # initial status for new orders
    triggers_invoice: Mapped[bool] = mapped_column(Boolean, default=False)
    triggers_notification: Mapped[bool] = mapped_column(Boolean, default=False)
    triggers_payment: Mapped[bool] = mapped_column(Boolean, default=False)  # open payment window
    locks_edits: Mapped[bool] = mapped_column(Boolean, default=False)
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)  # belongs to the estimate status set
    # 144ij — which record type this status set drives. Generalises the old
    # is_estimate boolean so ONE engine powers all 5 Orderry pipelines.
    # work_order|estimate|inquiry|invoice|transfer. is_estimate is kept as a
    # back-compat mirror (True iff record_type == 'estimate').
    record_type: Mapped[str] = mapped_column(String(20), default="work_order")
    shows_accept: Mapped[bool] = mapped_column(Boolean, default=False) # public page shows Accept/Decline
    converts_to_order: Mapped[bool] = mapped_column(Boolean, default=False)  # entering this status auto-creates a work order
    time_limit_min: Mapped[Optional[int]] = mapped_column(Integer)  # max minutes allowed in this status
    moves_asset_to: Mapped[Optional[str]] = mapped_column(String(40))  # warehouse/zone name for asset auto-move
    # 144gj — Orderry requirement gates: block entering this status until satisfied.
    require_comment: Mapped[bool] = mapped_column(Boolean, default=False)     # a comment must exist on the order
    require_payment: Mapped[bool] = mapped_column(Boolean, default=False)     # order must be fully paid (balance <= 0)
    require_checklist: Mapped[bool] = mapped_column(Boolean, default=False)   # every checklist item must be filled
    # Can the customer message the shop while an order is in this status? None = automatic:
    # open statuses yes, closed ones for Organisation.oe_msg_days_after_close days (services/messaging_rule).
    allow_messages: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    location: Mapped["Location"] = relationship(back_populates="order_statuses")

class Order(Base):
    __tablename__ = "orders"
    # 144ej — order numbers are unique PER ORG, not globally (Orderry scopes
    # numbering to the account). Two tenants can each have their own "WO-00001".
    __table_args__ = (
        UniqueConstraint("org_id", "order_number", name="uq_order_org_number"),
        # Hot list query: filter org_id, sort created_at DESC (default WO list).
        Index("ix_orders_org_created", "org_id", "created_at"),
        # Status tabs: filter org_id + status.
        Index("ix_orders_org_status", "org_id", "status"),
        # 144ei — barcode is globally unique across ALL tenants. Declared exactly as the migrations
        # build it (a lookup index + a separate unique index, see y5a7c9e1g3i5) so models and the
        # migrated production schema match. Uniqueness is unchanged.
        Index("uq_orders_barcode", "barcode", unique=True),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_number: Mapped[str] = mapped_column(String(50))
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    device_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("devices.id"), index=True)
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"), index=True)
    assigned_to_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"), index=True)
    manager_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"), index=True)  # 144cp: the WO owner/manager (Orderry parity), distinct from the assigned specialist
    status: Mapped[str] = mapped_column(String(50), default="new")
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)  # estimate vs work order
    converted_order_id: Mapped[Optional[str]] = mapped_column(String)  # set on the estimate after it becomes an order
    converted_from_id: Mapped[Optional[str]] = mapped_column(String)   # set on the order created from an estimate
    signature: Mapped[Optional[str]] = mapped_column(Text)
    estimate_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # Build 75: public estimate approval
    signature_blob_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("media_blobs.id"))             # client e-signature on accept
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0=normal 1=urgent 2=VIP
    complaint: Mapped[Optional[str]] = mapped_column(Text)
    diagnosis: Mapped[Optional[str]] = mapped_column(Text)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)
    # Why a job was cancelled (a value from the "Cancellation reasons" directory), set when the
    # order enters a dropped-group status. Feeds the cancellations-by-reason report.
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(120))
    tech_notes: Mapped[Optional[str]] = mapped_column(Text)   # 144bj: technician's working notes
    conclusion: Mapped[Optional[str]] = mapped_column(Text)   # 144bj: conclusion / client recommendations
    labour_total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    parts_total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    # Build 122: stored money is always in the org base currency. currency_code + fx_rate
    # (units of this currency per 1 base) tell the UI/print layer how to *display* converted
    # amounts; the rate is snapshotted at creation so later rate changes don't alter old docs.
    currency_code: Mapped[Optional[str]] = mapped_column(String(8))
    fx_rate: Mapped[Optional[float]] = mapped_column(Float)
    discount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    tax: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    prepayment_total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    deposit_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # requested deposit (e.g. on an estimate) the client can pay via pay-link
    warranty_until: Mapped[Optional[date]] = mapped_column(Date)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status_entered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=_now)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime)
    scheduled_to: Mapped[Optional[datetime]] = mapped_column(DateTime)  # Phase G compound field
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    resource_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("resources.id"))  # Phase G
    type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("work_order_types.id"), index=True)
    template_key: Mapped[Optional[str]] = mapped_column(String(30), index=True)  # industry template at creation (null = legacy); historical records keep their own
    custom_data: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    intake_photos: Mapped[Optional[list]] = mapped_column(JSON, default=list)   # Build 144ak: media blob ids documenting device condition at check-in
    # 144dg — {original_blob_id: marked_blob_id}: a marked-up copy of an intake photo. The ORIGINAL
    # (in intake_photos) is never mutated, so the untouched check-in shot survives for disputes.
    intake_markups: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    public_token: Mapped[Optional[str]] = mapped_column(String(40), unique=True, index=True, default=lambda: secrets.token_urlsafe(16))  # 144cu — unique track link, never repeated
    client_msg_unread: Mapped[int] = mapped_column(Integer, default=0)  # 144ba: unread customer messages
    rec_unread: Mapped[int] = mapped_column(Integer, default=0)  # 144bd: unread recommendation decisions (approve/decline)
    barcode: Mapped[Optional[str]] = mapped_column(String(40), index=True, default=lambda: _uuid().replace("-", "")[:12].upper())  # Phase F scanner search; 144ei: globally unique via uq_orders_barcode (__table_args__)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # soft-delete: hidden from lists, kept for the owner
    archive_reason: Mapped[Optional[str]] = mapped_column(Text)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    # Several items per job: the job an item was split off from ("Split into its own job").
    split_from_order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    client: Mapped[Optional["Client"]] = relationship(back_populates="orders")
    device: Mapped[Optional["Device"]] = relationship(back_populates="orders")
    location: Mapped["Location"] = relationship(back_populates="orders")
    assigned_to: Mapped[Optional["Employee"]] = relationship(back_populates="assigned_orders", foreign_keys=[assigned_to_id])
    manager: Mapped[Optional["Employee"]] = relationship(foreign_keys=[manager_id])  # 144cp
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    status_logs: Mapped[list["OrderStatusLog"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    comments: Mapped[list["OrderComment"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="order")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="order")

class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    line_type: Mapped[str] = mapped_column(String(20))  # labour|part|fee
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[str] = mapped_column(String(500))
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    cost_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    kind: Mapped[Optional[str]] = mapped_column(String(20))  # service|product|custom
    technician_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"), index=True)
    work_share: Mapped[float] = mapped_column(Float, default=1.0)  # for split lines (0.5/0.5)
    # 144av #2 — flat-rate/estimated labour hours we bill, to compare against actual clocked time.
    booked_hours: Mapped[float] = mapped_column(Float, default=0.0)
    # 144av #3 — split one service across techs: [{"employee_id": "...", "share": 0.5}, ...].
    # When set it overrides technician_id/work_share for payroll + time attribution.
    co_technicians: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    pending_stock: Mapped[float] = mapped_column(Float, default=0.0)  # qty added without deduction
    warranty_days: Mapped[int] = mapped_column(Integer, default=0)  # per-line client warranty (Phase E)
    discount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    discount_type: Mapped[str] = mapped_column(String(10), default="pct")  # pct|fixed
    discount_paid_by: Mapped[str] = mapped_column(String(10), default="client")  # client|company
    tax_id: Mapped[Optional[str]] = mapped_column(String)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    tax_mode: Mapped[str] = mapped_column(String(10), default="imposed")  # included|imposed
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    order: Mapped["Order"] = relationship(back_populates="lines")
    # Several items per job: which of the job's items this line is for (null = the whole job).
    order_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("order_items.id"), index=True)


class OrderItem(Base):
    """Several items per job (multi-asset). One row per item the customer left on this job.

    Tenant-scoped twice over: org_id on every row, and every read/write also matches order_id,
    so an item can never be read or attached across shops or jobs. The job's own device stays
    on orders.device_id (item #1 is created for it) so everything that knows one item still works.

    outcome: open | fixed | cant_fix | declined
    """
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "asset_id", name="uq_order_items_order_asset"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("devices.id"), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(200))     # for items with no asset record
    complaint: Mapped[Optional[str]] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(20), default="open")
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# 144df — the legacy JSON JobKit model was retired; the Bundle engine (relabelled "Job Kit" in the UI)
# is now the single job-kit concept. Its table is dropped by migration jk144df01.

class OrderStatusLog(Base):
    __tablename__ = "order_status_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(50))
    to_status: Mapped[str] = mapped_column(String(50))
    changed_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    note: Mapped[Optional[str]] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    order: Mapped["Order"] = relationship(back_populates="status_logs")

class OrderComment(Base):
    __tablename__ = "order_comments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    author_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    body: Mapped[str] = mapped_column(Text)
    visible_to_client: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    order: Mapped["Order"] = relationship(back_populates="comments")

# ─────────────────────────────────────────────
# INVENTORY
# ─────────────────────────────────────────────

class Bundle(Base):
    """A catalog kit: one pick that explodes into component services/products
    on the order, auto-deducting component stock (Orderry Bundles)."""
    __tablename__ = "bundles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(200))
    price_mode: Mapped[str] = mapped_column(String(10), default="sum")  # sum|fixed
    price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)            # used when fixed
    category_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("categories.id"), index=True)  # 144ax
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class BundleComponent(Base):
    __tablename__ = "bundle_components"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    bundle_id: Mapped[str] = mapped_column(String, ForeignKey("bundles.id"), index=True)
    ref_type: Mapped[str] = mapped_column(String(10))  # service|product
    ref_id: Mapped[str] = mapped_column(String)
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    # 144db — device-aware kit: when True, this product slot resolves at apply-time to the in-category
    # part that fits the work order's device (the configured ref_id is the fallback / default).
    device_variant: Mapped[bool] = mapped_column(Boolean, default=False)


class Appointment(Base):
    """An appointment: service duration drives the slot; an employee can't be
    double-booked for overlapping windows."""
    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    client_name: Mapped[Optional[str]] = mapped_column(String(200))
    service_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("services.id"))
    # 144dr — appointments without an assignee: take the request first, assign the tech
    # later (the repair flow). Nullable now; unassigned appointments show in a dedicated lane.
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(20), default="booked")  # booked|done|canceled|no_show
    note: Mapped[Optional[str]] = mapped_column(Text)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))  # Build 85: appointment → work order
    client_phone: Mapped[Optional[str]] = mapped_column(String(40))                   # Build 85: public appointments
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Build 144bz: DB-level guard against the concurrent-create race — two clients
    # can't both grab the same offered slot for the same technician.
    __table_args__ = (
        Index("uq_appointment_emp_slot", "org_id", "employee_id", "scheduled_for",
              unique=True, sqlite_where=text("status = 'booked'"),
              postgresql_where=text("status = 'booked'")),
    )


class ChatThread(Base):
    """One conversation with a contact on one channel. Auto-links to the CRM
    client by phone (most recent record wins). Transport is a simulated
    adapter until provider keys exist."""
    __tablename__ = "chat_threads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")  # whatsapp|sms|email
    contact_phone: Mapped[Optional[str]] = mapped_column(String(160), index=True)  # C4: phone, email or social id
    contact_name: Mapped[Optional[str]] = mapped_column(String(200))
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    lead_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("leads.id"))
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))  # Build 81: chat → work order
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # C3: inbox status (new|reply_needed|replied|completed), set by message direction; replied
    # threads auto-complete after 24h. Spam threads sit in their own folder and notify nobody.
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")
    last_inbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_outbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    spam_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    __table_args__ = (Index("ix_chat_threads_org_status", "org_id", "is_spam", "status"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(String, ForeignKey("chat_threads.id"), index=True)
    direction: Mapped[str] = mapped_column(String(5))  # in|out
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(15), default="sent")  # sent|failed|received
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    external_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)  # C4: provider message id (de-dupe)
    # C4: a provider retry racing the first delivery can't store the message twice.
    __table_args__ = (Index("uq_chat_messages_thread_external", "thread_id", "external_id", unique=True),)


class Part(Base):
    __tablename__ = "parts"
    # Inventory list: filter org_id (parts had no org index before), sort by name.
    __table_args__ = (Index("ix_parts_org_name", "org_id", "name"),
                      # opa: one active product per MPN per org, case-insensitive (backs _checked_mpn;
                      # also the per-org index for MPN scans, which match on lower(mpn))
                      Index("uq_parts_org_mpn_active", "org_id", text("lower(mpn)"), unique=True,
                            sqlite_where=text("active = 1 AND mpn IS NOT NULL"),
                            postgresql_where=text("active AND mpn IS NOT NULL")),
                      # ii144a: one active product per SKU per shop, case-insensitive
                      Index("uq_parts_org_sku_active", "org_id", text("lower(sku)"), unique=True,
                            sqlite_where=text("active = 1 AND sku IS NOT NULL AND sku <> ''"),
                            postgresql_where=text("active AND sku IS NOT NULL AND sku <> ''")))
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    sku: Mapped[Optional[str]] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    brand: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)  # 144av: product description (form)
    default_supplier_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("suppliers.id", ondelete="SET NULL"))  # 144av; 144ep: supplier mirror can be removed -> clear the preference
    category: Mapped[Optional[str]] = mapped_column(String(100))
    cost_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    avg_cost: Mapped[float] = mapped_column(Numeric(12, 4, asdecimal=False), default=0.0)  # weighted moving-average cost
    sell_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)   # store / counter price
    repair_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # 144bk: price when used on a work order (0 = fall back to sell_price)
    uom: Mapped[str] = mapped_column(String(10), default="pcs")  # pcs|m|kg|l|hr|set
    barcode: Mapped[Optional[str]] = mapped_column(String(100))
    warranty_days: Mapped[int] = mapped_column(Integer, default=0)  # client warranty (Phase E)
    supplier_warranty_days: Mapped[int] = mapped_column(Integer, default=0)
    min_stock: Mapped[int] = mapped_column(Integer, default=0)
    # build 144aa: default quantity to reorder when raising a PO for this product
    min_order_qty: Mapped[float] = mapped_column(Float, default=0)
    # build 144bo: one-shot low-stock alert dedupe — set True when a low-stock alert fires,
    # reset False when a receipt lifts on-hand back above min_stock ("once until restocked").
    low_stock_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    # ── catalog master (Phase F) ──
    code: Mapped[Optional[str]] = mapped_column(String(100))            # internal article/code, distinct from SKU/barcode
    mpn: Mapped[Optional[str]] = mapped_column(String(64))              # opa: manufacturer part number
    category_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("categories.id"), index=True)
    barcode_type: Mapped[str] = mapped_column(String(12), default="code128")  # upca|ean13|ean8|code128|qr
    serialized: Mapped[bool] = mapped_column(Boolean, default=False)    # irreversible once True (Orderry rule)
    expiry_tracking: Mapped[bool] = mapped_column(Boolean, default=False)  # FEFO write-off when on
    # ── Build 76: asset metadata preserved across Asset → Product conversion ──
    template_key: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    asset_group_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("asset_groups.id"))
    asset_brand: Mapped[Optional[str]] = mapped_column(String(100))
    asset_model: Mapped[Optional[str]] = mapped_column(String(100))
    compatibility_json: Mapped[Optional[dict]] = mapped_column(JSON)
    weight: Mapped[Optional[float]] = mapped_column(Float)              # kg
    dimensions: Mapped[Optional[str]] = mapped_column(String(60))       # "LxWxH" cm
    image_data: Mapped[Optional[str]] = mapped_column(Text)
    image_blob_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("media_blobs.id"))            # data URL / base64 thumbnail
    # ── variants (sizes/colors/configs): a variant is a Part whose parent_id points to the base product ──
    parent_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("parts.id"), index=True)
    variant_attrs: Mapped[Optional[dict]] = mapped_column(JSON)         # {"Size":"L","Color":"Red"}
    # build 144as: values for org custom product fields (Settings → Forms → Products)
    custom_data: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    stock_levels: Mapped[list["StockLevel"]] = relationship(back_populates="part")
    movements: Mapped[list["StockMovement"]] = relationship(back_populates="part")


class Service(Base):
    """Services & Labors catalog — preset labor items with price + cost.
    Unlike Parts, services carry no stock; they are pure labor revenue."""
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(String(2000), default="")     # 144ee
    category: Mapped[Optional[str]] = mapped_column(String(100))  # legacy free-text label
    category_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("categories.id"), index=True)  # 144bu: shared category tree
    is_labor: Mapped[bool] = mapped_column(Boolean, default=False)         # 144ee: Labor vs Service (Orderry type)
    unit: Mapped[str] = mapped_column(String(24), default="pc")            # 144ee: unit of measurement
    price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    cost_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)  # drives appointment slot length
    # 144ax G3 — flat-rate labour: bill the booked (standard) hours × this price-as-hourly-rate
    # rather than a flat qty×price. When on, adding the service sets the line qty = booked hours.
    flat_rate: Mapped[bool] = mapped_column(Boolean, default=False)
    warranty_days: Mapped[int] = mapped_column(Integer, default=0)
    barcode: Mapped[Optional[str]] = mapped_column(String(100))
    bookable: Mapped[bool] = mapped_column(Boolean, default=True)          # 144ee: show on public appointment page
    allowed_employee_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)     # 144ek: who can perform (empty = all)
    required_resource_types: Mapped[Optional[list]] = mapped_column(JSON, default=list)  # 144ek: resource types this service needs
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SavedFilter(Base):
    """A saved work-order filter combination. Private unless shared with all staff."""
    __tablename__ = "saved_filters"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"))
    name: Mapped[str] = mapped_column(String(150))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserPref(Base):
    """Build 144ca — per-user UI preferences that follow the user across devices
    (e.g. list column layout + saved views). One row per (employee, key)."""
    __tablename__ = "user_prefs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    pref_key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        Index("uq_userpref_emp_key", "employee_id", "pref_key", unique=True),
    )


class AuthToken(Base):
    """Build 144cb — single-use, expiring tokens for account flows that happen
    while logged out: password reset and email verification. Only the SHA-256
    hash of the token is stored, so a database leak can't be replayed. One row is
    consumed (``used_at`` set) the first time it validates."""
    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(20))                     # reset | verify
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)   # sha256 hex of the raw token
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Invite(Base):
    """Build 144cc — a pending teammate invitation. An admin creates it with the
    new person's email + role; we email a link carrying a single-use token (only
    its hash is stored). Accepting it creates the Employee with a password the
    invitee chooses. One open invite per (org, email) is enforced in the router."""
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    name: Mapped[Optional[str]] = mapped_column(String(200))
    role_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("roles.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    invited_by: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PlatformAdmin(Base):
    """Build 144ce — a super-user of the whole platform, NOT tied to any tenant.
    These accounts sign in at the separate /admin portal and can see and manage
    every organisation. Kept in their own table so tenant data and platform-operator
    data never mix."""
    __tablename__ = "platform_admins"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PlatformCoupon(Base):
    """Build 144ce — a discount code generated in the /admin portal. Optionally
    scoped to a plan. Applying it to a tenant sets that tenant's discount_pct."""
    __tablename__ = "platform_coupons"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    percent: Mapped[float] = mapped_column(Float, default=0.0)
    plan: Mapped[Optional[str]] = mapped_column(String(50))    # null = any plan
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[date]] = mapped_column(Date)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Tax(Base):
    """A named, reusable tax rate. Mode: included (price already contains it,
    extracted for reporting) or imposed (added on top, shown to customer)."""
    __tablename__ = "taxes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(100))
    rate: Mapped[float] = mapped_column(Float, default=0.0)   # percent
    mode: Mapped[str] = mapped_column(String(10), default="imposed")  # included|imposed (aka inclusive|exclusive)
    code: Mapped[Optional[str]] = mapped_column(String(40))               # tax code (e.g. VAT reg)
    name_for_document: Mapped[Optional[str]] = mapped_column(String(100)) # print label if different from name
    location_scope: Mapped[str] = mapped_column(String(10), default="all")  # all|selected
    location_ids: Mapped[Optional[str]] = mapped_column(Text)             # JSON list when scope=selected
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

class ProductBarcode(Base):
    """ii144a — every barcode a product answers to. A product can have several (a supplier's EAN, a
    repackaged unit's UPC, the shop's own label). `is_main` mirrors `parts.barcode` and is kept in sync
    by services/product_codes.py on every flush; `active` mirrors the product's, so an archived product
    releases its codes. Unique per shop among active rows — another shop's codes never clash."""
    __tablename__ = "product_barcodes"
    __table_args__ = (Index("uq_product_barcodes_org_code", "org_id", "barcode", unique=True,
                            sqlite_where=text("active = 1"), postgresql_where=text("active")),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    barcode: Mapped[str] = mapped_column(String(100))
    barcode_type: Mapped[Optional[str]] = mapped_column(String(12))    # upca|ean13|ean8|code128|qr
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    part: Mapped["Part"] = relationship()     # many-to-one only: orders the insert after a new product


class StockLevel(Base):
    """On-hand for one product in one place. ii144a: a row is keyed (product, location, bin) — one row
    per bin, plus one unbinned row per (location, warehouse tag). Read and write it only through
    services/stock_rows.py, which sums and draws across a location's rows."""
    __tablename__ = "stock_levels"
    __table_args__ = (Index("ix_stock_levels_part_location", "part_id", "location_id"),   # phase 4 perf: hot lookup path
                      Index("uq_stock_levels_part_bin", "part_id", "bin_id", unique=True,
                            sqlite_where=text("bin_id IS NOT NULL"), postgresql_where=text("bin_id IS NOT NULL")),
                      Index("uq_stock_levels_part_loc_unbinned", "part_id", "location_id",
                            text("coalesce(warehouse_id, '')"), unique=True,
                            sqlite_where=text("bin_id IS NULL"), postgresql_where=text("bin_id IS NULL")))
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"), index=True)  # micro-location
    qty_available: Mapped[float] = mapped_column(Float, default=0.0)
    qty_reserved: Mapped[float] = mapped_column(Float, default=0.0)
    part: Mapped["Part"] = relationship(back_populates="stock_levels")
    location: Mapped["Location"] = relationship(back_populates="stock_levels")

class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (Index("ix_stock_movements_part_created", "part_id", "created_at"),)   # phase 4 perf: hot lookup path
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"), index=True)
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"), index=True)  # source/target bin
    movement_type: Mapped[str] = mapped_column(String(30))  # receive|use|return|writeoff|transfer
    qty: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # batch cost price for this movement
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    ref_type: Mapped[Optional[str]] = mapped_column(String(50))
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    part: Mapped["Part"] = relationship(back_populates="movements")

class TelephonyConfig(Base):
    """B7: per-org telephony/VoIP settings. Provider-agnostic; a simulated
    provider works with no external creds so the flow is usable out of the box."""
    __tablename__ = "telephony_config"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True, unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="simulated")  # simulated|twilio|aircall|zadarma
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    caller_id_number: Mapped[Optional[str]] = mapped_column(String(40))
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)  # provider creds ref / numbers
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Tag(Base):
    """B6: a reusable label attachable to contacts, orders, chats, etc."""
    __tablename__ = "tags"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    color: Mapped[str] = mapped_column(String(20), default="#64748b")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Taggable(Base):
    """B6: join row — a tag applied to one entity (entity_type + entity_id)."""
    __tablename__ = "taggables"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    tag_id: Mapped[str] = mapped_column(String, ForeignKey("tags.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)  # contact|order|chat
    entity_id: Mapped[str] = mapped_column(String, index=True)
    __table_args__ = (UniqueConstraint("tag_id", "entity_type", "entity_id", name="uq_taggable"),)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    lead_days: Mapped[int] = mapped_column(Integer, default=3)
    balance: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # what we owe the supplier (credit)
    currency: Mapped[str] = mapped_column(String(8), default="₹")
    # Build 114: link back to the unified Contact (Client) this supplier mirrors, so a
    # contact marked "supplier" and a PO-facing Supplier row stay in sync.
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="supplier")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    # PO numbers are per shop like order numbers (migration ls144r2): a global unique made the
    # second shop's first PO-00001 a 500.
    __table_args__ = (UniqueConstraint("org_id", "po_number", name="uq_purchase_orders_org_number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(String, ForeignKey("suppliers.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    po_number: Mapped[str] = mapped_column(String(50))
    source_order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)  # WO that needs these parts
    status: Mapped[str] = mapped_column(String(30), default="draft")  # RECEIPT state: draft|ordered|partial|received|cancelled (auto, receipt-driven)
    # 144an — manual, configurable WORKFLOW stage (references an OrderStatus key in the
    # 'purchase_order' pipeline). Human-driven and independent of the receipt state above.
    workflow_status: Mapped[Optional[str]] = mapped_column(String(50))
    # 144ap — landed cost: how much freight/charges has already been capitalised into the
    # cost of received units, so a later freight bill only applies the *delta* (recost).
    freight_capitalised: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    # 144ar — how PO charges are spread across lines when capitalised into landed cost:
    # value (default, cost-weighted) | qty (per unit) | weight (per kg, uses Part.weight).
    alloc_basis: Mapped[str] = mapped_column(String(10), default="value")
    # 144ar — per-shipment freight already capitalised at receive time (freight billed WITH a
    # partial delivery), tracked separately from PO-level charge recost above.
    shipment_freight: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    on_credit: Mapped[bool] = mapped_column(Boolean, default=False)  # buy on credit → adds to supplier balance
    received_cost: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # cost of goods actually received
    expected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ordered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # ── Build 144al: editable PO — supplier document + optional charges ──
    supplier_invoice_no: Mapped[Optional[str]] = mapped_column(String(80))
    supplier_invoice_date: Mapped[Optional[date]] = mapped_column(Date)
    # Build 144aac — three-way match: the amount the supplier actually billed us, matched
    # against what we ordered and what we received.
    supplier_invoice_total: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))
    # Build 144aj — the supplier's invoice PDF/image, stored as a media blob (evidence for
    # the three-way match).
    supplier_invoice_blob_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("media_blobs.id"))
    supplier_invoice_name: Mapped[Optional[str]] = mapped_column(String(200))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    # optional, customizable charges: [{"label": "Shipping", "amount": 12.5}, ...]
    charges: Mapped[Optional[list]] = mapped_column(JSON)
    # when True, charges are allocated into landed unit cost at receive (else flat expense)
    charges_to_cost: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    supplier: Mapped["Supplier"] = relationship(back_populates="purchase_orders")
    lines: Mapped[list["POLine"]] = relationship(back_populates="po", cascade="all, delete-orphan")

class POLine(Base):
    __tablename__ = "po_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    po_id: Mapped[str] = mapped_column(String, ForeignKey("purchase_orders.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    qty_ordered: Mapped[float] = mapped_column(Float, default=1.0)
    qty_received: Mapped[float] = mapped_column(Float, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    po: Mapped["PurchaseOrder"] = relationship(back_populates="lines")

# ─────────────────────────────────────────────
# FINANCE
# ─────────────────────────────────────────────
    reserved_order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))  # Build 94

class Invoice(Base):
    __tablename__ = "invoices"
    # Invoices list: filter org_id, sort created_at DESC. One invoice number per shop (migration
    # ls144r4; scripts/find_duplicate_invoice_numbers.py finds legacy clashes).
    __table_args__ = (Index("ix_invoices_org_created", "org_id", "created_at"),
                      UniqueConstraint("org_id", "invoice_number", name="uq_invoices_org_number"))
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    invoice_number: Mapped[str] = mapped_column(String(50))
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    # 144dr — invoice loop. payer_client_id lets the bill go to a different party
    # (B2B / insurance / fleet); display_mode controls line grouping on the doc.
    payer_client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    display_mode: Mapped[str] = mapped_column(String(12), default="detailed")  # detailed|simplified|one_line
    comment: Mapped[str] = mapped_column(String(1000), default="")              # 144ed: free-text note (Orderry parity)
    auto_drafted: Mapped[bool] = mapped_column(Boolean, default=False)          # created by the on-close automation
    status: Mapped[str] = mapped_column(String(30), default="draft")
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    discount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    tax: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    amount_paid: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    # opb — sum of ISSUED credit notes linked to this invoice; balance due = total − paid − credited.
    credited: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0, server_default="0")
    legal_entity_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("legal_entities.id"))
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 144if — e-invoicing (in-house UBL 2.1). status: none|issued|cleared|rejected|cancelled.
    einvoice_status: Mapped[str] = mapped_column(String(12), default="none")
    einvoice_number: Mapped[Optional[str]] = mapped_column(String(60))   # fiscal / clearance ref
    einvoice_format: Mapped[Optional[str]] = mapped_column(String(20))   # e.g. ubl2.1
    einvoice_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    einvoice_xml: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    order: Mapped[Optional["Order"]] = relationship(back_populates="invoices")
    client: Mapped[Optional["Client"]] = relationship(back_populates="invoices", foreign_keys=[client_id])
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice")
    order_links: Mapped[list["InvoiceOrder"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        cascade="all, delete-orphan")  # 144ed: free-form invoice lines

class DocumentSignature(Base):
    """144ig — tamper-evident client e-signature on a document (estimate OR work order).

    Beyond Orderry's bare drawn scribble: we capture the typed name, the drawn image,
    the timestamp, the signer's IP + user agent, and a SHA-256 hash of the exact
    document content the customer saw at sign time (with its total). That record backs
    a downloadable signed-approval certificate that holds up for insurance / fleet /
    B2B disputes across jurisdictions.
    """
    __tablename__ = "document_signatures"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(20), default="estimate")   # estimate|work_order
    decision: Mapped[str] = mapped_column(String(10), default="accept")     # accept|decline
    signer_name: Mapped[Optional[str]] = mapped_column(String(200))
    signature_png: Mapped[Optional[str]] = mapped_column(Text)              # drawn signature data URL
    doc_hash: Mapped[Optional[str]] = mapped_column(String(64))             # sha256 of doc snapshot
    doc_total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    currency_code: Mapped[Optional[str]] = mapped_column(String(3))
    signer_ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class InvoiceOrder(Base):
    """Build 144du — an invoice can bill several work orders (Orderry parity).
    order_id on Invoice stays the primary/first order for back-compat; this link
    table carries every order the invoice covers, including the primary."""
    __tablename__ = "invoice_orders"
    __table_args__ = (UniqueConstraint("invoice_id", "order_id", name="uq_invoice_order"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    invoice_id: Mapped[str] = mapped_column(String, ForeignKey("invoices.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    invoice: Mapped["Invoice"] = relationship(back_populates="order_links")

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_paid_at", "paid_at"),)   # phase 4 perf: hot lookup path
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    invoice_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("invoices.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    method: Mapped[str] = mapped_column(String(50))  # cash|card|transfer|balance|online
    kind: Mapped[str] = mapped_column(String(20), default="payment")  # prepayment|payment|refund
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    received_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    shift_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("cash_shifts.id"))
    reference: Mapped[Optional[str]] = mapped_column(String(200))
    provider_ref: Mapped[Optional[str]] = mapped_column(String(120))   # F2: card-reader charge id (pi_… / Square checkout)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    invoice: Mapped["Invoice"] = relationship(back_populates="payments")

class CashShift(Base):
    __tablename__ = "cash_shifts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    opened_by_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"))
    opening_float: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    closing_float: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))
    expected_cash: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 144bc — optional denomination breakdowns {"100": 2, "20": 5, ...} captured on open/close.
    open_denoms: Mapped[Optional[dict]] = mapped_column(JSON)
    close_denoms: Mapped[Optional[dict]] = mapped_column(JSON)
    opened_by: Mapped[Optional["Employee"]] = relationship(foreign_keys=[opened_by_id])
    payments: Mapped[list["Payment"]] = relationship(foreign_keys=[Payment.shift_id])


class CashDrawerMovement(Base):
    """144bc — a manual cash in/out during an open shift (petty cash, bank run, change top-up).
    Each carries a mandatory note so the drawer can be reconciled; feeds expected_cash."""
    __tablename__ = "cash_drawer_movements"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    shift_id: Mapped[str] = mapped_column(String, ForeignKey("cash_shifts.id"), index=True)
    direction: Mapped[str] = mapped_column(String(4))        # in | out
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    note: Mapped[str] = mapped_column(String(300))
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProcessorPayout(Base):
    """144bf — a card/online processor's deposit to the bank (Stripe/Square/SumUp or manual). The
    third reconciliation leg: card payments the shop took, minus fees, should equal what the
    processor actually deposited. A connector will populate this table later; for now it's entered
    by hand. `gross − fee = net`."""
    __tablename__ = "processor_payouts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="manual")   # stripe|square|sumup|manual
    payout_date: Mapped[date] = mapped_column(Date, default=date.today)
    gross_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    fee_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    net_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    reference: Mapped[Optional[str]] = mapped_column(String(120))
    note: Mapped[Optional[str]] = mapped_column(String(300))
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    category: Mapped[Optional[str]] = mapped_column(String(100))
    recorded_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    description: Mapped[str] = mapped_column(String(500))
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    receipt_url: Mapped[Optional[str]] = mapped_column(String(500))
    expense_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Account(Base):
    """A cashbox/account. Holds a running balance. Its payment_option gates which
    payment methods appear at checkout. Company-scoped (all locations) or per-location."""
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))  # null = company-wide
    name: Mapped[str] = mapped_column(String(120))
    icon: Mapped[str] = mapped_column(String(40), default="wallet")
    payment_option: Mapped[str] = mapped_column(String(20), default="cash")  # cash|cashless|card
    is_company: Mapped[bool] = mapped_column(Boolean, default=False)
    is_virtual: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    # Build 144cr: an account is denominated in one currency. Null = org base
    # currency. Set at creation and locked afterwards; the balance stays native.
    currency_code: Mapped[Optional[str]] = mapped_column(String(8))
    fee_pct: Mapped[float] = mapped_column(Float, default=0.0)      # transaction fee %
    fee_fixed: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)    # transaction fee fixed
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConsentLog(Base):
    """Build 144cs — GDPR proof-of-consent trail. Every grant/withdrawal of a
    client's contact/marketing consent is recorded permanently with who + when
    + source, so the controller can demonstrate lawful basis (GDPR Art. 7(1))."""
    __tablename__ = "consent_log"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    client_id: Mapped[str] = mapped_column(String, ForeignKey("clients.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))     # email|phone|marketing
    granted: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[Optional[str]] = mapped_column(String(60))   # staff|appointment|import|erasure
    actor_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Currency(Base):
    """Build 144cr — an additional (non-base) currency the company works with.
    ``rate`` is units of the BASE currency per 1 unit of this currency
    (i.e. ``1 <code> = rate <base>``). Editing the rate appends a history row."""
    __tablename__ = "currencies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    code: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(8), default="")
    position: Mapped[str] = mapped_column(String(10), default="before")  # before|after
    rate: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CurrencyRateHistory(Base):
    """Build 144cr — permanent audit trail of exchange-rate changes."""
    __tablename__ = "currency_rate_history"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    currency_id: Mapped[str] = mapped_column(String, ForeignKey("currencies.id"), index=True)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    rate: Mapped[float] = mapped_column(Float)
    changed_by: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CashFlowItem(Base):
    """A tag categorising a transaction (Sales, Rent, Payroll, Supplier payment…)."""
    __tablename__ = "cash_flow_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(120))
    direction: Mapped[str] = mapped_column(String(10), default="income")  # income|expense
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Transaction(Base):
    """A single money movement against an account. Types: income, expense,
    transfer (paired), invoice_payment, fee. The ledger that feeds all reports."""
    __tablename__ = "transactions"
    # Finance P&L / fee aggregates: filter org_id + txn_type.
    __table_args__ = (Index("ix_transactions_org_type", "org_id", "txn_type"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), index=True)
    txn_type: Mapped[str] = mapped_column(String(20))  # income|expense|transfer|invoice_payment|fee
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))        # signed: + in, - out
    cash_flow_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("cash_flow_items.id"))
    comment: Mapped[Optional[str]] = mapped_column(String(500))
    ref_type: Mapped[Optional[str]] = mapped_column(String(30))  # order|invoice|sale|manual
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"), index=True)
    transfer_pair_id: Mapped[Optional[str]] = mapped_column(String)  # links the two halves of a transfer
    # 144bh: manual cash income/expense on a till during an open shift links here so the drawer's
    # expected cash includes it (sales go through Payment.shift_id; this covers manual till money).
    shift_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("cash_shifts.id"), index=True)
    fx_rate: Mapped[Optional[float]] = mapped_column(Float)  # Build 144cr: rate snapshotted on a cross-currency transfer
    is_retroactive: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # A1 (parity): money is never silently mutated. A transaction is corrected by
    # posting a signed delta row (reverses_txn_id) and deleted softly (status),
    # so the ledger stays append-only and auditable. balance_after is the running
    # account balance at post time — frozen forever, never recomputed after a
    # later delete/correction (matches Orderry's strikethrough ledger).
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")  # active|corrected|deleted
    balance_after: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))  # frozen snapshot at post time
    reverses_txn_id: Mapped[Optional[str]] = mapped_column(String)  # set on a correction delta row
    void_reason: Mapped[Optional[str]] = mapped_column(String(300))
    voided_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))

# ─────────────────────────────────────────────
# TASKS & COMMS
# ─────────────────────────────────────────────

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)
    assigned_to_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    assignee_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)  # 144ex: multiple technicians (primary owner stays assigned_to_id)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)   # Build 86
    lead_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("leads.id"), index=True)       # Build 86
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))           # Build 86
    parent_task_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("tasks.id"))            # Build 86: subtasks
    completed_on_time: Mapped[Optional[bool]] = mapped_column(Boolean)  # Build 86: on-time analysis
    status: Mapped[str] = mapped_column(String(30), default="todo")
    priority: Mapped[str] = mapped_column(String(20), default="normal")

    @validates("priority")
    def _priority_text(self, _key, value):
        # The tasks API sends numbers (0, 1, 2); the column is text. SQLite coerced silently,
        # Postgres/asyncpg rejected the int — creating any task 500'd in production.
        return None if value is None else str(value)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    assigned_employee: Mapped[Optional["Employee"]] = relationship(back_populates="tasks", foreign_keys=[assigned_to_id])
    order: Mapped[Optional["Order"]] = relationship(back_populates="tasks")

class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    channel: Mapped[str] = mapped_column(String(30))  # sms|email|whatsapp
    trigger_event: Mapped[str] = mapped_column(String(100))  # status_change|order_created
    trigger_status: Mapped[Optional[str]] = mapped_column(String(50))  # which status fires it
    name: Mapped[Optional[str]] = mapped_column(String(150))
    subject: Mapped[Optional[str]] = mapped_column(String(300))
    body_template: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationRule(Base):
    """Build 80 — an external-notification rule.

    Orderry configures notifications per module as *rules*, not just templates:
    pick the event, the channel and template, and optionally delay the send, hold
    it to working hours, scope it to a location, or fire it a fixed interval
    *before* a scheduled appointment. This model is that rule; NotificationTemplate
    remains the message body it renders.
    """
    __tablename__ = "notification_rules"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    module: Mapped[str] = mapped_column(String(16), default="order")   # order|lead|appointment|task|contact
    # 144jb: who the rule notifies — the client (external) or staff (internal:
    # the order's assignee + manager). Orderry keeps External and Internal
    # notifications as two separate settings pages; one engine powers both here.
    scope: Mapped[str] = mapped_column(String(10), default="client")   # client|staff
    # 144jb: for module="order", scope a rule to Jobs vs Estimates (Orderry splits
    # these). None = applies to every order record type.
    record_type: Mapped[Optional[str]] = mapped_column(String(20))     # work_order|estimate|None
    event: Mapped[str] = mapped_column(String(24))                     # created|status_changed|closed|reminder
    trigger_status_key: Mapped[Optional[str]] = mapped_column(String(60))
    channel: Mapped[str] = mapped_column(String(16), default="email")  # email|sms|whatsapp
    template_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("notification_templates.id"))
    delay_min: Mapped[int] = mapped_column(Integer, default=0)         # send N minutes after the event
    working_hours_only: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_first: Mapped[bool] = mapped_column(Boolean, default=False)      # try the client's latest chat channel before the configured one
    reminder_offset_min: Mapped[int] = mapped_column(Integer, default=0)  # for event=reminder: N min before scheduled_for
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WorkOrderType(Base):
    """A work-order template: Repair, Warranty, Rental, etc.
    Each type has its own set of custom fields and an initial status."""
    __tablename__ = "work_order_types"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(20), default="#4f8ef7")
    initial_status: Mapped[str] = mapped_column(String(50), default="new")
    default_asset_type: Mapped[str] = mapped_column(String(50), default="Device")  # Device|Vehicle|Tool|Appliance
    sched_default_duration: Mapped[int] = mapped_column(Integer, default=60)  # Phase G
    sched_required: Mapped[bool] = mapped_column(Boolean, default=False)  # Phase G: "Scheduled for" mandatory
    requires_signature: Mapped[bool] = mapped_column(Boolean, default=False)  # gate completion on signature
    requires_checklist: Mapped[bool] = mapped_column(Boolean, default=False)  # gate completion on required checklist items
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class InsightCard(Base):
    """Build 144ay — a saved Company Insights card (diagram or trend).
    Configuration only; the data is aggregated live on read."""
    __tablename__ = "insight_cards"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="diagram")   # diagram | trend
    subject: Mapped[str] = mapped_column(String(40))                   # closed_orders | sales | payments
    metric: Mapped[str] = mapped_column(String(40), default="amount")  # count | amount | gross_profit | avg
    group_by: Mapped[Optional[str]] = mapped_column(String(40))        # type|seller|method|location|status_group|month|quarter
    period: Mapped[str] = mapped_column(String(20), default="30d")     # 30d | 24w | 24m (trend) / range key (diagram)
    view: Mapped[str] = mapped_column(String(12), default="chart")     # chart | table (diagram)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CustomField(Base):
    """A configurable field. May belong to a work-order TYPE (type_id set)
    or to a generic entity (client/device) when type_id is null."""
    __tablename__ = "custom_fields"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("work_order_types.id"))
    entity_type: Mapped[str] = mapped_column(String(50))  # order|client|device
    field_key: Mapped[str] = mapped_column(String(100))
    field_label: Mapped[str] = mapped_column(String(200))
    field_type: Mapped[str] = mapped_column(String(30))  # text|textarea|number|select|date|bool|header
    # 144hi — bind a select/multiselect's options to a Directory (by key) instead of a static list
    directory_key: Mapped[Optional[str]] = mapped_column(String(60))
    # 144hj — cascade: filter this directory field's options to children of another field's value
    parent_field_key: Mapped[Optional[str]] = mapped_column(String(100))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    # build 144at: Orderry "Disable editing after creating" — value is set on create then read-only
    lock_after_create: Mapped[bool] = mapped_column(Boolean, default=False)
    # 144gp Phase C — surface this field's value on printouts / the customer portal
    show_on_print: Mapped[bool] = mapped_column(Boolean, default=False)
    # Forms step 2 — "Shows on": the work order screen (job card) and the customer portal / tracking page
    show_on_job_card: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_portal: Mapped[bool] = mapped_column(Boolean, default=False)
    # 144gq — auto-fill source: "asset.<attr>" (from the linked device) or
    # "last_order.<field_key>" (from the client's most recent order). Pre-fills on open.
    autofill_source: Mapped[Optional[str]] = mapped_column(String(80))
    # 144gr — field-gates-status: when this field's value matches, block entering
    # `gate_status_key` until it clears (shared trigger vocabulary with upsell).
    gate_status_key: Mapped[Optional[str]] = mapped_column(String(50))
    gate_comparator: Mapped[Optional[str]] = mapped_column(String(6))   # is|is_not|lt|gt
    gate_value: Mapped[Optional[str]] = mapped_column(String(200))
    gate_threshold: Mapped[Optional[float]] = mapped_column(Float)
    placeholder: Mapped[Optional[str]] = mapped_column(String(200))
    options: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 144gp — conditional visibility: show this field only when another field on the
    # same form matches. One condition, one operator (Smart Fields spec, Phase A).
    show_when_field_key: Mapped[Optional[str]] = mapped_column(String(100))
    show_when_op: Mapped[Optional[str]] = mapped_column(String(10))   # is|is_not|gt|lt|filled
    show_when_value: Mapped[Optional[str]] = mapped_column(String(200))
    # 144gp Phase B — action rule: a field value can auto-draft an upsell through the
    # same shared surface as checklist items. One rule per field.
    upsell_ref_id: Mapped[Optional[str]] = mapped_column(String)
    upsell_kind: Mapped[Optional[str]] = mapped_column(String(20))         # service|product|custom
    upsell_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))
    upsell_comparator: Mapped[Optional[str]] = mapped_column(String(6))    # is|is_not|lt|gt
    upsell_value: Mapped[Optional[str]] = mapped_column(String(200))       # match value (is/is_not)
    upsell_threshold: Mapped[Optional[float]] = mapped_column(Float)       # numeric (lt/gt)

class FormLayout(Base):
    """144da — Forms Editor system-field layout. One row per (org, entity, type_id).
    `data` holds the ordered system-field settings: [{key, visible, required, sort}].
    Entities: order|invoice|contact|product|vehicle. type_id only used for order."""
    __tablename__ = "form_layouts"
    __table_args__ = (UniqueConstraint("org_id", "entity", "type_id", name="uq_form_layout"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    entity: Mapped[str] = mapped_column(String(32))
    type_id: Mapped[Optional[str]] = mapped_column(String)  # work-order type (nullable); '' for none
    data: Mapped[Optional[list]] = mapped_column(JSON, default=list)

class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_org_created", "org_id", "created_at"),)   # phase 4 perf: hot lookup path
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    actor_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[Optional[str]] = mapped_column(String)
    diff: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EventFeedItem(Base):
    """Polymorphic event feed — attaches to a work order OR a client.

    item_type: comment | photo | signature | file | system
    visibility: private | client  (client = shown on public order page)
    """
    __tablename__ = "event_feed_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))   # order | client
    entity_id: Mapped[str] = mapped_column(String, index=True)
    item_type: Mapped[str] = mapped_column(String(20))     # comment|photo|signature|file|system
    body: Mapped[Optional[str]] = mapped_column(Text)       # comment text / system message
    # For photo/signature/file: data URL or base64 payload kept inline for the demo
    media_data: Mapped[Optional[str]] = mapped_column(Text)
    media_blob_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("media_blobs.id"))
    media_name: Mapped[Optional[str]] = mapped_column(String(300))
    media_mime: Mapped[Optional[str]] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    author_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    author_name: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Recommendation(Base):
    """Build 144bd — upsell / recommended service or product on a work order.

    Anyone internal (role perm ``can_recommend``, default-allow) drafts an item;
    it stays staff-only (status=draft) until published to the customer (status=sent).
    The customer approves single or multiple items on the track/portal page; an
    approved item auto-creates the matching OrderLine so it flows into billing.

    status:  draft (internal) | sent (published to client) | approved | declined
    kind:    service | product | custom   (drives the created OrderLine's type)
    """
    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_org_remind_after", "org_id", "remind_after"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    note: Mapped[Optional[str]] = mapped_column(Text)          # "why we recommend this"
    kind: Mapped[str] = mapped_column(String(20), default="custom")   # service|product|custom
    ref_id: Mapped[Optional[str]] = mapped_column(String)      # linked service/product id
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    media_data: Mapped[Optional[str]] = mapped_column(Text)    # inline photo proof (data URL)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    order_line_id: Mapped[Optional[str]] = mapped_column(String)  # set once approved
    # Several items per job: which of the job's items this is for (null = the whole job).
    order_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("order_items.id"), index=True)
    urgency: Mapped[Optional[str]] = mapped_column(String(10))          # now | soon | None (plain words on the page)
    decline_reason: Mapped[Optional[str]] = mapped_column(String(20))   # not_now (deferred, raise again later) | not_needed
    remind_after: Mapped[Optional[date]] = mapped_column(Date)          # set when the customer says "not now"
    decided_by_name: Mapped[Optional[str]] = mapped_column(String(200)) # name typed when confirming
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | checklist
    source_ref_id: Mapped[Optional[str]] = mapped_column(String)  # e.g. order_checklist_item id (idempotency + attribution)
    author_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    author_name: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class InvoiceLine(Base):
    """Lets one invoice bill a SUBSET of an order's lines (multi-invoice per order),
    OR carry a free-form catalog/manual line (Build 144ed — Orderry-style "Add
    labors, services, products" on the invoice itself). When ``order_line_id`` is
    null the row is a free line authored on the invoice; ``kind`` records what it
    is (service|product|labor|custom) and ``source_id`` the catalog item it came
    from (null for a hand-typed line)."""
    __tablename__ = "invoice_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    invoice_id: Mapped[str] = mapped_column(String, ForeignKey("invoices.id"), index=True)
    order_line_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("order_lines.id"))
    kind: Mapped[str] = mapped_column(String(16), default="custom")            # 144ed
    source_id: Mapped[Optional[str]] = mapped_column(String)                    # 144ed: catalog ref
    sort_order: Mapped[int] = mapped_column(Integer, default=0)                 # 144ed
    description: Mapped[str] = mapped_column(String(500), default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)


class StatusTransition(Base):
    """Allowed status moves (from -> to), optionally restricted to a role."""
    __tablename__ = "status_transitions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    from_status_id: Mapped[str] = mapped_column(String, ForeignKey("order_statuses.id"))
    to_status_id: Mapped[str] = mapped_column(String, ForeignKey("order_statuses.id"))
    role_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("roles.id"))  # null = all roles


class AssetMovement(Base):
    """Logs an asset moving between warehouses/zones, usually driven by a status change.

    Build 120: also used as the asset's lifecycle log — ``event_type`` distinguishes a
    zone move (the original use) from a transfer, write-off, ownership change or creation,
    and ``note``/``created_by_id`` capture who did it and why, so the History tab shows a
    full audit trail with dates, users and detail.
    """
    __tablename__ = "asset_movements"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    asset_id: Mapped[str] = mapped_column(String, ForeignKey("devices.id"))
    from_zone: Mapped[Optional[str]] = mapped_column(String(60))
    to_zone: Mapped[Optional[str]] = mapped_column(String(60))
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))
    status_key: Mapped[Optional[str]] = mapped_column(String(50))
    # Build 120 lifecycle-log fields
    event_type: Mapped[Optional[str]] = mapped_column(String(24))  # zone|transfer|writeoff|owner|create
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    moved_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StockReservation(Base):
    """Reserves stock for an order. Reserved qty is excluded from available."""
    __tablename__ = "stock_reservations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    released: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Warehouse(Base):
    """Build 84 — a warehouse is a first-class entity (Settings → Warehouses).

    Orderry separates two parallel worlds and Tekly now does the same:
      * `kind="product"` — shop inventory (parts, materials, goods for sale).
      * `kind="asset"`   — the physical items being serviced/tracked. Every client
        and employee gets one automatically; the company gets a global one plus
        internal staging warehouses ("Pending repair", "Ready for pickup") and
        external ones for contractors.

    `scope` decides reach: a `location` warehouse is usable only from the location
    it belongs to; a `company` warehouse is usable from any location.

    Locations still exist — a warehouse lives *at* a location — but stock is now
    addressed by warehouse. Each location keeps one default product warehouse so
    every existing `location_id` call site still resolves (compatibility shim).
    """
    __tablename__ = "warehouses"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="product")   # product|asset
    scope: Mapped[str] = mapped_column(String(10), default="location")  # location|company
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"), index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)   # the location's implicit warehouse
    # ── asset warehouses (Build 90) ──────────────────────────────────────────
    asset_role: Mapped[Optional[str]] = mapped_column(String(16))      # client|employee|internal|external|company
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"), index=True)
    system_key: Mapped[Optional[str]] = mapped_column(String(30), index=True)  # pending_repair|ready_for_pickup
    # ── allowed actions (Build 89) ───────────────────────────────────────────
    allowed_actions: Mapped[Optional[dict]] = mapped_column(
        JSON, default=lambda: ["post", "writeoff", "transfer", "sell", "stocktake"])
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # soft-delete → restorable
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WarehouseAccess(Base):
    """Build 89 — per-employee, per-warehouse permissions. Overrides the role's
    warehouse scope: an employee may be allowed to post here but not write off."""
    __tablename__ = "warehouse_access"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(String, ForeignKey("warehouses.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    actions: Mapped[Optional[dict]] = mapped_column(JSON, default=list)  # subset of allowed_actions
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StorageBin(Base):
    """Bin-level (micro-location) storage within a warehouse (Orderry "bin locations").
    A warehouse divides into zones/shelves, and each bin is the smallest pick unit
    (e.g. A1, B2, C18H036). Bins carry a barcode for scan-based posting/stocktake."""
    __tablename__ = "storage_bins"
    # ii144a: bin name unique per warehouse (case-insensitive), bin code unique per shop — active bins only
    __table_args__ = (Index("uq_storage_bins_name", "location_id", text("coalesce(warehouse_id, '')"),
                            text("lower(name)"), unique=True,
                            sqlite_where=text("active = 1"), postgresql_where=text("active")),
                      Index("uq_storage_bins_barcode", "org_id", "barcode", unique=True,
                            sqlite_where=text("active = 1 AND barcode IS NOT NULL"),
                            postgresql_where=text("active AND barcode IS NOT NULL")))
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))  # the warehouse
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    name: Mapped[str] = mapped_column(String(60))               # e.g. "A001" / "C18H036"
    zone: Mapped[Optional[str]] = mapped_column(String(40))      # section / area
    shelf: Mapped[Optional[str]] = mapped_column(String(40))     # stand / shelf / row
    bin_number: Mapped[Optional[str]] = mapped_column(String(40))
    barcode: Mapped[Optional[str]] = mapped_column(String(60), index=True)   # ii144a: from the bin template (routers/bins.py)
    max_weight_capacity: Mapped[float] = mapped_column(Float, default=0.0)  # kg, 0 = unlimited
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ════════ Payroll & Commissions (Mandate 1) ════════

class CommissionRule(Base):
    """A payroll commission rule. Triggers on an event and pays a flat amount or
    a percentage. Percentage basis can be the line total or the margin
    (price − moving-average cost). Optional category override + seniority
    coefficient multiply the result."""
    __tablename__ = "commission_rules"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))  # None = applies to all
    name: Mapped[str] = mapped_column(String(120), default="Commission")
    event: Mapped[str] = mapped_column(String(30))  # order_created|order_closed|labor_performed|product_sold
    calc_type: Mapped[str] = mapped_column(String(10), default="percent")  # percent|flat
    percent_basis: Mapped[str] = mapped_column(String(10), default="total")  # total|margin
    amount: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)  # % (0-100) or flat money
    coefficient: Mapped[Numeric] = mapped_column(Numeric(8, 4), default=1)  # seniority multiplier
    category: Mapped[Optional[str]] = mapped_column(String(120))  # optional product/labor category override
    # ── Build 70: rule conditions & timing ────────────────────────────────
    wo_type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("work_order_types.id"))
    calc_timing: Mapped[str] = mapped_column(String(12), default="on_close")   # on_close|immediately|on_status
    trigger_status_key: Mapped[Optional[str]] = mapped_column(String(60))
    term_condition: Mapped[str] = mapped_column(String(10), default="any")      # any|on_time|overdue|urgent
    cancel_if_deleted: Mapped[bool] = mapped_column(Boolean, default=True)
    # ── Build 72: exceptional-commission priority (product → category → profile)
    rule_level: Mapped[str] = mapped_column(String(16), default="generic")      # generic|product|category|service|profile
    product_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("parts.id"))
    service_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("services.id"))  # 144ef: service-level exceptional commission
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EmployeeCoefficient(Base):
    """Build 72 — per-employee seniority coefficient scoped to a product, service,
    or category, applied on top of exceptional commissions. Separate coefficients
    can apply in the work-order vs the sales context (Orderry)."""
    __tablename__ = "employee_coefficients"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    scope_type: Mapped[str] = mapped_column(String(20))   # product|service|product_category|service_category
    scope_ref: Mapped[str] = mapped_column(String(160))   # part id or category name
    context: Mapped[str] = mapped_column(String(10), default="order")  # order|sales
    coefficient: Mapped[Numeric] = mapped_column(Numeric(8, 4), default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WorkSchedule(Base):
    """Build 73 — a planned entry on an employee's work calendar: a shift with
    start/end minutes-of-day, or a whole-day absence. Payroll runs off actual
    timesheets (TimeEntry); this is the plan the timesheet is compared against."""
    __tablename__ = "work_schedules"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(12), default="shift")   # shift|day_off|vacation|sick
    start_min: Mapped[int] = mapped_column(Integer, default=540)     # minutes from midnight (09:00)
    end_min: Mapped[int] = mapped_column(Integer, default=1020)      # 17:00
    note: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EmployeeBalanceLedger(Base):
    """Append-only ledger of every change to an employee's payroll balance.
    Positive = accrual (commission/bonus/base), negative = claw-back/penalty/payout.
    `idempotency_key` blocks the same accrual from posting twice (no double-dipping)."""
    __tablename__ = "employee_balance_ledger"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(20))  # commission|base|bonus|penalty|clawback|payout
    amount: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)  # signed
    event: Mapped[Optional[str]] = mapped_column(String(30))
    rule_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("commission_rules.id"))
    ref_type: Mapped[Optional[str]] = mapped_column(String(40))  # order|order_line|refund|payroll_run
    ref_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True, index=True)
    # Groups a commission with its reversals across close/reopen cycles: every accrual of the
    # same (event, line/order, rule, employee) and every claw-back of it share this key, so the
    # engine can see what is still open (the group's net) instead of trusting one fixed key.
    accrual_key: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    KEY_MAX = 120

    @staticmethod
    def normalize_key(raw: Optional[str]) -> Optional[str]:
        """Keys longer than the 120-char column (e.g. "<event>:<line>:<rule>:<employee>" is three
        UUIDs ≈ 126 chars) are replaced by a stable digest form. Postgres rejected them outright
        — every per-line commission accrual failed — while SQLite silently stored them. Keys
        that fit are unchanged, so existing rows keep matching."""
        if raw is None or len(raw) <= EmployeeBalanceLedger.KEY_MAX:
            return raw
        import hashlib
        return f"{raw[:60]}#sha256:{hashlib.sha256(raw.encode()).hexdigest()}"[:EmployeeBalanceLedger.KEY_MAX]

    @validates("idempotency_key", "accrual_key")
    def _fit_key(self, _key, value):
        return EmployeeBalanceLedger.normalize_key(value)


class PayrollRun(Base):
    """A monthly payroll roll-up per employee: base salary for the period plus the
    sum of ledger commissions/bonuses/penalties, ending in a payout entry."""
    __tablename__ = "payroll_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    base_salary: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    commissions_total: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    hourly_total: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    bonuses_total: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    penalties_total: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    gross_total: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|accrued|paid|void
    accrued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # The cashbox the pay came out of and the expense transaction posted there (P&L opex).
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"))
    cash_txn_id: Mapped[Optional[str]] = mapped_column(String)
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    voided_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    void_reason: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ════════ Serialized Inventory & Cost Layers (Mandate 2) ════════

class StockItemBatch(Base):
    """A distinct cost layer for a (part, warehouse[, bin]) — one row per intake at
    a given unit cost. FIFO/LIFO consume from these layers oldest/newest first."""
    __tablename__ = "stock_item_batches"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"), index=True)
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"))
    po_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("purchase_orders.id"))
    unit_cost: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)  # this layer's cost
    qty_received: Mapped[Numeric] = mapped_column(Numeric(14, 3), default=0)
    qty_remaining: Mapped[Numeric] = mapped_column(Numeric(14, 3), default=0)  # consumed FIFO/LIFO
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, index=True)  # FEFO when set
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class SerializedItem(Base):
    """A unique serialized unit of a product, tracked individually through its
    lifecycle, with a hard link to the batch cost layer it came in on."""
    __tablename__ = "serialized_items"
    # ii144a: a serial is in stock (or reserved on a job) at most once per shop — the same normalised
    # form serial_intake compares (upper-case, no spaces). Sold / written-off history may repeat.
    __table_args__ = (Index("uq_serialized_items_live_serial", "org_id",
                            text("upper(replace(serial_number, ' ', ''))"), unique=True,
                            sqlite_where=text("status IN ('in_stock', 'allocated_to_order')"),
                            postgresql_where=text("status IN ('in_stock', 'allocated_to_order')")),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"), index=True)
    serial_number: Mapped[str] = mapped_column(String(120), index=True)
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"), index=True)
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    batch_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("stock_item_batches.id"))
    po_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("purchase_orders.id"))
    unit_cost: Mapped[Numeric] = mapped_column(Numeric(12, 4), default=0)
    status: Mapped[str] = mapped_column(String(20), default="in_stock")  # in_stock|allocated_to_order|sold|written_off|converted_to_asset
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    converted_asset_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("devices.id"))  # Build 76
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AssetProductConversion(Base):
    """Build 76 — an immutable conversion document.

    Records a serialized product unit becoming a tracked asset, or an asset becoming
    a catalog product. Conversion documents are never deleted: a conversion is
    reversed by performing the opposite conversion, which writes a second document
    (Orderry semantics). `metadata_json` snapshots the field values at conversion
    time so the document stays meaningful even if the product or asset later changes.
    """
    __tablename__ = "asset_product_conversions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(20))   # product_to_asset|asset_to_product
    part_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("parts.id"), index=True)
    serialized_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("serialized_items.id"))
    device_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("devices.id"), index=True)
    owner_before: Mapped[Optional[str]] = mapped_column(String(20))   # company|client|supplier
    owner_after: Mapped[Optional[str]] = mapped_column(String(20))
    template_key: Mapped[Optional[str]] = mapped_column(String(30))
    asset_group_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("asset_groups.id"))
    serial_identifier: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")  # completed|reversed
    reversed_by_id: Mapped[Optional[str]] = mapped_column(String)   # the opposite conversion's id
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)


class NotificationLog(Base):
    """Every notification the system rendered + attempted to deliver. This is the
    communication history shown on the order and the client profile."""
    __tablename__ = "notification_logs"
    __table_args__ = (Index("ix_notification_logs_org_created", "org_id", "created_at"),)   # phase 4 perf: hot lookup path
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    scope: Mapped[str] = mapped_column(String(12), default="client")  # client|employee
    channel: Mapped[str] = mapped_column(String(20))  # sms|email|whatsapp
    trigger_event: Mapped[Optional[str]] = mapped_column(String(60))
    template_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("notification_templates.id"))
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    recipient: Mapped[Optional[str]] = mapped_column(String(200))  # phone or email actually targeted
    subject: Mapped[Optional[str]] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")  # rendered message
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|scheduled|sent|failed|skipped
    rule_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("notification_rules.id", ondelete="SET NULL"))  # Build 80; SET NULL so deleting a rule that already fired keeps its log history
    send_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)  # due time for delayed/reminder sends
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class TimeEntry(Base):
    """A span of labor time, tracked against a work order (or non-job time when
    order_id is null). Rates are snapshotted at start so later raises don't
    rewrite history. At most one 'running' entry per employee at a time."""
    __tablename__ = "time_entries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    # 144v: attribute a tech's time to a specific labour/service line on the order,
    # so several people on one WO each roll up to their own line for cost/commission.
    order_line_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("order_lines.id"), index=True)
    # build 144x: the work-order timer can also run against a standalone task
    task_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("tasks.id", ondelete="SET NULL"), index=True)  # 144ep: tasks are hard-deleted; keep the time (payroll history)
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    category: Mapped[str] = mapped_column(String(16), default="job")  # job|task|break|admin|other
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_rate: Mapped[float] = mapped_column(Float, default=0.0)      # per hour, snapshot
    billable_rate: Mapped[float] = mapped_column(Float, default=0.0)  # per hour, snapshot
    note: Mapped[Optional[str]] = mapped_column(Text)
    gps_lat: Mapped[Optional[float]] = mapped_column(Float)  # capture at clock-in if permission granted
    gps_lng: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(10), default="timer")  # timer|manual
    state: Mapped[str] = mapped_column(String(10), default="running")  # running|stopped|approved|void
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # 144az — a tech can flag their own entry as disputed (wrong hours); a manager resolves it.
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    dispute_note: Mapped[Optional[str]] = mapped_column(Text)
    approved_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TimeEntryAudit(Base):
    """144ay — append-only edit history for a time entry. Makes the job clock usable as the
    system of record for payable hours (ADR 144ax → Option B): every edit/void/approve/reopen
    writes an immutable row of who changed what, old→new. Never updated or deleted."""
    __tablename__ = "time_entry_audits"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    time_entry_id: Mapped[str] = mapped_column(String, ForeignKey("time_entries.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))        # create|edit|void|approve|reopen
    field: Mapped[Optional[str]] = mapped_column(String(32))   # which field (edit only)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    actor_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class WebhookEndpoint(Base):
    """A customer-registered URL that receives event callbacks."""
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(64), default=_uuid)
    events: Mapped[str] = mapped_column(String(500), default="*")  # csv of events or *
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WebhookDelivery(Base):
    """One attempt to deliver one event to one endpoint (the delivery log)."""
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    endpoint_id: Mapped[str] = mapped_column(String, ForeignKey("webhook_endpoints.id"), index=True)
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|sent|failed
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Lead(Base):
    """An inquiry — the pre-order pipeline (Orderry Inquiries/Lead Funnel).
    Each lead carries a response-time SLA; "won" converts into a work order,
    creating the client on the fly if the contact isn't in CRM yet."""
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    name: Mapped[str] = mapped_column(String(200))             # contact name as given
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(30), default="phone")  # phone|walk_in|whatsapp|instagram|web|other
    inquiry: Mapped[Optional[str]] = mapped_column(Text)        # what they asked for
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|in_progress|won|lost
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    manager_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # respond-by deadline
    converted_order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PriceType(Base):
    """A named markup over cost price (Orderry Settings > Prices and discounts).
    Computed price = cost basis x (1 + markup_pct/100). Products use avg_cost
    (falling back to cost_price); services use their cost_price. Selectable on
    lines and assignable to a client as their personal price level."""
    __tablename__ = "price_types"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(120))
    markup_pct: Mapped[float] = mapped_column(Float, default=0.0)
    location_scope: Mapped[str] = mapped_column(String(10), default="all")  # all|selected
    location_ids: Mapped[Optional[str]] = mapped_column(Text)  # JSON list when scope=selected
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 144hb — optional promotional window: outside [starts_at, ends_at] the price is
    # not offered (auto-expiring promos). Null bound = open-ended on that side.
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Sale(Base):
    """Over-the-counter sale (Orderry Sales page): services + products sold
    without a work order. One warehouse per sale; estimated profit = total − cost."""
    __tablename__ = "sales"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    sale_number: Mapped[str] = mapped_column(String(20))
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    seller_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"))
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"))
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total_cost: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    discount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # whole-sale discount amount
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    coupon_id: Mapped[Optional[str]] = mapped_column(String)          # the coupon used, so a void can give the use back
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)   # deleted ("Delete sales"): kept, but reversed
    voided_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    void_reason: Mapped[Optional[str]] = mapped_column(String(300))


class SaleLine(Base):
    __tablename__ = "sale_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    sale_id: Mapped[str] = mapped_column(String, ForeignKey("sales.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # product|service
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[str] = mapped_column(String(300), default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    cost_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)  # exact FIFO COGS for products
    discount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)    # pct
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    # What the line drew, so a void puts it back exactly: {"layers": [{batch_id, qty}],
    # "holds": [{hold_id, qty}]} — FIFO cost layers and other jobs' reservations (override).
    stock_draws: Mapped[Optional[dict]] = mapped_column(JSON)


class Stocktake(Base):
    """Guided count: snapshot system qty → enter counted → post one adjustment."""
    __tablename__ = "stocktakes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)  # Build 84
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"))
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress|posted
    note: Mapped[Optional[str]] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(12), default="full")  # Build 91: full|partial
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class StocktakeLine(Base):
    __tablename__ = "stocktake_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    stocktake_id: Mapped[str] = mapped_column(String, ForeignKey("stocktakes.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    system_qty: Mapped[float] = mapped_column(Float, default=0.0)
    counted_qty: Mapped[Optional[float]] = mapped_column(Float)
    variance: Mapped[float] = mapped_column(Float, default=0.0)
    # ── Build 91 ─────────────────────────────────────────────────────────────
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"))
    serialized_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("serialized_items.id"))
    is_unknown: Mapped[bool] = mapped_column(Boolean, default=False)   # found on the shelf, not in the system
    serial_mismatch: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text)

class ClientReturn(Base):
    """Goods coming back from a client (separate from the money refund)."""
    __tablename__ = "client_returns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ClientReturnLine(Base):
    __tablename__ = "client_return_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    return_id: Mapped[str] = mapped_column(String, ForeignKey("client_returns.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    restock: Mapped[bool] = mapped_column(Boolean, default=True)


class Refund(Base):
    """Money going back to a client (separate from the goods return)."""
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))
    return_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("client_returns.id"))
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    payment_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("payments.id"))  # the refund Payment
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)   # deleted ("Delete refunds"): kept, but reversed
    voided_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    void_reason: Mapped[Optional[str]] = mapped_column(String(300))


class StockTransfer(Base):
    """Inter-branch transfer with transit statuses (Orderry Mar-2026):
    draft -> ready (reserves at source) -> in_transit (stock held on the transfer)
    -> received (arrived, verify) -> completed (lands at destination).
    Cancel before shipping returns the reservation; after shipping you choose
    the warehouse that takes the goods back."""
    __tablename__ = "stock_transfers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    transfer_number: Mapped[str] = mapped_column(String(20))
    from_location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    to_location_id: Mapped[str] = mapped_column(String, ForeignKey("locations.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|ready|in_transit|received|completed|canceled
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    from_warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"))  # Build 93
    to_warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"))    # Build 93
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # Build 93: left the origin
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime)    # Build 93: confirmed at destination


class StockTransferLine(Base):
    __tablename__ = "stock_transfer_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    transfer_id: Mapped[str] = mapped_column(String, ForeignKey("stock_transfers.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    qty: Mapped[float] = mapped_column(Float, default=1.0)


# ════════ Phase E — Customer Feedback ════════
    qty_received: Mapped[Optional[float]] = mapped_column(Float)  # Build 93: what actually turned up

class FeedbackSettings(Base):
    """Per-org feedback config: which rating system + per-band answer messages.
    bands is a JSON list like [{"min":1,"max":3,"message":"..."}]."""
    __tablename__ = "feedback_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    system: Mapped[str] = mapped_column(String(20), default="five_point")  # five_point|like|nps
    trigger_status_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("order_statuses.id"))
    heading: Mapped[str] = mapped_column(String(200), default="How did we do?")
    subheading: Mapped[Optional[str]] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(String(300), default="How would you rate your experience?")
    clarify_question: Mapped[Optional[str]] = mapped_column(String(300), default="Tell us more")
    bands: Mapped[dict] = mapped_column(JSON, default=dict)  # {"low":"..","mid":"..","high":".."}
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    # 144ix — public review CTA: happy customers get a one-tap link to the shop's
    # Google (or any) review page. Shown to high raters only; everyone still rates.
    google_review_url: Mapped[Optional[str]] = mapped_column(String(400))


class Feedback(Base):
    """A submitted customer rating for an order."""
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    token: Mapped[str] = mapped_column(String(40), index=True, default=lambda: _uuid().replace("-", "")[:16])
    system: Mapped[str] = mapped_column(String(20), default="five_point")
    rating: Mapped[Optional[int]] = mapped_column(Integer)  # 1-5, 0/1 for like, 0-10 for nps
    comment: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ════════ Phase F — Documents, Templates & Barcodes ════════

class DocumentTemplate(Base):
    """A print/PDF template with {variables}. doc_type groups it (Orderry families)."""
    __tablename__ = "document_templates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(200))
    doc_type: Mapped[str] = mapped_column(String(30), default="work_order")  # work_order|estimate|invoice|receipt|act|label
    body_html: Mapped[str] = mapped_column(Text, default="")  # with {variables}
    auto_print_on: Mapped[Optional[str]] = mapped_column(String(20))  # create|close|none
    page_size: Mapped[str] = mapped_column(String(16), default="a4")   # a4|letter|label|receipt80|receipt58 — 144n/144ia
    # 144ia — exact physical size for true-to-size printing on any printer (labels/receipts/custom)
    page_w_mm: Mapped[Optional[float]] = mapped_column(Float)
    page_h_mm: Mapped[Optional[float]] = mapped_column(Float)
    margin_mm: Mapped[Optional[float]] = mapped_column(Float)
    font_family: Mapped[str] = mapped_column(String(40), default="Sans-Serif")
    # build 144o: JSON list of status keys this doc may be printed in. null/empty = any status.
    print_statuses: Mapped[Optional[str]] = mapped_column(Text)
    # opa: label templates — which product field {Barcode} encodes, and the symbology per field
    barcode_field: Mapped[Optional[str]] = mapped_column(String(12))   # barcode|sku|code|mpn (null = first filled)
    barcode_types: Mapped[Optional[dict]] = mapped_column(JSON)        # {"mpn": "code128", "barcode": "ean13"}
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GeneratedDocument(Base):
    """A rendered document instance attached to an order."""
    __tablename__ = "generated_documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"))
    template_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("document_templates.id"))
    name: Mapped[str] = mapped_column(String(200), default="Document")
    rendered_html: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ════════ Phase G — Location Resources (bays/rooms/equipment) ════════

class Resource(Base):
    """A schedulable location resource (bay, room, machine) — Orderry 'By resource'."""
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), default="bay")  # bay|room|equipment|other
    color: Mapped[str] = mapped_column(String(20), default="#2cd4c0")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# Import trigger for init_db
class ApiKey(Base):
    """A token for the public REST API (Phase 2B). The plaintext key is shown
    once at creation; only its sha256 hash is stored."""
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="API key")
    prefix: Mapped[str] = mapped_column(String(16), index=True)      # rok_xxxxxxxx (shown in UI)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)    # sha256 of the full key
    scopes: Mapped[str] = mapped_column(String(300), default="read,write")  # csv
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PaymentIntent(Base):
    """A payment being collected through an external gateway (Phase 2A). Tracks
    the checkout/charge lifecycle and, on success, links to the reconciled Payment."""
    __tablename__ = "payment_intents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    invoice_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("invoices.id"), index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"))  # target cashbox
    provider: Mapped[str] = mapped_column(String(20), default="simulated")  # stripe|simulated
    provider_ref: Mapped[Optional[str]] = mapped_column(String(120), index=True)  # session/intent id
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="usd")
    status: Mapped[str] = mapped_column(String(16), default="created")  # created|pending|succeeded|failed|canceled
    checkout_url: Mapped[Optional[str]] = mapped_column(String(600))
    provider_link_id: Mapped[Optional[str]] = mapped_column(String(120))   # the link itself (Square needs it to delete one)
    payment_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("payments.id"), index=True, unique=True)
    error: Mapped[Optional[str]] = mapped_column(Text)
    viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # A2: first time the client opened the link
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class QboConnection(Base):
    """QuickBooks Online OAuth connection state for an org (Phase 2C)."""
    __tablename__ = "qbo_connections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True, unique=True)
    realm_id: Mapped[Optional[str]] = mapped_column(String(64))
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    environment: Mapped[str] = mapped_column(String(12), default="sandbox")
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    state_nonce: Mapped[Optional[str]] = mapped_column(String(64))  # CSRF nonce for the OAuth round-trip
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class IntegrationMapping(Base):
    """Maps a local record to its counterpart id in an external system (Phase 2C)."""
    __tablename__ = "integration_mappings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    integration: Mapped[str] = mapped_column(String(20), default="qbo")
    entity_type: Mapped[str] = mapped_column(String(20))   # invoice|payment|contact
    local_id: Mapped[str] = mapped_column(String, index=True)
    remote_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IntegrationSyncLog(Base):
    """One sync attempt for one record (Phase 2C). The audit trail of the sync."""
    __tablename__ = "integration_sync_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    integration: Mapped[str] = mapped_column(String(20), default="qbo")
    entity_type: Mapped[str] = mapped_column(String(20))
    local_id: Mapped[Optional[str]] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String(8), default="push")  # push|pull
    status: Mapped[str] = mapped_column(String(12), default="queued")  # ok|failed|skipped|queued
    remote_id: Mapped[Optional[str]] = mapped_column(String(64))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Category(Base):
    """A shared, hierarchical category tree reused across products, services and
    bundles (Orderry keeps a tree per catalog type with an 'All ...' root).
    entity_type scopes the tree; parent_id builds the hierarchy."""
    __tablename__ = "categories"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(12), index=True)  # product|service|bundle
    name: Mapped[str] = mapped_column(String(160))
    parent_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("categories.id"), index=True)
    auto_deduct: Mapped[bool] = mapped_column(Boolean, default=False)  # bundle categories: deduct products from stock
    # Build 144ao: scope a category tree to an industry template (null = shared across templates)
    template_key: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StaffNotification(Base):
    """Per-employee in-app notification (powers the technician unread count and
    the push payload). Created by services.push.notify()."""
    __tablename__ = "staff_notifications"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # job.assigned|schedule.changed|chat.message|estimate.approved|appointment.cancelled|payment.received
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    deep_link: Mapped[Optional[str]] = mapped_column(String(300))  # e.g. /tech#/order/<id>
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 144bp: soft dismiss — row kept for history/audit but hidden from the bell + unread count.
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class PushSubscription(Base):
    """A Web Push (browser) subscription for one employee's device."""
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text)            # unique push endpoint URL
    p256dh: Mapped[str] = mapped_column(String(255))       # client public key
    auth: Mapped[str] = mapped_column(String(255))         # client auth secret
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ChecklistTemplate(Base):
    """A reusable checklist (Build 144dp: Orderry-style Template → Steps →
    Sections → typed Fields). `scope` = all|selected; when 'selected', the
    applicable order types are listed in `type_ids` (JSON). `type_id` is kept for
    backward compatibility with the flat 144-era checklist."""
    __tablename__ = "checklist_templates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("work_order_types.id"), index=True)
    scope: Mapped[str] = mapped_column(String(12), default="all")          # all | selected
    type_ids: Mapped[str] = mapped_column(Text, default="[]")               # JSON list of work_order_type ids
    name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # E4: attach automatically when an order enters one of these statuses.
    # List of {"record_type": "work_order"|"estimate", "key": OrderStatus.key}.
    auto_status_keys: Mapped[Optional[list]] = mapped_column(JSON)


class ChecklistStep(Base):
    """A stage of a checklist template (e.g. Receipt → Diagnostics → Handover)."""
    __tablename__ = "checklist_steps"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    template_id: Mapped[str] = mapped_column(String, ForeignKey("checklist_templates.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ChecklistSection(Base):
    """Groups fields by topic within a step. `allow_notes` lets staff add a free
    comment/photo where there is no dedicated field."""
    __tablename__ = "checklist_sections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    step_id: Mapped[str] = mapped_column(String, ForeignKey("checklist_steps.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    allow_notes: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ChecklistTemplateItem(Base):
    """A typed field inside a section. `field_kind` drives the response control;
    `options` (JSON) holds choices for dropdown/multiselect."""
    __tablename__ = "checklist_template_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    template_id: Mapped[str] = mapped_column(String, ForeignKey("checklist_templates.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("checklist_sections.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    # check_result | yes_no | text | textarea | dropdown | multiselect | date | file | catalog_pick
    field_kind: Mapped[str] = mapped_column(String(20), default="check_result")
    options: Mapped[str] = mapped_column(Text, default="[]")               # JSON list of strings
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    client_visible: Mapped[bool] = mapped_column(Boolean, default=False)   # surfaced on the client track page
    affects_result: Mapped[bool] = mapped_column(Boolean, default=True)    # a Fail here fails the checklist
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 144go — smart upsell: a Fail / threshold breach on this item auto-drafts a
    # recommendation for the linked catalog item.
    upsell_ref_id: Mapped[Optional[str]] = mapped_column(String)            # linked service/product id
    upsell_kind: Mapped[Optional[str]] = mapped_column(String(20))         # service | product | custom
    upsell_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))           # snapshot price for the draft line
    upsell_threshold: Mapped[Optional[float]] = mapped_column(Float)       # numeric trigger threshold
    upsell_comparator: Mapped[Optional[str]] = mapped_column(String(4))    # lt | gt | fail


class OrderChecklist(Base):
    """One template instantiated onto a work order. Header for its answers; a job
    can carry several (diagnostic, QA, handover). Snapshot — later template edits
    never touch it."""
    __tablename__ = "order_checklists"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("checklist_templates.id"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(14), default="in_progress")  # in_progress | completed
    result: Mapped[Optional[str]] = mapped_column(String(10))               # passed | failed | None
    author_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class OrderChecklistItem(Base):
    """A field instantiated onto one work-order checklist (snapshot, so editing a
    template never mutates a job's history). `value` holds the typed answer as
    JSON; `file_ids` references uploaded media."""
    __tablename__ = "order_checklist_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    checklist_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("order_checklists.id", ondelete="CASCADE"), index=True)
    step_title: Mapped[Optional[str]] = mapped_column(String(200))
    section_title: Mapped[Optional[str]] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(300))
    field_kind: Mapped[str] = mapped_column(String(20), default="check_result")
    options: Mapped[str] = mapped_column(Text, default="[]")
    value: Mapped[Optional[str]] = mapped_column(Text)                      # JSON: {"v": ...}
    file_ids: Mapped[str] = mapped_column(Text, default="[]")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    client_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    affects_result: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # 144go — snapshotted upsell config (see ChecklistTemplateItem)
    upsell_ref_id: Mapped[Optional[str]] = mapped_column(String)
    upsell_kind: Mapped[Optional[str]] = mapped_column(String(20))
    upsell_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))
    upsell_threshold: Mapped[Optional[float]] = mapped_column(Float)
    upsell_comparator: Mapped[Optional[str]] = mapped_column(String(4))


class NotificationPreference(Base):
    """Per-employee push/notification opt-out by kind. Absence of a row means
    enabled (notifications are on by default)."""
    __tablename__ = "notification_preferences"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class GiftCard(Base):
    __tablename__ = "gift_cards"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)          # e.g. GC-8F3K2P
    initial_value: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    balance: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GiftCardLedger(Base):
    """Every load (+) and redemption (−); balance is always derivable."""
    __tablename__ = "gift_card_ledger"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    gift_card_id: Mapped[str] = mapped_column(String, ForeignKey("gift_cards.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))                        # + load / − redeem
    ref_type: Mapped[Optional[str]] = mapped_column(String(20))         # order|sale|manual
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    note: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    # 144bi: a coupon issued to a specific returning client (null = public code).
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(10), default="pct")        # pct|fixed
    value: Mapped[float] = mapped_column(Float, default=0.0)
    min_total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)           # 0 = unlimited
    uses: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LoyaltyLedger(Base):
    """Per-client points: + earn on paid invoices, − burn at redemption."""
    __tablename__ = "loyalty_ledger"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    client_id: Mapped[str] = mapped_column(String, ForeignKey("clients.id"), index=True)
    points: Mapped[float] = mapped_column(Float)                        # + earn / − burn
    ref_type: Mapped[Optional[str]] = mapped_column(String(20))         # payment|order|manual
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    note: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ── Phase 1: numbering engine + legal entities ──────────────────────────────
class NumberSequence(Base):
    """A per-scope document counter driven by a token template
    ({D}{M}{Y}{C:n}). scope_key: 'org' | 'loc:{id}' | 'entity:{id}'."""
    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("org_id", "doc_type", "scope_key", name="uq_numseq_scope"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(20))     # order|invoice|inquiry|sale|po|transfer
    scope_key: Mapped[str] = mapped_column(String(60), default="org")
    template: Mapped[str] = mapped_column(String(60))
    next_value: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class LegalEntity(Base):
    """A billing entity used to issue invoices with its own details + numbering."""
    __tablename__ = "legal_entities"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(Text)
    tax_id: Mapped[Optional[str]] = mapped_column(String(60))
    details: Mapped[Optional[str]] = mapped_column(Text)      # free-form for print
    # 144hk — richer legal-entity print variables (Orderry parity)
    registration_no: Mapped[Optional[str]] = mapped_column(String(80))
    vat_no: Mapped[Optional[str]] = mapped_column(String(80))
    bank_name: Mapped[Optional[str]] = mapped_column(String(160))
    bank_account: Mapped[Optional[str]] = mapped_column(String(120))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(160))
    director: Mapped[Optional[str]] = mapped_column(String(160))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ── Phase 2: tax engine depth ───────────────────────────────────────────────
class TaxGroup(Base):
    __tablename__ = "tax_groups"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    member_tax_ids: Mapped[str] = mapped_column(Text, default="[]")   # JSON list of Tax.id
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaxRule(Base):
    """Ordered default tax stack per document family. Lower priority = applied
    first / highest precedence (Orderry: 'first tax has the highest priority')."""
    __tablename__ = "tax_rules"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    doc_family: Mapped[str] = mapped_column(String(20))    # tickets_sales|posting
    tax_id: Mapped[str] = mapped_column(String, ForeignKey("taxes.id"))
    priority: Mapped[int] = mapped_column(Integer, default=0)


class TaxException(Base):
    """Override the default stack for a specific labor/service/product/category."""
    __tablename__ = "tax_exceptions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    doc_family: Mapped[str] = mapped_column(String(20))    # tickets_sales|posting
    target_type: Mapped[str] = mapped_column(String(20))   # labor|service|product|category
    target_id: Mapped[str] = mapped_column(String)
    tax_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("taxes.id"))  # None + exempt=True => no tax
    exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    taxation_system: Mapped[Optional[str]] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class PriceTypeItem(Base):
    """Explicit price for a catalog item within a price type; overrides the
    cost x markup derivation when present."""
    __tablename__ = "price_type_items"
    __table_args__ = (UniqueConstraint("price_type_id", "item_type", "item_id", name="uq_ptitem"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    price_type_id: Mapped[str] = mapped_column(String, ForeignKey("price_types.id"), index=True)
    item_type: Mapped[str] = mapped_column(String(10))   # product|service
    item_id: Mapped[str] = mapped_column(String)
    price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)


class PriceTypeTier(Base):
    """144he — quantity break: at or above min_qty, this price type uses tier_markup_pct
    instead of its base markup. Highest matching min_qty wins."""
    __tablename__ = "price_type_tiers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    price_type_id: Mapped[str] = mapped_column(String, ForeignKey("price_types.id"), index=True)
    min_qty: Mapped[float] = mapped_column(Float, default=1.0)
    markup_pct: Mapped[float] = mapped_column(Float, default=0.0)



class Directory(Base):
    """A named, org-customizable reference list (asset types, manufacturers,
    cancel reasons, lead sources...) that feeds dropdowns app-wide. `key` is a
    stable slug; some directories are system-seeded and cannot be deleted."""
    __tablename__ = "directories"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_directory_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))          # asset_type|manufacturer|cancel_reason|lead_source|...
    name: Mapped[str] = mapped_column(String(80))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DirectoryItem(Base):
    __tablename__ = "directory_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    directory_id: Mapped[str] = mapped_column(String, ForeignKey("directories.id"), index=True)
    value: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[Optional[str]] = mapped_column(String)   # for hierarchical lists (mfr -> model)
    # 144hi — per-location visibility (Orderry): null = visible at every location
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ─────────────────────────────────────────────
# INDUSTRY TEMPLATES — per-template asset directories
#
# These directories are ALWAYS scoped by (org_id, template_key) so an industry's
# asset structure never leaks into another. A phone shop's brands/models can never
# appear under Small Engine, and "Yamaha" can exist independently under
# powersports, marine, small_engine and bike as distinct rows. The code-level
# source of truth for labels, field schemas, default groups and scan kinds lives
# in backend/services/industry.py; these tables hold the org-editable data.
# ─────────────────────────────────────────────

class AssetGroup(Base):
    """A per-template asset category (e.g. Phone/Tablet for mobile, Car/Truck for
    auto, Boat/Trailer for marine). Scoped by org + template_key."""
    __tablename__ = "asset_groups"
    __table_args__ = (UniqueConstraint("org_id", "template_key", "key", name="uq_asset_group_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    template_key: Mapped[str] = mapped_column(String(30), index=True)   # mobile_phone|computer|auto|...
    key: Mapped[str] = mapped_column(String(50))          # stable slug, e.g. "phone"
    label: Mapped[str] = mapped_column(String(80))        # display, e.g. "Phone"
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # seeded default group
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AssetBrand(Base):
    """A brand within a template (optionally narrowed to one asset group). Two
    rows named the same under different templates are intentionally distinct."""
    __tablename__ = "asset_brands"
    __table_args__ = (
        UniqueConstraint("org_id", "template_key", "asset_group_id", "name",
                         name="uq_asset_brand_name"),
        # group-specific brands are covered above; this closes the template-wide
        # case (asset_group_id NULL), where SQLite treats NULLs as distinct.
        Index("uq_asset_brand_tpl_wide", "org_id", "template_key", "name", unique=True,
              sqlite_where=text("asset_group_id IS NULL"),
              postgresql_where=text("asset_group_id IS NULL")),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    template_key: Mapped[str] = mapped_column(String(30), index=True)
    asset_group_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("asset_groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AssetModel(Base):
    """A model under a brand, within a template (and optionally a group)."""
    __tablename__ = "asset_models"
    __table_args__ = (UniqueConstraint("org_id", "template_key", "brand_id", "name",
                                       name="uq_asset_model_name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    template_key: Mapped[str] = mapped_column(String(30), index=True)
    brand_id: Mapped[str] = mapped_column(String, ForeignKey("asset_brands.id"), index=True)
    asset_group_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("asset_groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    year_from: Mapped[Optional[int]] = mapped_column(Integer)
    year_to: Mapped[Optional[int]] = mapped_column(Integer)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AssetIdentifierRule(Base):
    """Which identifier kind is primary (and which are allowed) for a template,
    optionally overridden per asset group. Drives template-aware scanning so a
    phone never shows VIN and a car never shows IMEI. asset_group_id null = the
    template-wide default rule."""
    __tablename__ = "asset_identifier_rules"
    __table_args__ = (UniqueConstraint("org_id", "template_key", "asset_group_id",
                                       name="uq_asset_ident_rule"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    template_key: Mapped[str] = mapped_column(String(30), index=True)
    asset_group_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("asset_groups.id"), index=True)
    primary_kind: Mapped[str] = mapped_column(String(16))          # imei|vin|hin|serial|asset_tag|barcode|engine_serial
    allowed_kinds: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
class XeroConnection(Base):
    """QuickBooks Online OAuth connection state for an org (Phase 2C)."""
    __tablename__ = "xero_connections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True, unique=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64))
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    environment: Mapped[str] = mapped_column(String(12), default="sandbox")
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    state_nonce: Mapped[Optional[str]] = mapped_column(String(64))  # CSRF nonce for the OAuth round-trip
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class MediaBlob(Base):
    """B3: an offloaded media object. The row holds metadata + a storage key;
    the bytes live in the configured backend (local FS or S3), not the DB.
    When MEDIA_BACKEND=db (legacy), `inline_data` still holds the payload so
    nothing breaks during migration."""
    __tablename__ = "media_blobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    backend: Mapped[str] = mapped_column(String(10), default="db")   # db|local|s3
    storage_key: Mapped[Optional[str]] = mapped_column(String(300))  # path/object key when offloaded
    inline_data: Mapped[Optional[str]] = mapped_column(Text)         # base64/data-url when backend=db
    mime: Mapped[Optional[str]] = mapped_column(String(100))
    name: Mapped[Optional[str]] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class EmailConnection(Base):
    """A6: per-org OAuth email account (Gmail/Outlook) for sending notifications
    from the shop's own address. Gated like every provider — inert until
    connected. Tokens are stored for the send path; refresh handled at send time."""
    __tablename__ = "email_connections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(10))              # gmail|outlook
    email_address: Mapped[Optional[str]] = mapped_column(String(200))
    sender_name: Mapped[Optional[str]] = mapped_column(String(120))
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    state_nonce: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class Call(Base):
    """A7 — a VoIP call record (inbound/outbound), linked to a client if matched.
    Populated by the provider webhook; the provider itself is a gated seam."""
    __tablename__ = "calls"
    # D1: one row per provider call (Twilio CallSid), so webhook retries never duplicate.
    __table_args__ = (Index("uq_calls_org_provider_sid", "org_id", "provider_sid", unique=True),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="manual")   # aircall|twilio|zadarma|manual
    direction: Mapped[str] = mapped_column(String(10), default="inbound") # inbound|outbound
    from_number: Mapped[Optional[str]] = mapped_column(String(40))
    to_number: Mapped[Optional[str]] = mapped_column(String(40))
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"))
    employee_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(20), default="completed")  # ringing|completed|missed|voicemail
    duration_s: Mapped[int] = mapped_column(Integer, default=0)
    recording_url: Mapped[Optional[str]] = mapped_column(String(400))
    external_id: Mapped[Optional[str]] = mapped_column(String(120))
    provider_sid: Mapped[Optional[str]] = mapped_column(String(64))       # D1: Twilio CallSid
    # ── Build 105: VoIP + AI depth ────────────────────────────────────────────
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    sentiment: Mapped[Optional[str]] = mapped_column(String(12))     # positive|neutral|negative
    ai_processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Campaign(Base):
    """A7 — a marketing campaign (SMS/email blast to a client segment)."""
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    channel: Mapped[str] = mapped_column(String(10), default="sms")       # sms|email
    subject: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    segment: Mapped[str] = mapped_column(String(20), default="all")       # all|loyalty|lapsed
    status: Mapped[str] = mapped_column(String(20), default="draft")      # draft|sent
    recipients: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SsoConnection(Base):
    """SSO groundwork — per-org identity provider config (SAML/OIDC). Metadata
    only; the actual assertion/token exchange is a gated seam (needs an IdP)."""
    __tablename__ = "sso_connections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    protocol: Mapped[str] = mapped_column(String(10), default="oidc")     # oidc|saml
    display_name: Mapped[str] = mapped_column(String(120), default="")
    issuer: Mapped[Optional[str]] = mapped_column(String(300))            # OIDC issuer / SAML entityID
    client_id: Mapped[Optional[str]] = mapped_column(String(200))
    client_secret: Mapped[Optional[str]] = mapped_column(String(300))
    metadata_url: Mapped[Optional[str]] = mapped_column(String(400))
    email_domain: Mapped[Optional[str]] = mapped_column(String(120))     # auto-route logins from this domain
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AutomationRule(Base):
    """Build 144w — workflow automation: WHEN an event happens (and optional
    conditions match) THEN run an action beyond messaging.

    Complements NotificationRule (which owns the *messaging* side — delays,
    working hours, reminders). This engine owns the *do-something* actions
    Orderry's automation offers: create a task, assign a technician, tag the
    order, or call a webhook. Conditions and action_config are JSON blobs so the
    schema stays stable as we add fields.
    """
    __tablename__ = "automation_rules"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(140))
    # order_created|status_changed|estimate_approved|payment_received|order_overdue|low_stock
    event: Mapped[str] = mapped_column(String(32), index=True)
    trigger_status: Mapped[Optional[str]] = mapped_column(String(60))   # for status_changed
    conditions: Mapped[Optional[str]] = mapped_column(Text)             # JSON list of {field,op,value}
    action_type: Mapped[str] = mapped_column(String(24))               # create_task|assign|add_tag|webhook|notify
    action_config: Mapped[Optional[str]] = mapped_column(Text)         # JSON action params
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    last_fired_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


all_models = [
    Organisation, Location, Role, Employee, Shift, WageRecord,
    Client, Device, OrderStatus, Order, OrderLine, OrderItem, OrderStatusLog, OrderComment,
    LegalEntity, NumberSequence,
    Part, ProductBarcode, StockLevel, StockMovement, Supplier, PurchaseOrder, POLine, Service, SavedFilter, Tax,
    Invoice, InvoiceLine, Payment, CashShift, Expense, Account, CashFlowItem, Transaction,
    Task, NotificationTemplate, CustomField, AuditLog, EventFeedItem,
    WorkOrderType, StatusTransition, AssetMovement, StockReservation, StorageBin,
    CommissionRule, EmployeeBalanceLedger, PayrollRun, StockItemBatch, SerializedItem, NotificationLog, Lead, PriceType, Sale, SaleLine, Stocktake, StocktakeLine, ClientReturn, ClientReturnLine, Refund, StockTransfer, StockTransferLine, Bundle, BundleComponent, Appointment, ChatThread, ChatMessage,
    Feedback, FeedbackSettings, DocumentTemplate, GeneratedDocument, Resource,
    TimeEntry, TimeEntryAudit, WebhookEndpoint, WebhookDelivery,
    ApiKey, PaymentIntent, QboConnection, IntegrationMapping, IntegrationSyncLog,
    Category,
    StaffNotification, PushSubscription,
    ChecklistTemplate, ChecklistTemplateItem, OrderChecklistItem,
    GiftCard, GiftCardLedger, Coupon, LoyaltyLedger,
    NumberSequence, LegalEntity, TaxGroup, TaxRule, TaxException,
    XeroConnection,
    Directory, DirectoryItem,
    AssetGroup, AssetBrand, AssetModel, AssetIdentifierRule,
    MediaBlob,
    Call, Campaign, SsoConnection,
    EmailConnection,
    PriceTypeItem,
    NotificationPreference,
    AutomationRule,
]


class PurchaseReturn(Base):
    """Build 87 — a return-to-supplier document.

    The mirror image of a posting: stock leaves the warehouse and the money owed to
    the supplier is credited back (either to their balance or as a cash refund).
    Orderry keeps these as first-class documents with their own list and filters;
    a return that has been paid out can't be deleted until its payment is removed.
    """
    __tablename__ = "purchase_returns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(40))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("suppliers.id"), index=True)
    settlement: Mapped[str] = mapped_column(String(16), default="balance")  # balance|cash
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"))
    total: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="posted")  # posted|deleted
    note: Mapped[Optional[str]] = mapped_column(Text)
    paid: Mapped[bool] = mapped_column(Boolean, default=False)  # a paid return can't be deleted
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class PurchaseReturnLine(Base):
    __tablename__ = "purchase_return_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    return_id: Mapped[str] = mapped_column(String, ForeignKey("purchase_returns.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    serialized_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("serialized_items.id"))
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit_cost: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)


class WriteOff(Base):
    """Build 88 — a write-off document.

    Previously a write-off was a fire-and-forget stock movement, so it could never be
    corrected. Orderry models it as an editable document whose **deletion restores the
    stock**, with per-line comments that can be private (internal) or public.
    """
    __tablename__ = "write_offs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(40))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="posted")  # posted|reverted
    note: Mapped[Optional[str]] = mapped_column(Text)
    total_cost: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    reverted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class WriteOffLine(Base):
    __tablename__ = "write_off_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    write_off_id: Mapped[str] = mapped_column(String, ForeignKey("write_offs.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    serialized_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("serialized_items.id"))
    bin_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("storage_bins.id"))
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit_cost: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)
    comment: Mapped[Optional[str]] = mapped_column(Text)          # internal by default
    comment_public: Mapped[bool] = mapped_column(Boolean, default=False)


class Posting(Base):
    """Build 93 — a stock posting (goods-in) as an editable document.

    Postings were a fire-and-forget bulk inflow, so a mistyped purchase price was
    permanent and quietly poisoned the moving-average cost. Now the posting is a
    document: it can be corrected while unpaid, deleting it takes the stock back out,
    and the settlement (supplier balance vs cash) is recorded rather than implied.
    """
    __tablename__ = "postings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(40))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("suppliers.id"), index=True)
    settlement: Mapped[str] = mapped_column(String(16), default="balance")  # balance|cash
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"))
    reference: Mapped[Optional[str]] = mapped_column(String(120))   # supplier invoice no.
    total: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="posted")  # posted|deleted
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class PostingLine(Base):
    __tablename__ = "posting_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    posting_id: Mapped[str] = mapped_column(String, ForeignKey("postings.id"), index=True)
    part_id: Mapped[str] = mapped_column(String, ForeignKey("parts.id"))
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit_cost: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)
    markup_pct: Mapped[Optional[float]] = mapped_column(Float)
    price_type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("price_types.id"))
    total: Mapped[Numeric] = mapped_column(Numeric(12, 2), default=0)


class LabelTemplate(Base):
    """Build 95 — a printable label or price tag.

    `fields` is the ordered list of things to show (name, sku, price, barcode…), so one
    template serves a shelf tag and another a small product label without new code.
    """
    __tablename__ = "label_templates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="label")  # label|price_tag
    width_mm: Mapped[float] = mapped_column(Float, default=50)
    height_mm: Mapped[float] = mapped_column(Float, default=30)
    price_type_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("price_types.id"))
    fields: Mapped[Optional[dict]] = mapped_column(
        JSON, default=lambda: ["name", "sku", "price", "barcode"])
    barcode_type: Mapped[str] = mapped_column(String(12), default="code128")  # code128|qr
    # opa: which product field the barcode encodes, and the symbology chosen per field
    barcode_field: Mapped[Optional[str]] = mapped_column(String(12))   # barcode|sku|code|mpn (null = barcode)
    barcode_types: Mapped[Optional[dict]] = mapped_column(JSON)        # {"mpn": "code128", "barcode": "ean13"}
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AutomationSetting(Base):
    """Build 97 — a per-org automation toggle with a small JSON config.

    Keyed by name (`task_on_missed_call`, …) so new automations don't each need a column.
    Kept deliberately simple: a bool and a config blob is enough for the rules we have,
    and anything richer already belongs in the NotificationRule engine.
    """
    __tablename__ = "automation_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class InboxThread(Base):
    """Build 104 — one conversation with one customer, across whatever channel it
    arrived on. The unified inbox exists so the front desk stops tab-hopping between
    WhatsApp, SMS, social DMs and email: every message lands in one stream keyed by
    thread, and a job or invoice can be raised straight from it.
    """
    __tablename__ = "inbox_threads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)   # whatsapp|sms|email|facebook|instagram|call
    external_id: Mapped[Optional[str]] = mapped_column(String(160), index=True)  # provider thread id
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(240))
    handle: Mapped[Optional[str]] = mapped_column(String(160))     # phone / email / social handle
    assigned_to_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|snoozed|closed
    unread: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))  # if converted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class InboxMessage(Base):
    """Build 104 — a single message within a thread."""
    __tablename__ = "inbox_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String, ForeignKey("inbox_threads.id"), index=True)
    direction: Mapped[str] = mapped_column(String(8))              # inbound|outbound
    body: Mapped[Optional[str]] = mapped_column(Text)
    media_url: Mapped[Optional[str]] = mapped_column(String(400))
    author_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))  # outbound sender
    external_id: Mapped[Optional[str]] = mapped_column(String(160))
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class EmailAccount(Base):
    """Build 106 — one of up to N connected sending mailboxes for the org, with a
    per-account allow-list of who may send from it. Multi-account email with sender
    permissions keeps a personal inbox from becoming a shared free-for-all.
    """
    __tablename__ = "email_accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    address: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[Optional[str]] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(16), default="gmail")   # gmail|smtp|outlook
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_employee_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)  # empty = everyone
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EcommerceIntegration(Base):
    """Build 107 — a connected online store. Product and order sync run against this
    record; credentials are referenced, never stored in the clear here.
    """
    __tablename__ = "ecommerce_integrations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    platform: Mapped[str] = mapped_column(String(16))             # shopify|woocommerce|etsy|amazon
    store_url: Mapped[Optional[str]] = mapped_column(String(240))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id"))
    warehouse_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("warehouses.id"))
    credential_ref: Mapped[Optional[str]] = mapped_column(String(120))  # secret manager handle
    sync_products: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EcommerceOrderMap(Base):
    """Build 107 — maps an external store order to a Tekly order, so re-syncing the
    same store order updates rather than duplicates."""
    __tablename__ = "ecommerce_order_maps"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    integration_id: Mapped[str] = mapped_column(String, ForeignKey("ecommerce_integrations.id"), index=True)
    external_order_id: Mapped[str] = mapped_column(String(160), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"))
    external_status: Mapped[Optional[str]] = mapped_column(String(40))
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ApprovalRequest(Base):
    """Build 108 — a pending approval for an action that exceeds a role's authority,
    e.g. a refund above the refund ceiling. Rather than a blunt allow/deny permission,
    a sensitive action can be *requested* and an authorised employee approves or rejects
    it, leaving an auditable record.
    """
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))                 # refund|discount|retro_edit
    amount: Mapped[Optional[Numeric]] = mapped_column(Numeric(12, 2))
    ref_type: Mapped[Optional[str]] = mapped_column(String(40))
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    requested_by_id: Mapped[str] = mapped_column(String, ForeignKey("employees.id"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|approved|rejected
    decided_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ClientLedgerEntry(Base):
    """Build 116 — append-only movements on a contact's balance.

    Every advance (client pays money in), payout (we return money), invoice charge, and
    manual adjustment posts one row here. The contact's ``balance`` is the running sum;
    keeping the individual movements means the Balance tab can show a full audited history
    the way Orderry does, rather than just a single number.
    """
    __tablename__ = "client_ledger_entries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    client_id: Mapped[str] = mapped_column(String, ForeignKey("clients.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(16))   # advance|payout|charge|adjustment
    amount: Mapped[Numeric] = mapped_column(Numeric(12, 2))  # signed: + owed to us, - we owe
    note: Mapped[Optional[str]] = mapped_column(Text)
    ref_type: Mapped[Optional[str]] = mapped_column(String(32))
    ref_id: Mapped[Optional[str]] = mapped_column(String)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class MergeLog(Base):
    """Build 144aj — one record per contact merge, enough to reverse it.

    A merge hard-deletes the source contacts after re-pointing their history at the target.
    To make that recoverable we snapshot each source's full row and record exactly which
    records moved (and from which source) plus the target fields/balance we changed, so an
    undo can recreate the sources with their original ids and put everything back.
    """
    __tablename__ = "merge_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    target_id: Mapped[str] = mapped_column(String, index=True)
    source_snapshots: Mapped[list] = mapped_column(JSON, default=list)   # full column dicts of each deleted source
    moved: Mapped[dict] = mapped_column(JSON, default=dict)              # {model: {record_id: original_source_id}}
    target_before: Mapped[dict] = mapped_column(JSON, default=dict)      # target's filled fields + balance before the merge
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class MessageTemplate(Base):
    """Build 143 — a reusable message snippet the front desk inserts into a reply
    (Phase 4: templates + snippet insertion). Org-scoped, per channel."""
    __tablename__ = "message_templates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(16), default="any")   # any|email|sms|whatsapp
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OrgIntegrationCredential(Base):
    """Per-org (per-shop) provider credentials for the multi-tenant integrations.

    This is what makes the keyed connectors (Stripe/Square/SumUp, Twilio, SMTP,
    Meta, …) truly per-tenant: each shop stores its OWN keys here, encrypted at
    rest, instead of sharing one global set from server env. The effective-creds
    resolver prefers this row and falls back to env only as an operator default.
    One row per (org, provider); the secret fields live inside `data_enc`.
    """
    __tablename__ = "org_integration_credentials"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))     # stripe|square|sumup|twilio|smtp|meta
    data_enc: Mapped[Optional[str]] = mapped_column(Text)  # Fernet-encrypted JSON of the fields
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (UniqueConstraint("org_id", "provider", name="uq_org_integration_cred"),)


class CustomReport(Base):
    """A user-defined report (custom report builder). `config` is a JSON spec —
    {source, columns, filters, group_by, aggregate, sort} — validated server-side
    against a strict whitelist before it ever touches the DB. Org-scoped."""
    __tablename__ = "custom_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CreditNote(Base):
    """opb — a credit note: money the shop owes back to a client, as a numbered, locked document.

    draft → issued (locked, numbered via services/numbering doc_type "credit_note") → canceled.
    Linked to an invoice it lowers that invoice's balance due (Invoice.credited); standalone it adds
    client credit. Either way issuing posts a "credit_note" movement to the contact ledger and
    canceling an issued note posts the reversal. The number is taken on issue, so deleted drafts
    never leave gaps in the sequence."""
    __tablename__ = "credit_notes"
    __table_args__ = (Index("ix_credit_notes_org_created", "org_id", "created_at"),
                      UniqueConstraint("org_id", "number", name="uq_credit_notes_org_number"))
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    number: Mapped[Optional[str]] = mapped_column(String(50))          # set on issue
    client_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("clients.id"), index=True)
    invoice_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("invoices.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="draft")   # draft|issued|canceled
    reason: Mapped[str] = mapped_column(String(500), default="")
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    tax_total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    issued_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    canceled_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    lines: Mapped[list["CreditNoteLine"]] = relationship(
        cascade="all, delete-orphan", order_by="CreditNoteLine.sort_order")


class CreditNoteLine(Base):
    """opb — one credited line: qty × unit price, plus tax at tax_rate percent."""
    __tablename__ = "credit_note_lines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    credit_note_id: Mapped[str] = mapped_column(
        String, ForeignKey("credit_notes.id", ondelete="CASCADE"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500), default="")
    qty: Mapped[float] = mapped_column(Numeric(12, 3, asdecimal=False), default=1.0)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    tax_rate: Mapped[float] = mapped_column(Numeric(7, 3, asdecimal=False), default=0.0)   # percent
    net: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    tax: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    total: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)


class PaymentTerminal(Base):
    """F2/F3 — a card reader the shop charges from (server-driven). provider is stripe|square, or
    simulated (local development only). reader_id is Stripe's tmr_… or Square's device id."""
    __tablename__ = "payment_terminals"
    __table_args__ = (UniqueConstraint("org_id", "provider", "reader_id", name="uq_payment_terminal_reader"),
                      # Review P8: a real (live) reader belongs to one shop. Sandbox test devices are
                      # shared ids by design (e.g. Square's documented ones), so only live rows count.
                      Index("uq_payment_terminal_live_reader", "provider", "reader_id", unique=True,
                            sqlite_where=text("test_mode = 0"), postgresql_where=text("NOT test_mode")))
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    reader_id: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(120))
    location_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("locations.id", ondelete="SET NULL"), index=True)
    provider_location: Mapped[Optional[str]] = mapped_column(String(120))   # Stripe tml_… the reader is registered to
    device_type: Mapped[Optional[str]] = mapped_column(String(60))
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TerminalCharge(Base):
    """One attempt to take money on a reader, for a work order or a counter sale. Settled exactly
    once (webhook or status poll) into the cash system; payment_id links the recorded Payment."""
    __tablename__ = "terminal_charges"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    terminal_id: Mapped[str] = mapped_column(String, ForeignKey("payment_terminals.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[Optional[str]] = mapped_column(String(120), index=True)   # pi_… / Square checkout id
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(120))        # Square payment id (refunds)
    order_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("orders.id"), index=True)
    sale_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("sales.id"), index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="usd")
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending|processing|succeeded|declined|canceled|failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    payment_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("payments.id"), unique=True)
    refunded_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0.0)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PaymentWebhookEvent(Base):
    """Provider webhook events already applied (Stripe evt_…, Square event_id): a redelivery is
    acknowledged without being applied twice."""
    __tablename__ = "payment_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_payment_webhook_event"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(255))
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organisations.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ii144a: registers the before-flush hook that mirrors parts.barcode into product_barcodes.
from backend.services import product_codes as _product_codes  # noqa: E402,F401
