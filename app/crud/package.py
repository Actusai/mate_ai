# app/crud/package.py
from sqlalchemy.orm import Session
from app.models.package import Package
from app.schemas.package import PackageCreate, PackageUpdate


def create_package(db: Session, data: PackageCreate) -> Package:
    obj = Package(
        name=data.name,
        description=data.description,
        price=data.price,
        ai_system_limit=data.ai_system_limit,
        user_limit=data.user_limit,
        client_limit=data.client_limit,
        is_ar_only=data.is_ar_only,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_package(db: Session, package_id: int) -> Package | None:
    return db.query(Package).filter(Package.id == package_id).first()


def list_packages(db: Session) -> list[Package]:
    return db.query(Package).order_by(Package.id.asc()).all()


def update_package(db: Session, package: Package, data: PackageUpdate) -> Package:
    # Partial update – mijenja samo ono što je poslano
    for field, value in data.dict(exclude_unset=True).items():
        setattr(package, field, value)

    db.commit()
    db.refresh(package)
    return package


def delete_package(db: Session, package: Package) -> None:
    db.delete(package)
    db.commit()