import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee, Location, Organisation, Role
from backend.services import tokens as _tok
from backend.services.audit import record as _audit
from backend.services.currency import symbol_for as _symbol_for  # sign-up sends a code; we store its symbol
from backend.services.auth import (ACCOUNT_OFF, create_access_token, get_current_employee, hash_password,
                                   is_old_default_password, norm_email, verify_password)
from backend.services.notifications import send_direct_email
from backend.services.ratelimit import client_ip as _ip
from backend.services.ratelimit import aenforce as _rate

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Emails sent after the reply, so how long a request takes doesn't show whether the address has
# an account. Kept referenced until done (a bare create_task can be garbage-collected mid-send).
_BACKGROUND: set = set()


def _later(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND.add(t)
    t.add_done_callback(_BACKGROUND.discard)


def _link(path: str) -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}{path}" if base else path


async def _send_verification(db: AsyncSession, employee: "Employee") -> str:
    """Issue an email-verification token and email the link. Returns the raw
    token so tests (and dev, when no SMTP is set) can complete the flow."""
    raw = await _tok.issue(db, employee_id=employee.id, kind="verify")
    await db.commit()
    url = _link(f"/verify-email?token={raw}")
    await send_direct_email(
        employee.email, "Verify your Tekly email",
        f"Welcome to Tekly. Confirm this address to secure your account:\n\n{url}\n\n"
        f"This link expires in 48 hours. If you didn't sign up, ignore this email.")
    return raw


class RegisterRequest(BaseModel):
    org_name: str
    name: str
    email: str
    password: str
    # The signup form's "I agree to the Terms and Privacy policy" box; recorded in the audit log
    # with the version of the terms shown.
    accept_terms: bool = False
    terms_version: Optional[str] = None
    # Build 144cg — Orderry-style onboarding (all optional; sensible defaults applied).
    industry_template: Optional[str] = None
    region: Optional[str] = None          # us | ca | eu | mena | other
    country: Optional[str] = None         # ISO alpha-2
    currency: Optional[str] = None        # symbol
    currency_code: Optional[str] = None   # ISO 4217
    timezone: Optional[str] = None
    document_language: Optional[str] = None
    company_phone: Optional[str] = None
    employee_count: Optional[str] = None


# Build 144cl — seed a sensible default tax so a new shop isn't starting blank.
# EU/Gulf VAT is normally price-inclusive; US/Canada sales tax is added on top.
# Keyed by region, with a country override table for the common exact rates.
REGION_TAX = {
    "us":   {"name": "Sales tax", "rate": 0.0,  "mode": "imposed"},   # varies by state — owner sets it
    "ca":   {"name": "GST",       "rate": 5.0,  "mode": "imposed"},
    "eu":   {"name": "VAT",       "rate": 21.0, "mode": "included"},
    "mena": {"name": "VAT",       "rate": 5.0,  "mode": "included"},
    "other": None,
}
COUNTRY_VAT = {  # ISO alpha-2 → (name, rate, mode)
    "DE": ("VAT", 19.0, "included"), "FR": ("VAT", 20.0, "included"), "ES": ("IVA", 21.0, "included"),
    "IT": ("IVA", 22.0, "included"), "NL": ("BTW", 21.0, "included"), "IE": ("VAT", 23.0, "included"),
    "GB": ("VAT", 20.0, "included"), "AE": ("VAT", 5.0, "included"), "SA": ("VAT", 15.0, "included"),
    "QA": ("VAT", 0.0, "included"), "US": ("Sales tax", 0.0, "imposed"), "CA": ("GST", 5.0, "imposed"),
}


def _default_tax_for(region, country):
    if country and country.upper() in COUNTRY_VAT:
        n, r, m = COUNTRY_VAT[country.upper()]
        return {"name": n, "rate": r, "mode": m}
    return REGION_TAX.get(region or "", None)


