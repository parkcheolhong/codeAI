from datetime import datetime, timedelta
from secrets import token_urlsafe
import base64
import os
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from typing import Optional
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialType,
    AuthenticatorAssertionResponse,
    AuthenticatorAttestationResponse,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    RegistrationCredential,
    AuthenticationCredential,
    UserVerificationRequirement,
    ResidentKeyRequirement,
)

from backend.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from backend.database import get_db
from backend.models import User

router = APIRouter()


def _should_issue_non_expiring_admin_token(user: User) -> bool:
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_superuser", False)
    )


# ---------- 스키마 ----------
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    member_type: str = "individual"
    business_name: Optional[str] = None
    business_registration_number: Optional[str] = None
    representative_name: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool = False
    is_superuser: bool = False
    is_active: bool = True
    full_name: Optional[str] = None
    member_type: str = "individual"
    business_name: Optional[str] = None
    business_registration_number: Optional[str] = None
    representative_name: Optional[str] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class PasskeyRegistrationStartRequest(BaseModel):
    email: EmailStr
    device_label: str | None = None


class PasskeyRegistrationStartResponse(BaseModel):
    registration_token: str
    options: dict
    device_label: str | None = None


class PasskeyRegistrationFinishRequest(BaseModel):
    registration_token: str
    credential: dict


class PasskeyLoginStartRequest(BaseModel):
    email: EmailStr


class PasskeyLoginStartResponse(BaseModel):
    options: dict


class PasskeyLoginFinishRequest(BaseModel):
    email: EmailStr
    credential: dict


_passkey_registration_store: dict[str, dict[str, object]] = {}
_passkey_login_store: dict[str, dict[str, object]] = {}


def _issue_passkey_challenge() -> str:
    return token_urlsafe(24)


def _to_base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=')


def _from_base64url(value: str) -> bytes:
    normalized = value + '=' * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(normalized.encode('utf-8'))


def _build_registration_credential(payload: dict) -> RegistrationCredential:
    response = payload.get("response") or {}
    return RegistrationCredential(
        id=str(payload.get("id") or ""),
        raw_id=_from_base64url(str(payload.get("rawId") or "")),
        response=AuthenticatorAttestationResponse(
            client_data_json=_from_base64url(str(response.get("clientDataJSON") or "")),
            attestation_object=_from_base64url(str(response.get("attestationObject") or "")),
        ),
        type="public-key",
    )


def _build_authentication_credential(payload: dict) -> AuthenticationCredential:
    response = payload.get("response") or {}
    user_handle = response.get("userHandle")
    return AuthenticationCredential(
        id=str(payload.get("id") or ""),
        raw_id=_from_base64url(str(payload.get("rawId") or "")),
        response=AuthenticatorAssertionResponse(
            client_data_json=_from_base64url(str(response.get("clientDataJSON") or "")),
            authenticator_data=_from_base64url(str(response.get("authenticatorData") or "")),
            signature=_from_base64url(str(response.get("signature") or "")),
            user_handle=_from_base64url(str(user_handle)) if user_handle else None,
        ),
        type="public-key",
    )


def _resolve_passkey_rp_id() -> str:
    requested = str(os.getenv("PASSKEY_RP_ID") or os.getenv("APP_DOMAIN") or os.getenv("PUBLIC_DOMAIN") or "").strip().lower()
    if requested:
        return requested
    return "localhost"


def _resolve_passkey_rp_name() -> str:
    return str(os.getenv("PASSKEY_RP_NAME") or "DevAnalysis114 Admin").strip() or "DevAnalysis114 Admin"


def _resolve_request_origin(request: Request | None) -> str:
    configured_rp_id = _resolve_passkey_rp_id()
    fallback_origin = str(os.getenv("PASSKEY_EXPECTED_ORIGIN") or f"https://{configured_rp_id}").strip().rstrip("/")
    if request is None:
        return fallback_origin

    explicit_origin = str(request.headers.get("origin") or "").strip().rstrip("/")
    if explicit_origin:
        return explicit_origin

    forwarded_proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    forwarded_host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return fallback_origin


def _origin_host(origin: str) -> str:
    try:
        return str(urlparse(origin).hostname or "").strip().lower()
    except Exception:
        return ""


