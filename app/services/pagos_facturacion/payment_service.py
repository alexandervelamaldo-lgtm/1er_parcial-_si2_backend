from dataclasses import dataclass


@dataclass(slots=True)
class PaymentBreakdown:
    total: float
    commission: float
    workshop_amount: float


def calculate_payment_breakdown(total: float, commission_rate: float = 0.1) -> PaymentBreakdown:
    if total <= 0:
        raise ValueError("El monto total debe ser mayor a cero")
    if not 0 < commission_rate < 1:
        raise ValueError("La comisión debe estar entre 0 y 1")
    commission = round(total * commission_rate, 2)
    workshop_amount = round(total - commission, 2)
    return PaymentBreakdown(total=round(total, 2), commission=commission, workshop_amount=workshop_amount)