# Region → sensible currency / timezone / language defaults for onboarding.
REGION_DEFAULTS = {
    "us":   {"currency": "$", "currency_code": "USD", "timezone": "America/New_York", "language": "en"},
    "ca":   {"currency": "$", "currency_code": "CAD", "timezone": "America/Toronto", "language": "en"},
    "eu":   {"currency": "€", "currency_code": "EUR", "timezone": "Europe/Berlin", "language": "en"},
    "mena": {"currency": "AED", "currency_code": "AED", "timezone": "Asia/Dubai", "language": "en"},
    "other": {"currency": "$", "currency_code": "USD", "timezone": "UTC", "language": "en"},
}


# ISO-3166 country → our region bucket, for pre-selecting the signup dropdown.
# Only a *hint* — the user can change it, and we never store the IP or the country.
_EU_ISO = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
           "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
           "SI", "ES", "SE", "GB", "CH", "NO", "IS"}
_MENA_ISO = {"AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "EG", "IL", "TR",
             "IQ", "MA", "DZ", "TN"}


def _region_hint(country: str | None) -> str | None:
    c = (country or "").strip().upper()
    if not c:
        return None
    if c == "US":
        return "us"
    if c == "CA":
        return "ca"
    if c in _EU_ISO:
        return "eu"
    if c in _MENA_ISO:
        return "mena"
    return "other"


@router.get("/signup-options")
async def signup_options(request: Request):
    """Public: choices the signup form needs — industry templates, region presets,
    and team-size brackets. Unauthenticated so the sign-up screen can load them.

    region_hint is a convenience default derived from the CDN's country header
    (Cloudflare/Vercel set these; no raw IP is read or stored). The user can
    override it; EU and Middle East still default to English per REGION_DEFAULTS."""
    from backend.services import industry as _ind
    templates = [{"key": k, "label": _ind.TEMPLATES[k].get("label", k)} for k in _ind.TEMPLATE_KEYS]
    country = (request.headers.get("cf-ipcountry")
               or request.headers.get("x-vercel-ip-country")
               or request.headers.get("x-country"))
    return {
        "templates": templates,
        "regions": [
            {"key": "us", "label": "United States"},
            {"key": "ca", "label": "Canada"},
            {"key": "eu", "label": "Europe"},
            {"key": "mena", "label": "Middle East"},
            {"key": "other", "label": "Other"},
        ],
        "region_defaults": REGION_DEFAULTS,
        "region_hint": _region_hint(country),
        "employee_brackets": ["1", "2-5", "6-20", "21-50", "50+"],
    }


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str


class TokenOnly(BaseModel):
    token: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee_id: str
    org_id: str
    name: str
    role: str
    # Build 144cd: set when the org mandates 2FA but this user hasn't enrolled yet.
    # The frontend uses it to force enrollment before the app becomes usable.
    mfa_enrollment_required: bool = False

