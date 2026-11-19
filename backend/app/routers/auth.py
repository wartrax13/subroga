"""Rotas /api/auth/* e /api/org/users (equipe)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from app import auth
from app.models import ROLES, Organization, User
from app.routers.deps import Ctx, current_user, get_ctx, get_session, org_out, require_admin, user_out

router = APIRouter(prefix="/api")


class SignupIn(BaseModel):
    org_name: str
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class UserIn(BaseModel):
    name: str
    email: str
    password: str
    role: str = "advogado"


def _clean_email(email: str) -> str:
    email = email.strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain or " " in email:
        raise HTTPException(422, "Email inválido.")
    return email


def _check_new_user(s: Session, name: str, email: str, password: str) -> str:
    if not name.strip():
        raise HTTPException(422, "Informe o nome.")
    email = _clean_email(email)
    problem = auth.password_problem(password)
    if problem:
        raise HTTPException(422, problem)
    if s.exec(select(User).where(User.email == email)).first() is not None:
        raise HTTPException(409, "Este email já está cadastrado.")
    return email


def _session_out(user: User, org: Organization, ctx: Ctx) -> dict[str, Any]:
    return {"access_token": auth.create_token(user.id, org.id, ctx.settings.jwt_secret),
            "token_type": "bearer", "user": user_out(user), "org": org_out(org)}


@router.post("/auth/signup", status_code=201)
def signup(body: SignupIn, s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    if not body.org_name.strip():
        raise HTTPException(422, "Informe o nome do escritório.")
    email = _check_new_user(s, body.name, body.email, body.password)
    org = Organization(name=body.org_name.strip())
    user = User(org_id=org.id, name=body.name.strip(), email=email,
                password_hash=auth.hash_password(body.password), role="admin")
    s.add(org)
    s.add(user)
    s.commit()
    s.refresh(org)
    s.refresh(user)
    return _session_out(user, org, ctx)


@router.post("/auth/login")
def login(body: LoginIn, s: Session = Depends(get_session), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    user = s.exec(select(User).where(User.email == body.email.strip().lower())).first()
    if not auth.verify_password(body.password, user.password_hash if user else None) or user is None:
        raise HTTPException(401, "Email ou senha incorretos.")
    org = s.get(Organization, user.org_id)
    assert org is not None
    return _session_out(user, org, ctx)


@router.get("/auth/me")
def me(user: User = Depends(current_user), s: Session = Depends(get_session)) -> dict[str, Any]:
    org = s.get(Organization, user.org_id)
    assert org is not None
    return {"user": user_out(user), "org": org_out(org)}


# ---------------------------------------------------------------- equipe (admin)

@router.get("/org/users")
def list_users(admin: User = Depends(require_admin), s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    users = s.exec(select(User).where(User.org_id == admin.org_id).order_by(User.created_at)).all()
    return [user_out(u) for u in users]


@router.post("/org/users", status_code=201)
def add_user(body: UserIn, admin: User = Depends(require_admin), s: Session = Depends(get_session)) -> dict[str, Any]:
    if body.role not in ROLES:
        raise HTTPException(422, "Papel inválido: use \"admin\" ou \"advogado\".")
    email = _check_new_user(s, body.name, body.email, body.password)
    user = User(org_id=admin.org_id, name=body.name.strip(), email=email,
                password_hash=auth.hash_password(body.password), role=body.role)
    s.add(user)
    s.commit()
    s.refresh(user)
    return user_out(user)


@router.delete("/org/users/{user_id}", status_code=204)
def delete_user(user_id: str, admin: User = Depends(require_admin), s: Session = Depends(get_session)) -> Response:
    user = s.get(User, user_id)
    if user is None or user.org_id != admin.org_id:
        raise HTTPException(404, "Usuário não encontrado.")
    if user.id == admin.id:
        raise HTTPException(409, "Você não pode remover a si mesmo.")
    s.delete(user)
    s.commit()
    return Response(status_code=204)