def _resolve_passkey_request_context(request: Request | None) -> tuple[str, str]:
    configured_rp_id = _resolve_passkey_rp_id()
    configured_origin = str(os.getenv("PASSKEY_EXPECTED_ORIGIN") or f"https://{configured_rp_id}").strip().rstrip("/")
    request_origin = _resolve_request_origin(request)
    request_host = _origin_host(request_origin)

    if request_host == "localhost":
        return "localhost", request_origin or "https://localhost"

    if request_host and (request_host == configured_rp_id or request_host.endswith(f".{configured_rp_id}")):
        return configured_rp_id, request_origin or configured_origin

    return configured_rp_id, configured_origin


class PasswordRecoveryStartRequest(BaseModel):
    scope: str = "admin"
    user_hint: EmailStr


class PasswordRecoveryStartResponse(BaseModel):
    recovery_session_token: str
    next_action: str
    expires_at: datetime


class PasswordRecoveryVerifyIdentityRequest(BaseModel):
    recovery_session_token: str
    identity_session_token: str
    verification_code: str


class PasswordRecoveryVerifyIdentityResponse(BaseModel):
    verified: bool
    reset_token: str
    expires_at: datetime


class PasswordRecoveryResetRequest(BaseModel):
    scope: str = "admin"
    reset_token: str
    new_password: str


class PasswordRecoveryResetResponse(BaseModel):
    reset: bool
    must_relogin: bool = True


_password_recovery_store: dict[str, dict[str, object]] = {}


def _issue_recovery_token(prefix: str) -> tuple[str, datetime]:
    from secrets import token_urlsafe

    expires_at = datetime.utcnow() + timedelta(minutes=10)
    return f"{prefix}_{token_urlsafe(24)}", expires_at