@router.post("/register", status_code=201)
async def register(data: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await _rate("register", _ip(request), limit=10, window_sec=3600)
    data.email = norm_email(data.email)
    if "@" not in data.email:
        raise HTTPException(400, "Enter a valid email address")
    # Same rule as reset, invites and staff accounts (signup used to accept a 1-character password).
    if not data.password or len(data.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if is_old_default_password(data.password):
        raise HTTPException(400, "Choose a different password")
    existing = await db.execute(select(Employee).where(func.lower(Employee.email) == data.email))
    if existing.scalar_one_or_none():
        # Don't confirm that the address has an account: pay the same hashing cost as a real
        # sign-up and tell the address's owner instead (they may simply have forgotten).
        await asyncio.to_thread(hash_password, data.password)
        _later(send_direct_email(
            data.email, "Your Tekly account",
            "Someone tried to create a new Tekly account with this email address. You already have "
            f"one: sign in at {_link('/login')} or reset your password at {_link('/reset-password')}.\n\n"
            "If it wasn't you, you can ignore this email."))
        raise HTTPException(400, "We couldn't create an account with these details. If you already "
                                 "have one, sign in or reset your password.")

    import secrets as _sec
    from datetime import date as _date
    org = Organisation(name=data.org_name, webhook_secret=_sec.token_urlsafe(24),
                       sub_status="trial", sub_start=_date.today())   # build 144ce
    # Build 144cg — apply onboarding choices with region-based defaults.
    if data.region:
        org.region = data.region
        defaults = REGION_DEFAULTS.get(data.region, {})
        org.currency_code = data.currency_code or defaults.get("currency_code") or org.currency_code
        # Prefer an explicit real symbol; otherwise derive it from the code (build 144ch)
        # so the UI never shows a bare code like "GBP" where "£" belongs.
        org.currency = _symbol_for(org.currency_code, data.currency or defaults.get("currency") or org.currency)
        org.timezone = data.timezone or defaults.get("timezone") or org.timezone
        org.document_language = data.document_language or defaults.get("language") or org.document_language
    else:
        if data.currency_code:
            org.currency_code = data.currency_code
            org.currency = _symbol_for(data.currency_code, data.currency or org.currency)
        elif data.currency:
            org.currency = data.currency
        if data.timezone:
            org.timezone = data.timezone
        if data.document_language:
            org.document_language = data.document_language
    if data.country:
        org.country = data.country
    if data.company_phone:
        org.company_phone = data.company_phone
    if data.employee_count:
        org.employee_count = data.employee_count
    if data.industry_template:
        from backend.services import industry as _ind
        org.industry_template = _ind.normalize_template_key(data.industry_template)
    db.add(org)
    await db.flush()

    location = Location(org_id=org.id, name="Main location")
    db.add(location)
    await db.flush()

    # Build 84: every location owns one default product warehouse. It's what makes the
    # location_id → warehouse compatibility shim resolve, so it must exist from day one.
    from backend.models import Warehouse
    from backend.services.warehouses import ACTIONS as _WH_ACTIONS
    db.add(Warehouse(
        org_id=org.id, name=f"{location.name} warehouse", kind="product",
        scope="location", location_id=location.id, is_default=True,
        allowed_actions=list(_WH_ACTIONS)))
    await db.flush()

    from backend.services.permissions import full_access_permissions
    admin_role = Role(org_id=org.id, name="admin", permissions=full_access_permissions(), perms_explicit=True)
    db.add(admin_role)
    await db.flush()

    # Build 144cl: seed a region-appropriate default tax rate (owner can edit/disable it).
    _tax = _default_tax_for(data.region, data.country)
    if _tax is not None:
        from backend.models import Tax
        db.add(Tax(org_id=org.id, name=_tax["name"], rate=_tax["rate"], mode=_tax["mode"],
                   is_default=True, active=True))
        await db.flush()

    employee = Employee(
        org_id=org.id,
        location_id=location.id,
        role_id=admin_role.id,
        name=data.name,
        email=data.email,
        password_hash=await asyncio.to_thread(hash_password, data.password),
    )
    db.add(employee)
    await db.flush()
    org.owner_id = employee.id   # the shop's creator: only they can change their own account
    if data.accept_terms:
        await _audit(db, org_id=org.id, actor_id=employee.id, action="auth.terms_accepted",
                     entity_type="employee", entity_id=employee.id,
                     diff={"terms_version": (data.terms_version or "")[:40]})

    # Build 144ah: give the new org a starter set of client-facing order notifications
    # (received / status changed / completed) so customers hear something out of the box.
    from backend.services.notification_rules import seed_default_rules
    await seed_default_rules(db, org.id)

    await db.commit()

    # Launch QA B2: seed every status pipeline now, before the app's parallel first-load
    # requests race to do it lazily.
    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, org.id)

    # Build 144cb: send an email-verification link (best-effort; never blocks signup).
    await _send_verification(db, employee)

    token = create_access_token({"sub": employee.id, "org_id": org.id, "tv": employee.token_version or 0})
    return TokenResponse(
        access_token=token,
        employee_id=employee.id,
        org_id=org.id,
        name=employee.name,
        role="admin",
    )

_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


@router.post("/token", response_model=TokenResponse)
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # Build 144bz: throttle brute-force — 10 attempts / 5 min per IP+username, plus a per-account
    # ceiling that no choice of source IP can reset (distributed guessing).
    _user = (form.username or "").strip().lower()
    await _rate("login", f"{_ip(request)}:{_user}", limit=10, window_sec=300)
    await _rate("login_acct", _user, limit=30, window_sec=900)
    result = await db.execute(select(Employee).where(func.lower(Employee.email) == _user))
    employee = result.scalar_one_or_none()
    # Unknown emails pay the same bcrypt cost as wrong passwords, so response time doesn't
    # reveal which addresses have accounts.
    if not employee:
        await asyncio.to_thread(verify_password, form.password, _DUMMY_HASH)
    if not employee or not await asyncio.to_thread(verify_password, form.password, employee.password_hash):
        if employee:   # unknown emails have no org to file the event under
            await _login_failed(db, employee, "password")
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not employee.active:
        await _login_failed(db, employee, "disabled")
        raise HTTPException(status_code=403, detail=ACCOUNT_OFF)
    # H1: an account still on the old default password (flagged by migration dp144q1, or simply
    # signing in with it) gets no session: the password is public, so whoever typed it may not be
    # the owner. A reset link goes to the account's own email instead.
    if employee.must_reset_password or is_old_default_password(form.password):
        if not employee.must_reset_password:
            employee.must_reset_password = True
            employee.token_version = (employee.token_version or 0) + 1
        await _email_reset_link(db, employee, quiet_limit=True)
        await db.commit()
        raise HTTPException(status_code=403, detail=(
            "For your security this password has to be changed. We've emailed you a link to set "
            "a new one."), headers={"X-Password-Reset-Required": "1"})

    # Build 144ce: account-level lifecycle gate. A suspended org or one past its
    # term end date can't sign in — the platform admin controls these dates.
    from datetime import date as _date
    _org = (await db.execute(select(Organisation).where(
        Organisation.id == employee.org_id))).scalar_one_or_none()
    if _org:
        if _org.sub_status == "suspended":
            raise HTTPException(status_code=403, detail=ACCOUNT_OFF)
        if _org.sub_end and _org.sub_end < _date.today():
            raise HTTPException(status_code=403, detail="Your subscription has ended. Please contact support to renew.")
    # Build 144ce: per-employee access window (set from the /admin portal).
    _today = _date.today()
    if (employee.access_start and employee.access_start > _today) or \
       (employee.access_end and employee.access_end < _today):
        raise HTTPException(status_code=403, detail="Your access is outside its allowed dates.")

    # B1: MFA gate — when enabled, a valid TOTP or recovery code is required.
    if employee.mfa_enabled and employee.mfa_secret:
        from backend.services.mfa import consume_recovery_code
        from backend.services.mfa import verify_step as _mfa_step
        code = (getattr(form, "client_secret", None) or "").strip()  # OAuth2 form carries it
        from backend.services.crypto import decrypt as _decrypt
        step = _mfa_step(_decrypt(employee.mfa_secret), code) if code else None
        # Each TOTP code works once: refuse a step at or before the last one accepted.
        if step is not None and employee.mfa_last_step is not None and step <= employee.mfa_last_step:
            step = None
        if step is not None:
            # Record the step with a conditional UPDATE: two sign-ins racing with the same
            # intercepted code can't both pass the check above.
            from sqlalchemy import or_ as _or
            from sqlalchemy import update as _update
            res = await db.execute(_update(Employee).where(
                Employee.id == employee.id,
                _or(Employee.mfa_last_step.is_(None), Employee.mfa_last_step < step),
            ).values(mfa_last_step=step).execution_options(synchronize_session=False))
            if res.rowcount != 1:
                step = None
        ok = step is not None
        if ok:
            employee.mfa_last_step = step
            await db.commit()
        if not ok and code:
            used, remaining = await asyncio.to_thread(consume_recovery_code, employee.mfa_recovery_codes, code)
            if used:
                employee.mfa_recovery_codes = remaining
                await db.commit()
                ok = True
        if not ok:
            if code:   # a wrong second factor after a correct password: cap guesses per account
                await _login_failed(db, employee, "mfa_code")
                await _rate("mfa_fail", employee.id, limit=5, window_sec=900)
            raise HTTPException(status_code=401, detail="MFA code required or invalid",
                                headers={"X-MFA-Required": "1"})

    role_result = await db.execute(select(Role).where(Role.id == employee.role_id))
    role = role_result.scalar_one_or_none()

    # Build 144cd: if the org mandates 2FA and this user hasn't enrolled, flag it so
    # the app forces enrollment. (Enrolled users already passed the code gate above.)
    org = (await db.execute(select(Organisation).where(
        Organisation.id == employee.org_id))).scalar_one_or_none()
    enroll_needed = bool(org and org.require_mfa) and not employee.mfa_enabled

    await _audit(db, org_id=employee.org_id, actor_id=employee.id, action="auth.login",
                 entity_type="employee", entity_id=employee.id,
                 diff={"ip": _ip(request), "mfa": bool(employee.mfa_enabled)})
    await db.commit()

    token = create_access_token({"sub": employee.id, "org_id": employee.org_id, "tv": employee.token_version or 0})
    return TokenResponse(
        access_token=token,
        employee_id=employee.id,
        org_id=employee.org_id,
        name=employee.name,
        role=role.name if role else "staff",
        mfa_enrollment_required=enroll_needed,
    )

async def _login_failed(db: AsyncSession, employee: Employee, reason: str) -> None:
    await _audit(db, org_id=employee.org_id, actor_id=employee.id, action="auth.login_failed",
                 entity_type="employee", entity_id=employee.id, diff={"reason": reason})
    await db.commit()


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Sign out of this device: the session id in the token goes on the revocation list, so
    this token, and any it was refreshed into, stops working at once. Other devices stay
    signed in (that's /logout-all-devices)."""
    import jwt as _jwt
    from datetime import datetime as _dt
    from backend.models import RevokedSession
    presented = (request.headers.get("authorization") or "").split(" ", 1)[-1]
    claims = _jwt.decode(presented, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if claims.get("sid"):
        # Kept until the whole sign-in would have expired (refresh can slide that far).
        ends = int(claims.get("auth_time") or claims.get("exp") or 0) + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
        if not await db.get(RevokedSession, claims["sid"]):
            db.add(RevokedSession(sid=claims["sid"], employee_id=current.id, expires_at=_dt.utcfromtimestamp(ends)))
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="auth.logout",
                 entity_type="employee", entity_id=current.id)
    await db.commit()


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Exchange a still-valid token for a fresh one (sliding session for the
    PWA, which keeps a long-lived session on-device and refreshes on launch).
    The original sign-in time travels with it, capping the whole session."""
    import jwt as _jwt
    from backend.services.auth import session_expired
    presented = (request.headers.get("authorization") or "").split(" ", 1)[-1]
    claims = _jwt.decode(presented, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    auth_time = claims.get("auth_time")
    if session_expired(auth_time):
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    data = {"sub": current.id, "org_id": current.org_id, "tv": current.token_version or 0}
    if auth_time is not None:
        data["auth_time"] = int(auth_time)
    if claims.get("sid"):
        data["sid"] = claims["sid"]      # same sign-in: logout must reach the refreshed token too
    token = create_access_token(data)
    return TokenResponse(
        access_token=token, employee_id=current.id, org_id=current.org_id,
        name=current.name, role=role.name if role else "staff",
    )


@router.post("/logout-all-devices")
async def logout_all_devices(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Lost-device / panic logout: invalidate every token issued to this
    employee (including the caller's) by bumping their token version. The
    employee must sign in again everywhere."""
    current.token_version = (current.token_version or 0) + 1
    await db.commit()
    return {"ok": True, "token_version": current.token_version}


# ── Build 144cb: password reset (self-service, logged out) ───────────────────
@router.post("/password/forgot")
async def password_forgot(data: ForgotRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Start a reset. Always returns 200 with the same message whether or not the
    email exists — never leak which addresses are registered. Rate-limited."""
    await _rate("forgot", f"{_ip(request)}:{(data.email or '').lower()}", limit=5, window_sec=900)
    await _rate("forgot_acct", (data.email or "").strip().lower(), limit=10, window_sec=3600)
    emp = (await db.execute(select(Employee).where(
        func.lower(Employee.email) == norm_email(data.email)))).scalar_one_or_none()
    if emp and emp.active:
        await _audit(db, org_id=emp.org_id, actor_id=emp.id, action="auth.password_reset_requested",
                     entity_type="employee", entity_id=emp.id)
        await _email_reset_link(db, emp, after_reply=True)
    return {"ok": True, "message": "If that email is registered, a reset link is on its way."}


async def _email_reset_link(db, emp, *, quiet_limit: bool = False, after_reply: bool = False) -> None:
    """Issue a fresh one-hour reset token and email it. With quiet_limit (the sign-in path),
    at most a few emails per hour per account are sent and going over is silent, so repeated
    sign-in attempts can't be used to flood someone's inbox. after_reply sends once the response
    is out, so forgot-password takes as long for unknown addresses as for real ones."""
    if quiet_limit:
        try:
            await _rate("reset_mail", emp.id, limit=3, window_sec=3600)
        except HTTPException:
            return
    await _tok.invalidate_all(db, employee_id=emp.id, kind="reset")
    raw = await _tok.issue(db, employee_id=emp.id, kind="reset")
    await db.commit()
    url = _link(f"/reset-password?token={raw}")
    mail = send_direct_email(
        emp.email, "Reset your Tekly password",
        f"Someone asked to reset your password. If it was you, open this link:\n\n{url}\n\n"
        f"It expires in 1 hour and can be used once. If it wasn't you, ignore this email.")
    if after_reply:
        _later(mail)
    else:
        await mail


@router.post("/password/reset")
async def password_reset(data: ResetRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Complete a reset: consume the token, set the new password, and revoke all
    existing sessions so a stolen token can't leave a live session behind."""
    await _rate("reset", _ip(request), limit=10, window_sec=900)
    if not data.new_password or len(data.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if is_old_default_password(data.new_password):
        raise HTTPException(400, "Choose a different password")
    emp = await _tok.redeem(db, raw=data.token, kind="reset")
    if not emp:
        raise HTTPException(400, "This reset link is invalid or has expired")
    emp.password_hash = await asyncio.to_thread(hash_password, data.new_password)
    emp.token_version = (emp.token_version or 0) + 1   # log out everywhere
    emp.email_verified = True                           # proving email access verifies it
    emp.must_reset_password = False
    await _audit(db, org_id=emp.org_id, actor_id=emp.id, action="auth.password_reset",
                 entity_type="employee", entity_id=emp.id)
    await db.commit()
    return {"ok": True}


# ── Build 144cb: email verification ──────────────────────────────────────────
@router.post("/email/verify")
async def email_verify(data: TokenOnly, request: Request, db: AsyncSession = Depends(get_db)):
    await _rate("verify", _ip(request), limit=20, window_sec=900)
    emp = await _tok.redeem(db, raw=data.token, kind="verify")
    if not emp:
        raise HTTPException(400, "This verification link is invalid or has expired")
    emp.email_verified = True
    await db.commit()
    return {"ok": True}


@router.post("/email/resend")
async def email_resend(current: Employee = Depends(get_current_employee),
                       db: AsyncSession = Depends(get_db)):
    """Re-send the verification email to the logged-in user's address."""
    await _rate("verify_resend", current.id, limit=3, window_sec=3600)
    if current.email_verified:
        return {"ok": True, "already_verified": True}
    await _tok.invalidate_all(db, employee_id=current.id, kind="verify")
    await _send_verification(db, current)
    return {"ok": True}


# ── Build 144cc: teammate invitation accept (logged out) ─────────────────────
class InvitePreview(BaseModel):
    token: str


class InviteAccept(BaseModel):
    token: str
    name: str
    password: str


def _invite_by_token(raw: str):
    import hashlib
    return hashlib.sha256((raw or "").encode()).hexdigest()


@router.post("/invite/preview")
async def invite_preview(data: InvitePreview, request: Request, db: AsyncSession = Depends(get_db)):
    """Show the invitee who invited them / which email, so the accept screen can
    pre-fill. Returns valid=False for unknown, expired, or already-accepted links."""
    await _rate("invite", _ip(request), limit=30, window_sec=900)
    from backend.models import Invite, Organisation
    inv = (await db.execute(select(Invite).where(
        Invite.token_hash == _invite_by_token(data.token)))).scalar_one_or_none()
    from datetime import datetime as _dt
    if not inv or inv.accepted_at is not None or inv.expires_at < _dt.utcnow():
        return {"valid": False}
    org = (await db.execute(select(Organisation).where(Organisation.id == inv.org_id))).scalar_one_or_none()
    return {"valid": True, "email": inv.email, "name": inv.name,
            "org_name": org.name if org else None}


@router.post("/invite/accept", response_model=TokenResponse)
async def invite_accept(data: InviteAccept, request: Request, db: AsyncSession = Depends(get_db)):
    """Redeem an invite: create the Employee with the chosen password and return a
    session token so they land straight in the app (email already proven verified)."""
    await _rate("invite", _ip(request), limit=30, window_sec=900)
    from datetime import datetime as _dt

    from backend.models import Invite
    if is_old_default_password(data.password):
        raise HTTPException(400, "Choose a different password")
    if not data.password or len(data.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    inv = (await db.execute(select(Invite).where(
        Invite.token_hash == _invite_by_token(data.token)))).scalar_one_or_none()
    if not inv or inv.accepted_at is not None or inv.expires_at < _dt.utcnow():
        raise HTTPException(400, "This invitation is invalid or has expired")
    # Guard against a race where the email got an account after the invite was sent. Emails are
    # unique across all shops, so an account anywhere blocks it (this used to 500 on the insert).
    if (await db.execute(select(Employee).where(
            func.lower(Employee.email) == norm_email(inv.email)))).scalar_one_or_none():
        raise HTTPException(409, "An account with this email already exists. Sign in with it, "
                                 "or ask for an invite to a different email address.")
    emp = Employee(
        org_id=inv.org_id, location_id=inv.location_id, role_id=inv.role_id,
        name=(data.name or inv.name or inv.email.split("@")[0]),
        email=norm_email(inv.email), password_hash=await asyncio.to_thread(hash_password, data.password),
        email_verified=True)
    db.add(emp)
    inv.accepted_at = _dt.utcnow()
    await db.commit()
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one_or_none()
    token = create_access_token({"sub": emp.id, "org_id": emp.org_id, "tv": 0})
    return TokenResponse(access_token=token, employee_id=emp.id, org_id=emp.org_id,
                         name=emp.name, role=role.name if role else "staff")


@router.get("/me")
async def me(current: Employee = Depends(get_current_employee),
             db: AsyncSession = Depends(get_db)):
    # Build 125: surface the org's base currency so the whole UI can format money in it
    # (fmt.currency) instead of a hard-coded symbol.
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one_or_none()
    # Surface the employee's effective role permissions so the UI can hide the
    # controls their role denies. Same deny-wins rule as require_perm: a code is
    # allowed unless the role sets it explicitly to False. No role => allow all.
    role_name, permissions, home_route = None, {}, None
    if current.role_id:
        role = (await db.execute(select(Role).where(
            Role.id == current.role_id))).scalar_one_or_none()
        if role:
            role_name = role.name
            from backend.services.permissions import effective_permissions
            permissions = effective_permissions(role)
            home_route = getattr(role, "home_route", None) or None
    return {
        "id": current.id,
        "name": current.name,
        "email": current.email,
        "org_id": current.org_id,
        "org_name": org.name if org else None,   # the shop's name under the logo / on job stickers
        "location_id": current.location_id,
        "active": current.active,
        "currency": (org.currency if org else None) or _symbol_for(org.currency_code if org else None, ""),
        "currency_code": (org.currency_code if org else None) or "",
        "role": role_name,
        "is_owner": current.role_id is None,
        "home_route": home_route,
        "permissions": permissions,
        # build 144cb: account-security surface for the UI (verify banner, 2FA badge)
        "email_verified": bool(current.email_verified),
        "mfa_enabled": bool(current.mfa_enabled),
        # build 144cd: org 2FA policy — the app forces enrollment when required and not yet set up
        "mfa_required": bool(org.require_mfa) if org else False,
        # build 144j: which time clocks this employee sees in the widget
        "track_job_hours": current.track_job_hours,
        "track_attendance": current.track_attendance,
        # build 144x: org master switches — effective clock = org ON *and* employee tracks it
        "clock_wo_enabled": (org.clock_wo_enabled if org and org.clock_wo_enabled is not None else True),
        "clock_attendance_enabled": (org.clock_attendance_enabled if org and org.clock_attendance_enabled is not None else True),
    }
