# app/api/v1/packages.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import is_super
from app.models.user import User
from app.models.package import Package
from app.schemas.package import PackageCreate, PackageUpdate, PackageOut
from app.crud.package import (
    create_package as crud_create_package,
    update_package as crud_update_package,
    list_packages as crud_list_packages,
    get_package as crud_get_package,
    delete_package as crud_delete_package,
)

router = APIRouter(tags=["Packages Admin"])


def _to_out(p: Package) -> PackageOut:
    return PackageOut.model_validate(p)


@router.get(
    "/packages",
    response_model=List[PackageOut],
    summary="List packages (admin)",
    operation_id="admin_list_packages",
)
def list_packages_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    is_ar_only: Optional[bool] = Query(None, description="Filter by AR-only flag"),
):
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    rows = crud_list_packages(db, is_ar_only=is_ar_only)
    return [_to_out(r) for r in rows]


@router.get(
    "/packages/{package_id}",
    response_model=PackageOut,
    summary="Get package by id (admin)",
    operation_id="admin_get_package",
)
def get_package_endpoint(
    package_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_get_package(db, package_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Package not found")
    return _to_out(obj)


@router.post(
    "/packages",
    response_model=PackageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create package (admin)",
    operation_id="admin_create_package",
)
def create_package_endpoint(
    payload: PackageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_create_package(db, payload)
    return _to_out(obj)


@router.put(
    "/packages/{package_id}",
    response_model=PackageOut,
    summary="Update package (admin)",
    operation_id="admin_update_package",
)
def update_package_endpoint(
    package_id: int,
    payload: PackageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_get_package(db, package_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Package not found")

    obj = crud_update_package(db, obj, payload)
    return _to_out(obj)


@router.delete(
    "/packages/{package_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete package (admin)",
    operation_id="admin_delete_package",
)
def delete_package_endpoint(
    package_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_get_package(db, package_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Package not found")

    crud_delete_package(db, obj)
    return