# ---------- 엔드포인트 ----------
@router.post("/signup", response_model=UserResponse, status_code=201)
def signup(payload: UserCreate, db: Session = Depends(get_db)):
    member_type = str(payload.member_type or "individual").strip().lower()
    if member_type not in {"individual", "sole_proprietor", "corporation"}:
        raise HTTPException(status_code=400, detail="가입 유형은 individual, sole_proprietor, corporation 중 하나여야 합니다")

    if member_type in {"sole_proprietor", "corporation"}:
        if not str(payload.business_name or "").strip():
            raise HTTPException(status_code=400, detail="사업자명 또는 법인명은 필수입니다")
        if not str(payload.business_registration_number or "").strip():
            raise HTTPException(status_code=400, detail="사업자등록번호는 필수입니다")

    if member_type == "corporation" and not str(payload.representative_name or "").strip():
        raise HTTPException(status_code=400, detail="법인 가입은 대표자명이 필수입니다")

    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="이미 사용 중인 이메일입니다")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="이미 사용 중인 사용자명입니다")

    user = User(
        username=payload.username,
        email=payload.email,
        full_name=(payload.full_name or "").strip() or None,
        member_type=member_type,
        business_name=(payload.business_name or "").strip() or None,
        business_registration_number=(payload.business_registration_number or "").strip() or None,
        representative_name=(payload.representative_name or "").strip() or None,
        hashed_password=get_password_hash(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """username 필드에 email 또는 username 모두 허용"""
    user = db.query(User).filter(
        (User.email == form_data.username)
        | (User.username == form_data.username)
    ).first()

    if not user or not verify_password(
        form_data.password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        no_expiry=_should_issue_non_expiring_admin_token(user),
    )
    return {"access_token": token, "token_type": "bearer"}


@router.post("/passkey/register/start", response_model=PasskeyRegistrationStartResponse)
def start_passkey_registration(
    request: Request,
    payload: PasskeyRegistrationStartRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(
        (User.email == payload.email) | (User.username == payload.email)
    ).first()
    if not user or not (getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)):
        raise HTTPException(status_code=404, detail="패스키를 등록할 관리자 계정을 찾을 수 없습니다")

    rp_id, expected_origin = _resolve_passkey_request_context(request)
    registration_token = f"pkreg_{token_urlsafe(24)}"
    user_handle = _to_base64url(str(user.id).encode("utf-8"))
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=_resolve_passkey_rp_name(),
        user_id=str(user.id).encode("utf-8"),
        user_name=str(user.email),
        user_display_name=str(user.email),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    _passkey_registration_store[registration_token] = {
        "user_id": int(user.id),
        "challenge": _to_base64url(options.challenge),
        "device_label": str(payload.device_label or "이 기기 패스키").strip() or "이 기기 패스키",
        "expires_at": datetime.utcnow() + timedelta(minutes=5),
        "user_handle": user_handle,
        "rp_id": rp_id,
        "expected_origin": expected_origin,
    }
    return {
        "registration_token": registration_token,
        "options": __import__('json').loads(options_to_json(options)),
        "device_label": _passkey_registration_store[registration_token]["device_label"],
    }


@router.post("/passkey/register/finish")
def finish_passkey_registration(
    request: Request,
    payload: PasskeyRegistrationFinishRequest,
    db: Session = Depends(get_db),
):
    state = _passkey_registration_store.get(payload.registration_token)
    if not state:
        raise HTTPException(status_code=404, detail="패스키 등록 세션을 찾을 수 없습니다")

    expires_at = state.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.utcnow():
        _passkey_registration_store.pop(payload.registration_token, None)
        raise HTTPException(status_code=410, detail="패스키 등록 세션이 만료되었습니다")

    user = db.query(User).filter(User.id == state.get("user_id")).first()
    if user is None:
        _passkey_registration_store.pop(payload.registration_token, None)
        raise HTTPException(status_code=404, detail="패스키 등록 대상 계정을 찾을 수 없습니다")

    rp_id = str(state.get("rp_id") or _resolve_passkey_request_context(request)[0])
    expected_origin = str(state.get("expected_origin") or _resolve_passkey_request_context(request)[1])
    try:
        verification = verify_registration_response(
            credential=_build_registration_credential(payload.credential),
            expected_challenge=_from_base64url(str(state.get("challenge") or "")),
            expected_rp_id=rp_id,
            expected_origin=expected_origin,
            require_user_verification=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"패스키 등록 검증에 실패했습니다: {exc}")

    user.passkey_enabled = True
    user.passkey_credential_id = _to_base64url(verification.credential_id)
    user.passkey_public_key = _to_base64url(verification.credential_public_key)
    user.passkey_device_label = str(state.get("device_label") or "이 기기 패스키")
    user.passkey_sign_count = int(verification.sign_count)
    user.passkey_registered_at = datetime.utcnow()
    db.add(user)
    db.commit()
    _passkey_registration_store.pop(payload.registration_token, None)
    return {
        "registered": True,
        "device_label": user.passkey_device_label,
    }


@router.post("/passkey/login/start", response_model=PasskeyLoginStartResponse)
def start_passkey_login(
    request: Request,
    payload: PasskeyLoginStartRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(
        (User.email == payload.email) | (User.username == payload.email)
    ).first()
    if user is None or not getattr(user, "passkey_enabled", False) or not getattr(user, "passkey_credential_id", None):
        raise HTTPException(status_code=404, detail="등록된 패스키가 없습니다")

    rp_id, expected_origin = _resolve_passkey_request_context(request)
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=_from_base64url(str(user.passkey_credential_id)),
                type=PublicKeyCredentialType.PUBLIC_KEY,
            )
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _passkey_login_store[str(user.email)] = {
        "challenge": _to_base64url(options.challenge),
        "expires_at": datetime.utcnow() + timedelta(minutes=5),
        "credential_id": str(user.passkey_credential_id),
        "rp_id": rp_id,
        "expected_origin": expected_origin,
    }
    return {
        "options": __import__('json').loads(options_to_json(options)),
    }


@router.post("/passkey/login/finish", response_model=Token)
def finish_passkey_login(
    request: Request,
    payload: PasskeyLoginFinishRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(
        (User.email == payload.email) | (User.username == payload.email)
    ).first()
    if user is None or not getattr(user, "passkey_enabled", False):
        raise HTTPException(status_code=404, detail="패스키 로그인 대상 계정을 찾을 수 없습니다")

    state = _passkey_login_store.get(str(user.email))
    if not state:
        raise HTTPException(status_code=404, detail="패스키 로그인 세션을 찾을 수 없습니다")

    expires_at = state.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.utcnow():
        _passkey_login_store.pop(str(user.email), None)
        raise HTTPException(status_code=410, detail="패스키 로그인 세션이 만료되었습니다")

    rp_id = str(state.get("rp_id") or _resolve_passkey_request_context(request)[0])
    expected_origin = str(state.get("expected_origin") or _resolve_passkey_request_context(request)[1])
    try:
        verification = verify_authentication_response(
            credential=_build_authentication_credential(payload.credential),
            expected_challenge=_from_base64url(str(state.get("challenge") or "")),
            expected_rp_id=rp_id,
            expected_origin=expected_origin,
            credential_public_key=_from_base64url(str(user.passkey_public_key or "")),
            credential_current_sign_count=int(getattr(user, "passkey_sign_count", 0) or 0),
            require_user_verification=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"패스키 로그인 검증에 실패했습니다: {exc}")

    token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        no_expiry=_should_issue_non_expiring_admin_token(user),
    )
    user.passkey_sign_count = int(verification.new_sign_count)
    db.add(user)
    db.commit()
    _passkey_login_store.pop(str(user.email), None)
    return {"access_token": token, "token_type": "bearer"}


@router.put("/extend", response_model=Token)
def extend_access_token(current_user: User = Depends(get_current_user)):
    subject = current_user.email or current_user.username
    token = create_access_token(
        data={"sub": subject},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        no_expiry=_should_issue_non_expiring_admin_token(current_user),
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/recovery/start", response_model=PasswordRecoveryStartResponse)
def start_password_recovery(
    payload: PasswordRecoveryStartRequest,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(
        (User.email == payload.user_hint)
        | (User.username == payload.user_hint)
    ).first()

    if user is None:
        raise HTTPException(status_code=404, detail="일치하는 계정을 찾을 수 없습니다")

    if payload.scope == "admin" and not (getattr(user, "is_admin", False) or getattr(user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="관리자 계정만 이 복구 경로를 사용할 수 있습니다")

    recovery_session_token, expires_at = _issue_recovery_token("recovery")
    _password_recovery_store[recovery_session_token] = {
        "user_id": int(user.id),
        "scope": payload.scope,
        "verified": False,
        "verification_code": "000000",
        "expires_at": expires_at,
    }
    return {
        "recovery_session_token": recovery_session_token,
        "next_action": "identity_verification_required",
        "expires_at": expires_at,
    }


@router.post("/recovery/verify-identity", response_model=PasswordRecoveryVerifyIdentityResponse)
def verify_password_recovery_identity(payload: PasswordRecoveryVerifyIdentityRequest):
    session_state = _password_recovery_store.get(payload.recovery_session_token)
    if not session_state:
        raise HTTPException(status_code=404, detail="복구 세션을 찾을 수 없습니다")

    expires_at = session_state.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.utcnow():
        _password_recovery_store.pop(payload.recovery_session_token, None)
        raise HTTPException(status_code=410, detail="복구 세션이 만료되었습니다")

    expected_code = str(session_state.get("verification_code") or "")
    if payload.verification_code.strip() != expected_code:
        raise HTTPException(status_code=401, detail="본인확인 코드가 올바르지 않습니다")

    identity_session_token = payload.identity_session_token.strip()
    if not identity_session_token:
        raise HTTPException(status_code=400, detail="identity_session_token이 필요합니다")

    reset_token, reset_expires_at = _issue_recovery_token("reset")
    session_state["verified"] = True
    session_state["identity_session_token"] = identity_session_token
    session_state["reset_token"] = reset_token
    session_state["reset_expires_at"] = reset_expires_at
    return {
        "verified": True,
        "reset_token": reset_token,
        "expires_at": reset_expires_at,
    }


@router.post("/recovery/reset-password", response_model=PasswordRecoveryResetResponse)
def reset_password_via_recovery(
    payload: PasswordRecoveryResetRequest,
    db: Session = Depends(get_db),
):
    if len(payload.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 8자 이상이어야 합니다")

    matched_session = None
    for session_token, session_state in _password_recovery_store.items():
        if session_state.get("reset_token") == payload.reset_token and session_state.get("scope") == payload.scope:
            matched_session = (session_token, session_state)
            break

    if not matched_session:
        raise HTTPException(status_code=404, detail="재설정 토큰을 찾을 수 없습니다")

    session_token, session_state = matched_session
    reset_expires_at = session_state.get("reset_expires_at")
    if not isinstance(reset_expires_at, datetime) or reset_expires_at <= datetime.utcnow():
        _password_recovery_store.pop(session_token, None)
        raise HTTPException(status_code=410, detail="재설정 토큰이 만료되었습니다")

    user_id = session_state.get("user_id")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        _password_recovery_store.pop(session_token, None)
        raise HTTPException(status_code=404, detail="대상 계정을 찾을 수 없습니다")

    user.hashed_password = get_password_hash(payload.new_password)
    db.add(user)
    db.commit()
    _password_recovery_store.pop(session_token, None)
    return {
        "reset": True,
        "must_relogin": True,
    }
