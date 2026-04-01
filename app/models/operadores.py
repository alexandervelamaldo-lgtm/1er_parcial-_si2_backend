from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Operador(Base):
    __tablename__ = "operadores"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    turno: Mapped[str] = mapped_column(String(60), nullable=False)

    user = relationship("User", back_populates="operador", lazy="selectin")
